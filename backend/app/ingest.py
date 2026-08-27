"""Background ingest: pull upstream data, roll it up, grade the model, fire alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from . import db, indicators, money, policy, scoring
from .config import settings
from .hub import hub
from .wiki import LOOKBACK_FOR_TIMESTEP, STEP_SECONDS, client

log = logging.getLogger("flipforge.ingest")

# A window whose two sides disagree by more than this factor is discarded when
# computing midpoints, price changes and volatility. Three times is deliberately
# permissive: real illiquid spreads run 10-30%, not 1000%.
SPREAD_GUARD = 3.0

# Horizons the score validation harness grades against. asyncpg maps an
# interval parameter from timedelta, not from a string.
HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}


def _ts(epoch: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch is not None else None


def _int(value: Any) -> int:
    return int(value) if value is not None else 0


def _float(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


# The tax rule is mirrored into SQL so per-candle aggregates (average margin,
# margin variability, realised outcomes) can be computed in the database instead
# of shipping every candle to Python. Generated from the same settings as
# money.sale_tax so the two cannot drift.
TAX_FN = """
CREATE OR REPLACE FUNCTION ff_tax(price NUMERIC, exempt BOOLEAN)
RETURNS NUMERIC AS $$
  SELECT CASE
    WHEN price IS NULL OR exempt OR price <= 0 THEN 0
    ELSE LEAST(FLOOR(price * {rate}), {cap}::NUMERIC)
  END;
$$ LANGUAGE SQL IMMUTABLE;
"""


async def install_sql_helpers() -> None:
    await db.execute(TAX_FN.format(rate=settings.ge_tax_rate, cap=settings.ge_tax_cap))


# ------------------------------------------------------------------ mapping --

async def refresh_mapping() -> int:
    rows = await client.mapping()
    payload = [
        (
            int(r["id"]), r["name"], r.get("examine"), bool(r.get("members", False)),
            r.get("value"), r.get("lowalch"), r.get("highalch"), r.get("limit"), r.get("icon"),
        )
        for r in rows
        if r.get("id") is not None and r.get("name")
    ]
    await db.executemany(
        """INSERT INTO items (id, name, examine, members, value, lowalch, highalch,
                              buy_limit, icon, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())
           ON CONFLICT (id) DO UPDATE SET
             name=EXCLUDED.name, examine=EXCLUDED.examine, members=EXCLUDED.members,
             value=EXCLUDED.value, lowalch=EXCLUDED.lowalch, highalch=EXCLUDED.highalch,
             buy_limit=EXCLUDED.buy_limit, icon=EXCLUDED.icon, updated_at=now()""",
        payload,
    )
    log.info("mapping refreshed: %s items", len(payload))
    return len(payload)


# ------------------------------------------------------------------- latest --

async def poll_latest() -> int:
    data = await client.latest()
    rows = []
    for raw_id, q in data.items():
        try:
            item_id = int(raw_id)
        except ValueError:
            continue
        rows.append((item_id, q.get("high"), _ts(q.get("highTime")), q.get("low"), _ts(q.get("lowTime"))))
    await db.executemany(
        """INSERT INTO latest (item_id, high, high_time, low, low_time, fetched_at)
           SELECT $1,$2,$3,$4,$5, now() WHERE EXISTS (SELECT 1 FROM items WHERE id = $1)
           ON CONFLICT (item_id) DO UPDATE SET
             high=EXCLUDED.high, high_time=EXCLUDED.high_time,
             low=EXCLUDED.low, low_time=EXCLUDED.low_time, fetched_at=now()""",
        rows,
    )
    await db.set_meta("last_latest_poll", str(int(time.time())))
    return len(rows)


# ------------------------------------------------------------------ candles --

def _floor_now(timestep: str) -> int:
    step = STEP_SECONDS[timestep]
    return (int(time.time()) // step) * step - step


async def _upsert_candles(rows: list[tuple]) -> None:
    await db.executemany(
        """INSERT INTO candles (item_id, timestep, ts, avg_high, avg_low, high_vol, low_vol)
           SELECT $1,$2,$3,$4,$5,$6,$7 WHERE EXISTS (SELECT 1 FROM items WHERE id = $1)
           ON CONFLICT (item_id, timestep, ts) DO UPDATE SET
             avg_high=EXCLUDED.avg_high, avg_low=EXCLUDED.avg_low,
             high_vol=EXCLUDED.high_vol, low_vol=EXCLUDED.low_vol""",
        rows,
    )


def _as_numeric(value: Any) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None


async def store_bulk(timestep: str, timestamp: Optional[int] = None) -> int:
    data = await client.bulk(timestep, timestamp)
    if not data:
        return 0
    bucket = timestamp if timestamp is not None else _floor_now(timestep)
    ts = _ts(bucket)
    rows = []
    for raw_id, q in data.items():
        try:
            item_id = int(raw_id)
        except ValueError:
            continue
        if q.get("avgHighPrice") is None and q.get("avgLowPrice") is None:
            continue
        rows.append((
            item_id, timestep, ts,
            _as_numeric(q.get("avgHighPrice")), _as_numeric(q.get("avgLowPrice")),
            q.get("highPriceVolume") or 0, q.get("lowPriceVolume") or 0,
        ))
    await _upsert_candles(rows)
    return len(rows)


async def fetch_item_series(item_id: int, timestep: str) -> int:
    """Lazy deep history for one item, cached into candles."""
    lookback = LOOKBACK_FOR_TIMESTEP[timestep]
    points = await client.timeseries(item_id, lookback)
    rows = [
        (
            item_id, timestep, _ts(p["timestamp"]),
            _as_numeric(p.get("avgHighPrice")), _as_numeric(p.get("avgLowPrice")),
            p.get("highPriceVolume") or 0, p.get("lowPriceVolume") or 0,
        )
        for p in points
        if p.get("timestamp") is not None
    ]
    await _upsert_candles(rows)
    return len(rows)


# ----------------------------------------------------------------- backfill --

async def backfill() -> None:
    """Walk the bulk endpoints backwards to build history for every item at once.

    One request covers all ~4000 items for one interval, so two weeks of hourly
    history for the entire game costs 336 requests, once, ever.
    """
    if await db.get_meta("backfill_done") == "1":
        log.info("backfill already complete, skipping")
        return

    gap = 1.0 / max(settings.backfill_rate_per_sec, 0.5)
    for timestep, steps in (("1h", settings.backfill_1h_steps), ("5m", settings.backfill_5m_steps)):
        step_seconds = STEP_SECONDS[timestep]
        now_bucket = _floor_now(timestep)
        stored = 0
        for i in range(1, steps + 1):
            try:
                stored += await store_bulk(timestep, now_bucket - i * step_seconds)
            except Exception as exc:  # noqa: BLE001 - never let backfill kill startup
                log.warning("backfill %s step %s failed: %s", timestep, i, exc)
            if i % 25 == 0:
                await hub.broadcast("backfill", {"timestep": timestep, "done": i, "total": steps})
            await asyncio.sleep(gap)
        log.info("backfill %s complete: %s rows", timestep, stored)

    await db.set_meta("backfill_done", "1")
    await hub.broadcast("backfill", {"done": 1, "total": 1, "complete": True})


# ------------------------------------------------------------------ rollup ---

MID = (
    "CASE WHEN c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL "
    f"          AND GREATEST(c.avg_high, c.avg_low) <= {SPREAD_GUARD} * LEAST(c.avg_high, c.avg_low) "
    "     THEN (c.avg_high + c.avg_low) / 2.0 END"
)
CANDLE_MARGIN = "(c.avg_high - ff_tax(c.avg_high, x.item_id IS NOT NULL) - c.avg_low)"

AGGREGATE_SQL = f"""
WITH hist AS (
    -- A midpoint needs both sides of the book to have traded AND to agree on
    -- roughly what the item is worth. One-sided windows and lone outlier trades
    -- yield NULL rather than a fake price.
    SELECT c.item_id, c.ts, c.high_vol, c.low_vol, {MID} AS mid
      FROM candles c
     WHERE c.timestep = '1h' AND c.ts > now() - INTERVAL '8 days'
),
agg24 AS (
    SELECT c.item_id,
           SUM(c.high_vol + c.low_vol)                        AS vol_24h,
           count(*)                                           AS n_24h,
           SUM(c.high_vol)                                    AS buy_vol_24h,
           SUM(c.low_vol)                                     AS sell_vol_24h,
           AVG({CANDLE_MARGIN}) FILTER (
               WHERE c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL) AS avg_margin_24h,
           -- Margin variability: stdev of the post-tax spread over its own mean.
           -- A margin that only exists in flickers has a huge coefficient here.
           STDDEV_POP({CANDLE_MARGIN}) FILTER (
               WHERE c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL) AS sd_margin_24h,
           AVG(CASE WHEN c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL
                      AND {CANDLE_MARGIN} > 0 THEN 1.0 ELSE 0.0 END) FILTER (
               WHERE c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL) AS margin_positive_24h
      FROM candles c
      LEFT JOIN tax_exemptions x ON x.item_id = c.item_id
     WHERE c.timestep = '1h' AND c.ts > now() - INTERVAL '24 hours'
     GROUP BY c.item_id
),
vol1h AS (
    SELECT item_id, SUM(high_vol + low_vol) AS vol_1h
      FROM candles WHERE timestep = '5m' AND ts > now() - INTERVAL '1 hour'
     GROUP BY item_id
),
-- The 1 hour reference comes from 5 minute candles: hourly candles are stamped
-- at the start of a completed hour, so they sit 60-120 minutes back and a narrow
-- window around "one hour ago" misses them for half of every hour.
ref1h AS (
    SELECT c.item_id, AVG({MID}) AS mid_1h
      FROM candles c
     WHERE c.timestep = '5m'
       AND c.ts >= now() - INTERVAL '70 minutes' AND c.ts <= now() - INTERVAL '50 minutes'
     GROUP BY c.item_id
),
refs AS (
    SELECT item_id,
           AVG(mid) FILTER (WHERE ts >= now() - INTERVAL '25 hours'
                              AND ts <= now() - INTERVAL '23 hours')        AS mid_24h,
           AVG(mid) FILTER (WHERE ts >= now() - INTERVAL '7 days 2 hours'
                              AND ts <= now() - INTERVAL '6 days 22 hours') AS mid_7d,
           AVG(mid) FILTER (WHERE ts > now() - INTERVAL '24 hours')         AS mean_mid_24h,
           STDDEV_POP(mid) FILTER (WHERE ts > now() - INTERVAL '24 hours')  AS sd_mid_24h,
           AVG(high_vol + low_vol)::double precision                        AS mean_hour_vol_7d,
           STDDEV_POP(high_vol + low_vol)::double precision                 AS sd_hour_vol_7d
      FROM hist GROUP BY item_id
),
rets AS (
    SELECT item_id, ln(mid / NULLIF(prev_mid, 0)) AS r FROM (
        SELECT item_id, mid, LAG(mid) OVER (PARTITION BY item_id ORDER BY ts) AS prev_mid
          FROM hist WHERE ts > now() - INTERVAL '24 hours' AND mid > 0
    ) s WHERE mid > 0 AND prev_mid > 0
),
vola AS (SELECT item_id, STDDEV_POP(r)::double precision AS volatility_24h FROM rets GROUP BY item_id),
chg AS (
    SELECT item_id, mid - LAG(mid) OVER (PARTITION BY item_id ORDER BY ts) AS d
      FROM hist WHERE ts > now() - INTERVAL '15 hours'
),
rsi AS (
    SELECT item_id,
           CASE WHEN SUM(CASE WHEN d < 0 THEN -d ELSE 0 END) = 0 THEN 100.0
                ELSE 100.0 - 100.0 / (1 + SUM(CASE WHEN d > 0 THEN d ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN d < 0 THEN -d ELSE 0 END), 0)) END::double precision AS rsi_14
      FROM chg WHERE d IS NOT NULL GROUP BY item_id
)
SELECT i.id, i.buy_limit, l.high, l.low,
       (x.item_id IS NOT NULL) AS tax_exempt,
       GREATEST(
           EXTRACT(EPOCH FROM (now() - COALESCE(l.high_time, l.fetched_at))),
           EXTRACT(EPOCH FROM (now() - COALESCE(l.low_time,  l.fetched_at)))
       )::int                                            AS data_age_seconds,
       COALESCE(v.vol_1h, 0) AS vol_1h, COALESCE(a.vol_24h, 0) AS vol_24h,
       COALESCE(a.buy_vol_24h, 0) AS buy_vol_24h, COALESCE(a.sell_vol_24h, 0) AS sell_vol_24h,
       a.avg_margin_24h, a.sd_margin_24h, a.margin_positive_24h, COALESCE(a.n_24h, 0) AS n_24h,
       h1.mid_1h, r.mid_24h, r.mid_7d, r.mean_mid_24h, r.sd_mid_24h,
       r.mean_hour_vol_7d, r.sd_hour_vol_7d, vo.volatility_24h, rs.rsi_14
  FROM items i
  JOIN latest l ON l.item_id = i.id
  LEFT JOIN tax_exemptions x ON x.item_id = i.id
  LEFT JOIN agg24 a  ON a.item_id = i.id
  LEFT JOIN vol1h v  ON v.item_id = i.id
  LEFT JOIN ref1h h1 ON h1.item_id = i.id
  LEFT JOIN refs  r  ON r.item_id = i.id
  LEFT JOIN vola  vo ON vo.item_id = i.id
  LEFT JOIN rsi   rs ON rs.item_id = i.id
 WHERE l.high IS NOT NULL OR l.low IS NOT NULL
"""

UPSERT_METRICS = """
INSERT INTO metrics (
    item_id, high, low, tax, margin, roi, breakeven_sell, crossed,
    vol_1h, vol_24h, buy_vol_24h, sell_vol_24h, flow_ratio,
    avg_margin_24h, margin_cv, margin_positive_24h,
    price_change_1h, price_change_24h, price_change_7d, volatility_24h,
    zscore_24h, vol_zscore, rsi_14, est_fill_hours, potential_profit,
    flip_score, score_components, data_age_seconds, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
        $21,$22,$23,$24,$25,$26,$27,$28, now())
ON CONFLICT (item_id) DO UPDATE SET
    high=EXCLUDED.high, low=EXCLUDED.low, tax=EXCLUDED.tax, margin=EXCLUDED.margin,
    roi=EXCLUDED.roi, breakeven_sell=EXCLUDED.breakeven_sell, crossed=EXCLUDED.crossed,
    vol_1h=EXCLUDED.vol_1h, vol_24h=EXCLUDED.vol_24h, buy_vol_24h=EXCLUDED.buy_vol_24h,
    sell_vol_24h=EXCLUDED.sell_vol_24h, flow_ratio=EXCLUDED.flow_ratio,
    avg_margin_24h=EXCLUDED.avg_margin_24h, margin_cv=EXCLUDED.margin_cv,
    margin_positive_24h=EXCLUDED.margin_positive_24h,
    price_change_1h=EXCLUDED.price_change_1h, price_change_24h=EXCLUDED.price_change_24h,
    price_change_7d=EXCLUDED.price_change_7d, volatility_24h=EXCLUDED.volatility_24h,
    zscore_24h=EXCLUDED.zscore_24h, vol_zscore=EXCLUDED.vol_zscore, rsi_14=EXCLUDED.rsi_14,
    est_fill_hours=EXCLUDED.est_fill_hours, potential_profit=EXCLUDED.potential_profit,
    flip_score=EXCLUDED.flip_score, score_components=EXCLUDED.score_components,
    data_age_seconds=EXCLUDED.data_age_seconds, updated_at=now()
"""


async def compute_metrics() -> int:
    """Aggregate in SQL, apply the money rules in Python, write one row per item."""
    tax = policy.current()
    rows = await db.fetch(AGGREGATE_SQL)
    payload = []
    for r in rows:
        item_id, high, low = r["id"], r["high"], r["low"]
        unit_tax = money.sale_tax(high, tax, item_id)
        m = money.margin(low, high, tax, item_id)
        roi = money.roi(low, high, tax, item_id)
        breakeven = money.breakeven_sell(low, tax, item_id)
        crossed = money.is_crossed(low, high)

        vol_24h, vol_1h = _int(r["vol_24h"]), _int(r["vol_1h"])
        hourly = vol_1h or (vol_24h / 24 if vol_24h else 0)
        fill_h = scoring.est_fill_hours(r["buy_limit"], hourly)
        qty = scoring.fillable_quantity(r["buy_limit"], vol_24h)
        potential = int(m * qty) if m and m > 0 else 0

        # Coefficient of variation on the post-tax spread.
        avg_margin, sd_margin = _float(r["avg_margin_24h"]), _float(r["sd_margin_24h"])
        margin_cv = None
        if avg_margin is not None and sd_margin is not None and abs(avg_margin) > 0:
            margin_cv = sd_margin / abs(avg_margin)

        mid_now = None
        if high and low and max(high, low) <= SPREAD_GUARD * min(high, low):
            mid_now = (high + low) / 2

        # A z-score needs enough observations, and integer prices on cheap items
        # produce near-zero deviations that would turn a one coin wobble into a
        # 40 sigma event. Floor the deviation at 0.2% of the mean.
        z = None
        mean_mid, sd_mid = _float(r["mean_mid_24h"]), _float(r["sd_mid_24h"])
        if mid_now is not None and _int(r["n_24h"]) >= 8 and mean_mid:
            z = (mid_now - mean_mid) / max(sd_mid or 0.0, 0.002 * mean_mid, 0.5)
        vz = None
        if r["sd_hour_vol_7d"] and r["mean_hour_vol_7d"] is not None and vol_1h:
            vz = (vol_1h - r["mean_hour_vol_7d"]) / r["sd_hour_vol_7d"]

        buy_vol, sell_vol = _int(r["buy_vol_24h"]), _int(r["sell_vol_24h"])
        total_vol = buy_vol + sell_vol
        flow = buy_vol / total_vol if total_vol else None

        score = scoring.flip_score(
            roi=roi, margin=m, potential_profit=potential, volume_24h=vol_24h,
            margin_cv=margin_cv, est_fill_hours=fill_h,
            quote_age_seconds=r["data_age_seconds"],
        )

        payload.append((
            item_id, high, low, unit_tax, m, roi, breakeven, crossed,
            vol_1h, vol_24h, buy_vol, sell_vol, flow,
            _as_numeric(avg_margin), _as_numeric(margin_cv),
            _as_numeric(_float(r["margin_positive_24h"])),
            _as_numeric(indicators.pct_change(_float(r["mid_1h"]), mid_now)),
            _as_numeric(indicators.pct_change(_float(r["mid_24h"]), mid_now)),
            _as_numeric(indicators.pct_change(_float(r["mid_7d"]), mid_now)),
            r["volatility_24h"], z, vz, r["rsi_14"], fill_h, potential,
            _as_numeric(score.total), json.dumps(score.as_dict()), r["data_age_seconds"],
        ))

    await db.executemany(UPSERT_METRICS, payload)
    await db.set_meta("last_metrics_run", str(int(time.time())))
    log.info("metrics recomputed for %s items", len(payload))
    return len(payload)


# ------------------------------------------------------ score validation -----

async def snapshot_scores() -> int:
    """Freeze what the model claims right now, to be graded once it matures."""
    result = await db.execute(
        """INSERT INTO score_snapshots
                  (item_id, ts, score, buy, sell, margin, roi, vol_24h, quantity, source)
           SELECT item_id, date_trunc('hour', now()), flip_score, low, high,
                  margin, roi, vol_24h,
                  CASE WHEN margin > 0 THEN potential_profit / margin ELSE 0 END,
                  'live'
             FROM metrics
            WHERE flip_score IS NOT NULL AND low IS NOT NULL AND low > 0
           ON CONFLICT (item_id, ts) DO UPDATE
              SET score = EXCLUDED.score, source = 'live'
            WHERE score_snapshots.source = 'reconstructed'"""
    )
    count = int(result.split()[-1]) if result.startswith("INSERT") else 0
    log.info("score snapshot stored for %s items", count)
    return count


GRADE_SQL = """
INSERT INTO score_outcomes (item_id, ts, horizon, exit_price, realised_margin,
                            realised_cycle_profit, realised_roi)
SELECT s.item_id, s.ts, $1::text, e.avg_high::bigint,
       (e.avg_high - ff_tax(e.avg_high, x.item_id IS NOT NULL) - s.buy)::bigint,
       ((e.avg_high - ff_tax(e.avg_high, x.item_id IS NOT NULL) - s.buy)
            * COALESCE(s.quantity, 0))::bigint,
       ((e.avg_high - ff_tax(e.avg_high, x.item_id IS NOT NULL) - s.buy) / s.buy)::numeric
  FROM score_snapshots s
  LEFT JOIN tax_exemptions x ON x.item_id = s.item_id
  JOIN LATERAL (
        SELECT c.avg_high
          FROM candles c
         WHERE c.item_id = s.item_id AND c.timestep = '1h'
           AND c.avg_high IS NOT NULL
           AND c.ts BETWEEN s.ts + $2::interval - INTERVAL '90 minutes'
                        AND s.ts + $2::interval + INTERVAL '90 minutes'
         ORDER BY abs(EXTRACT(EPOCH FROM (c.ts - (s.ts + $2::interval))))
         LIMIT 1
  ) e ON TRUE
 WHERE s.ts <= now() - $2::interval
   AND s.ts >= now() - INTERVAL '30 days'
   AND s.buy > 0
   AND NOT EXISTS (
        SELECT 1 FROM score_outcomes o
         WHERE o.item_id = s.item_id AND o.ts = s.ts AND o.horizon = $1::text)
ON CONFLICT (item_id, ts, horizon) DO NOTHING
"""


async def grade_outcomes() -> int:
    """Score every matured snapshot against what the market actually did.

    The exit price is the item's average instant-buy price one horizon later, so
    "realised margin" is what a flip entered at snapshot time and exited on
    schedule would genuinely have banked after tax.
    """
    total = 0
    for horizon, interval in HORIZONS.items():
        result = await db.execute(GRADE_SQL, horizon, interval)
        if result.startswith("INSERT"):
            total += int(result.split()[-1])
    if total:
        log.info("graded %s score outcomes", total)
    return total


RECONSTRUCT_SQL = f"""
WITH asof AS (SELECT $1::timestamptz AS t),
hour_candle AS (
    SELECT c.item_id, c.avg_high, c.avg_low, (x.item_id IS NOT NULL) AS exempt
      FROM candles c
      CROSS JOIN asof
      LEFT JOIN tax_exemptions x ON x.item_id = c.item_id
     WHERE c.timestep = '1h' AND c.ts = asof.t
       AND c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL
       AND c.avg_low > 0
),
window24 AS (
    SELECT c.item_id,
           SUM(c.high_vol + c.low_vol) AS vol_24h,
           AVG({CANDLE_MARGIN}) AS avg_margin,
           STDDEV_POP({CANDLE_MARGIN}) AS sd_margin
      FROM candles c
      CROSS JOIN asof
      LEFT JOIN tax_exemptions x ON x.item_id = c.item_id
     WHERE c.timestep = '1h'
       AND c.ts <= asof.t AND c.ts > asof.t - INTERVAL '24 hours'
       AND c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL
     GROUP BY c.item_id
)
SELECT h.item_id, i.buy_limit,
       h.avg_high::bigint AS sell, h.avg_low::bigint AS buy, h.exempt,
       COALESCE(w.vol_24h, 0) AS vol_24h, w.avg_margin, w.sd_margin
  FROM hour_candle h
  JOIN items i ON i.id = h.item_id
  LEFT JOIN window24 w ON w.item_id = h.item_id
"""


async def reconstruct_snapshots(hours: int = 96) -> int:
    """Rebuild score snapshots for hours that predate this install.

    Uses only data that existed at each hour, so it is a genuine as-of
    reconstruction rather than hindsight. It is still an approximation: the
    inputs are hourly averages rather than the live quotes the real scoreboard
    sees, and quote freshness cannot be recovered at all, so these rows are
    marked 'reconstructed' and reported separately.
    """
    if await db.get_meta("snapshots_reconstructed") == "1":
        return 0

    tax = policy.current()
    total = 0
    now_hour = int(time.time()) // 3600 * 3600
    for i in range(2, hours + 1):
        asof = datetime.fromtimestamp(now_hour - i * 3600, tz=timezone.utc)
        rows = await db.fetch(RECONSTRUCT_SQL, asof)
        payload = []
        for r in rows:
            item_id, buy, sell = r["item_id"], r["buy"], r["sell"]
            m = money.margin(buy, sell, tax, item_id)
            if m is None:
                continue
            roi = money.roi(buy, sell, tax, item_id)
            vol_24h = _int(r["vol_24h"])
            qty = scoring.fillable_quantity(r["buy_limit"], vol_24h)
            potential = int(m * qty) if m > 0 else 0
            avg_margin, sd_margin = _float(r["avg_margin"]), _float(r["sd_margin"])
            margin_cv = (
                sd_margin / abs(avg_margin)
                if avg_margin and sd_margin is not None and abs(avg_margin) > 0
                else None
            )
            score = scoring.flip_score(
                roi=roi, margin=m, potential_profit=potential, volume_24h=vol_24h,
                margin_cv=margin_cv,
                est_fill_hours=scoring.est_fill_hours(r["buy_limit"], vol_24h / 24 if vol_24h else 0),
                # Freshness is unrecoverable from an hourly average. A nominal
                # mid-interval age keeps the term from silently zeroing every
                # historical score and skewing the comparison.
                quote_age_seconds=300,
            )
            payload.append(
                (item_id, asof, _as_numeric(score.total), buy, sell, m, roi, vol_24h, qty)
            )

        if payload:
            await db.executemany(
                """INSERT INTO score_snapshots
                          (item_id, ts, score, buy, sell, margin, roi, vol_24h, quantity, source)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, 'reconstructed')
                   ON CONFLICT (item_id, ts) DO NOTHING""",
                payload,
            )
            total += len(payload)
        if i % 24 == 0:
            log.info("reconstructed snapshots: %s hours back, %s rows", i, total)

    await db.set_meta("snapshots_reconstructed", "1")
    log.info("snapshot reconstruction complete: %s rows", total)
    return total


# ------------------------------------------------------------------ alerts ---

ALERT_METRICS = {
    "high": "current instant-buy price",
    "low": "current instant-sell price",
    "margin": "post-tax margin",
    "roi": "post-tax ROI",
    "vol_1h": "hourly volume",
    "zscore_24h": "24h price z-score",
    "flip_score": "flip score",
}


async def evaluate_alerts() -> int:
    """Fire armed alerts whose condition holds, and re-arm ones that have retreated.

    Hysteresis plus a cooldown is what keeps a value hovering on its threshold
    from producing a toast every single minute.
    """
    rows = await db.fetch(
        """SELECT a.id, a.item_id, a.metric, a.op, a.threshold, a.hysteresis,
                  a.cooldown_s, a.last_fired, a.armed, i.name, m.*
             FROM alerts a
             JOIN items i ON i.id = a.item_id
             JOIN metrics m ON m.item_id = a.item_id
            WHERE a.active"""
    )
    fired = 0
    for r in rows:
        if r["metric"] not in ALERT_METRICS:
            continue
        raw = r[r["metric"]]
        if raw is None:
            continue
        value = float(raw)
        threshold, band = r["threshold"], r["hysteresis"] or 0.0
        above = r["op"] == "above"

        triggered = value > threshold if above else value < threshold
        # Re-arm only once the value has clearly retreated past the band.
        reset = value < threshold - band if above else value > threshold + band

        if not r["armed"]:
            if reset:
                await db.execute("UPDATE alerts SET armed = TRUE WHERE id = $1", r["id"])
            continue
        if not triggered:
            continue
        if r["last_fired"] is not None:
            age = time.time() - r["last_fired"].timestamp()
            if age < r["cooldown_s"]:
                continue

        message = (
            f"{r['name']}: {ALERT_METRICS[r['metric']]} is {_fmt(r['metric'], value)} "
            f"({r['op']} {_fmt(r['metric'], threshold)})"
        )
        event_id = await db.fetchval(
            """INSERT INTO alert_events (alert_id, item_id, message, value)
               VALUES ($1,$2,$3,$4) RETURNING id""",
            r["id"], r["item_id"], message, value,
        )
        await db.execute(
            "UPDATE alerts SET last_fired = now(), armed = FALSE WHERE id = $1", r["id"]
        )
        await hub.broadcast("alert", {
            "id": event_id, "alert_id": r["id"], "item_id": r["item_id"],
            "item_name": r["name"], "message": message, "value": value,
        })
        fired += 1
    return fired


def _fmt(metric: str, value: float) -> str:
    if metric == "roi":
        return f"{value * 100:.2f}%"
    if metric in ("zscore_24h", "flip_score"):
        return f"{value:.2f}"
    if metric in ("high", "low", "margin"):
        return f"{int(value):,} gp"
    return f"{int(value):,}"


# ------------------------------------------------------------------- loops ---

async def _loop(name: str, interval: int, fn, initial_delay: float = 0.0) -> None:
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        started = time.perf_counter()
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the loop
            log.exception("%s tick failed: %s", name, exc)
        await asyncio.sleep(max(interval - (time.perf_counter() - started), 1.0))


async def _latest_tick() -> None:
    count = await poll_latest()
    await hub.broadcast("latest", {"items": count, "at": int(time.time())})


async def _metrics_tick() -> None:
    await compute_metrics()
    fired = await evaluate_alerts()
    await hub.broadcast("metrics", {"at": int(time.time()), "alerts_fired": fired})


async def _validation_tick() -> None:
    await snapshot_scores()
    await grade_outcomes()


async def start_background(tasks: list[asyncio.Task]) -> None:
    """Kick off every periodic job. Called once from the app lifespan."""
    await install_sql_helpers()

    if await db.fetchval("SELECT count(*) FROM items") == 0:
        await refresh_mapping()
    await policy.seed_exemptions()
    await policy.reload()

    await poll_latest()
    for step in ("5m", "1h"):
        try:
            await store_bulk(step)
        except Exception as exc:  # noqa: BLE001
            log.warning("initial %s pull failed: %s", step, exc)
    try:
        await compute_metrics()
    except Exception as exc:  # noqa: BLE001
        log.warning("initial metrics run failed: %s", exc)

    specs = [
        ("latest", settings.poll_latest_seconds, _latest_tick, 5),
        ("5m", settings.poll_5m_seconds, lambda: store_bulk("5m"), 30),
        ("1h", settings.poll_1h_seconds, lambda: store_bulk("1h"), 60),
        ("mapping", settings.poll_mapping_seconds, refresh_mapping, 3600),
        ("metrics", settings.metrics_interval_seconds, _metrics_tick, 20),
        ("validation", settings.snapshot_interval_seconds, _validation_tick, 120),
        ("grading", settings.outcome_interval_seconds, grade_outcomes, 300),
    ]
    for name, interval, fn, delay in specs:
        tasks.append(asyncio.create_task(_loop(name, interval, fn, delay), name=f"ff-{name}"))

    if settings.backfill_on_start:
        tasks.append(asyncio.create_task(backfill(), name="ff-backfill"))

    if settings.reconstruct_snapshots_hours > 0:
        tasks.append(asyncio.create_task(_seed_validation(), name="ff-reconstruct"))


async def _seed_validation() -> None:
    """Give the validation harness something to grade on first boot."""
    await asyncio.sleep(90)   # let the candle backfill get ahead first
    try:
        await reconstruct_snapshots(settings.reconstruct_snapshots_hours)
        await grade_outcomes()
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot reconstruction failed: %s", exc)
