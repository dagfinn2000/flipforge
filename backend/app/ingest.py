"""Background ingest: pull upstream data, roll it up, fire alerts."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import analytics, db
from .config import settings
from .hub import hub
from .wiki import STEP_SECONDS, client

log = logging.getLogger("flipforge.ingest")

# A window whose two sides disagree by more than this factor is discarded when
# computing midpoints, price changes and volatility. Three times is deliberately
# permissive: real illiquid spreads run 10-30%, not 1000%.
SPREAD_GUARD = 3.0


def _ts(epoch: Optional[int]) -> Optional[datetime]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


# The tax rule is duplicated into SQL purely so per-candle aggregates (average
# margin, margin stability) can be computed inside the database instead of
# shipping every candle to Python each minute. It is generated from the same
# settings as analytics.sale_tax so the two cannot drift apart.
TAX_FN = """
CREATE OR REPLACE FUNCTION ff_tax(price DOUBLE PRECISION, exempt BOOLEAN)
RETURNS DOUBLE PRECISION AS $$
  SELECT CASE
    WHEN price IS NULL OR exempt OR price < {min_price} THEN 0
    ELSE LEAST(FLOOR(price * {rate}), {cap}::DOUBLE PRECISION)
  END;
$$ LANGUAGE SQL IMMUTABLE;
"""


async def install_sql_helpers() -> None:
    await db.execute(
        TAX_FN.format(
            min_price=settings.ge_tax_min_price,
            rate=settings.ge_tax_rate,
            cap=settings.ge_tax_cap,
        )
    )


# ------------------------------------------------------------------ mapping --

async def refresh_mapping() -> int:
    rows = await client.mapping()
    payload = [
        (
            int(r["id"]),
            r["name"],
            r.get("examine"),
            bool(r.get("members", False)),
            r.get("value"),
            r.get("lowalch"),
            r.get("highalch"),
            r.get("limit"),
            r.get("icon"),
            analytics.is_tax_exempt(r["name"]),
        )
        for r in rows
        if r.get("id") is not None and r.get("name")
    ]
    await db.executemany(
        """INSERT INTO items (id, name, examine, members, value, lowalch, highalch,
                              buy_limit, icon, tax_exempt, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, now())
           ON CONFLICT (id) DO UPDATE SET
             name=EXCLUDED.name, examine=EXCLUDED.examine, members=EXCLUDED.members,
             value=EXCLUDED.value, lowalch=EXCLUDED.lowalch, highalch=EXCLUDED.highalch,
             buy_limit=EXCLUDED.buy_limit, icon=EXCLUDED.icon,
             tax_exempt=EXCLUDED.tax_exempt, updated_at=now()""",
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
        rows.append(
            (item_id, q.get("high"), _ts(q.get("highTime")), q.get("low"), _ts(q.get("lowTime")))
        )
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

async def store_bulk(timestep: str, timestamp: Optional[int] = None) -> int:
    data = await client.bulk(timestep, timestamp)
    if not data:
        return 0
    # The bulk endpoints label a window by its start timestamp; when we ask for
    # "now" the API returns the most recent complete window.
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
        rows.append(
            (
                item_id,
                timestep,
                ts,
                q.get("avgHighPrice"),
                q.get("avgLowPrice"),
                q.get("highPriceVolume") or 0,
                q.get("lowPriceVolume") or 0,
            )
        )
    await _upsert_candles(rows)
    return len(rows)


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


async def fetch_item_series(item_id: int, timestep: str) -> int:
    """Pull up to a year of history for one item and cache it."""
    points = await client.timeseries(item_id, timestep)
    rows = [
        (
            item_id,
            timestep,
            _ts(p["timestamp"]),
            p.get("avgHighPrice"),
            p.get("avgLowPrice"),
            p.get("highPriceVolume") or 0,
            p.get("lowPriceVolume") or 0,
        )
        for p in points
        if p.get("timestamp") is not None
    ]
    await _upsert_candles(rows)
    return len(rows)


# ----------------------------------------------------------------- backfill --

async def backfill() -> None:
    """Walk the bulk endpoints backwards to build history for every item at once.

    One request per timestamp covers all ~4000 items, which is far cheaper for
    the upstream API than crawling items individually.
    """
    if await db.get_meta("backfill_done") == "1":
        log.info("backfill already complete, skipping")
        return

    gap = 1.0 / max(settings.backfill_rate_per_sec, 0.5)
    plan = (("1h", settings.backfill_1h_steps), ("5m", settings.backfill_5m_steps))
    for timestep, steps in plan:
        step_seconds = STEP_SECONDS[timestep]
        now_bucket = _floor_now(timestep)
        stored = 0
        for i in range(1, steps + 1):
            ts = now_bucket - i * step_seconds
            try:
                stored += await store_bulk(timestep, ts)
            except Exception as exc:  # noqa: BLE001 - never let backfill kill startup
                log.warning("backfill %s @%s failed: %s", timestep, ts, exc)
            if i % 25 == 0:
                log.info("backfill %s: %s/%s windows, %s rows", timestep, i, steps, stored)
                await hub.broadcast(
                    "backfill", {"timestep": timestep, "done": i, "total": steps}
                )
            await asyncio.sleep(gap)
        log.info("backfill %s complete: %s rows", timestep, stored)

    await db.set_meta("backfill_done", "1")
    await hub.broadcast("backfill", {"done": 1, "total": 1, "complete": True})


# ------------------------------------------------------------------ rollup ---

# A midpoint is only meaningful when both sides of the book traded in the window
# AND they agree on roughly what the item is worth. Filling a missing side from
# the other turns one stray trade into a fake 2000% move, and a window where
# someone paid 11,000 for a 400gp cape holds two unrelated trades rather than a
# price. Both cases yield NULL and are simply skipped by the aggregates below.
MID = (
    "CASE WHEN c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL "
    f"          AND GREATEST(c.avg_high, c.avg_low) <= {SPREAD_GUARD} * LEAST(c.avg_high, c.avg_low) "
    "     THEN (c.avg_high + c.avg_low)::double precision / 2.0 END"
)

AGGREGATE_SQL = f"""
WITH hist AS (
    SELECT c.item_id, c.ts, c.high_vol, c.low_vol, {MID} AS mid
      FROM candles c
     WHERE c.timestep = '1h' AND c.ts > now() - INTERVAL '8 days'
),
agg24 AS (
    SELECT c.item_id,
           SUM(c.high_vol + c.low_vol)                              AS vol_24h,
           count(*)                                                 AS n_24h,
           SUM(c.high_vol)                                          AS buy_vol_24h,
           SUM(c.low_vol)                                           AS sell_vol_24h,
           (AVG(c.avg_high - ff_tax(c.avg_high, i.tax_exempt) - c.avg_low)
               FILTER (WHERE c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL))::double precision
               AS avg_margin_24h,
           (AVG(CASE WHEN c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL
                      AND (c.avg_high - ff_tax(c.avg_high, i.tax_exempt) - c.avg_low) > 0
                     THEN 1.0 ELSE 0.0 END)
               FILTER (WHERE c.avg_high IS NOT NULL AND c.avg_low IS NOT NULL))::double precision
               AS margin_stability
      FROM candles c JOIN items i ON i.id = c.item_id
     WHERE c.timestep = '1h' AND c.ts > now() - INTERVAL '24 hours'
     GROUP BY c.item_id
),
vol1h AS (
    SELECT item_id, SUM(high_vol + low_vol) AS vol_1h
      FROM candles
     WHERE timestep = '5m' AND ts > now() - INTERVAL '1 hour'
     GROUP BY item_id
),
-- The 1 hour reference comes from 5 minute candles. Hourly candles are stamped
-- at the start of a completed hour, so the newest one sits 60-120 minutes back
-- and a narrow window around "one hour ago" misses it for half of every hour.
ref1h AS (
    SELECT c.item_id, AVG({MID}) AS mid_1h
      FROM candles c
     WHERE c.timestep = '5m'
       AND c.ts >= now() - INTERVAL '70 minutes'
       AND c.ts <= now() - INTERVAL '50 minutes'
     GROUP BY c.item_id
),
refs AS (
    SELECT item_id,
           AVG(mid) FILTER (WHERE ts >= now() - INTERVAL '25 hours'
                              AND ts <= now() - INTERVAL '23 hours')         AS mid_24h,
           AVG(mid) FILTER (WHERE ts >= now() - INTERVAL '7 days 2 hours'
                              AND ts <= now() - INTERVAL '6 days 22 hours')  AS mid_7d,
           AVG(mid) FILTER (WHERE ts > now() - INTERVAL '24 hours')          AS mean_mid_24h,
           STDDEV_POP(mid) FILTER (WHERE ts > now() - INTERVAL '24 hours')   AS sd_mid_24h,
           AVG(high_vol + low_vol)::double precision                         AS mean_hour_vol_7d,
           STDDEV_POP(high_vol + low_vol)::double precision                  AS sd_hour_vol_7d
      FROM hist
     GROUP BY item_id
),
rets AS (
    SELECT item_id, ln(mid / NULLIF(prev_mid, 0)) AS r FROM (
        SELECT item_id, mid, LAG(mid) OVER (PARTITION BY item_id ORDER BY ts) AS prev_mid
          FROM hist WHERE ts > now() - INTERVAL '24 hours' AND mid > 0
    ) s WHERE mid > 0 AND prev_mid > 0
),
vola AS (
    SELECT item_id, STDDEV_POP(r) AS volatility_24h FROM rets GROUP BY item_id
),
chg AS (
    SELECT item_id, mid - LAG(mid) OVER (PARTITION BY item_id ORDER BY ts) AS d
      FROM hist WHERE ts > now() - INTERVAL '15 hours'
),
rsi AS (
    SELECT item_id,
           CASE WHEN SUM(CASE WHEN d < 0 THEN -d ELSE 0 END) = 0 THEN 100.0
                ELSE 100.0 - 100.0 / (1 + SUM(CASE WHEN d > 0 THEN d ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN d < 0 THEN -d ELSE 0 END), 0)) END AS rsi_14
      FROM chg WHERE d IS NOT NULL GROUP BY item_id
)
SELECT i.id, i.buy_limit, i.tax_exempt, l.high, l.low,
       GREATEST(
           EXTRACT(EPOCH FROM (now() - COALESCE(l.high_time, l.fetched_at))),
           EXTRACT(EPOCH FROM (now() - COALESCE(l.low_time,  l.fetched_at)))
       )::int                                            AS data_age_seconds,
       COALESCE(v.vol_1h, 0)                             AS vol_1h,
       COALESCE(a.vol_24h, 0)                            AS vol_24h,
       COALESCE(a.buy_vol_24h, 0)                        AS buy_vol_24h,
       COALESCE(a.sell_vol_24h, 0)                       AS sell_vol_24h,
       a.avg_margin_24h, a.margin_stability, COALESCE(a.n_24h, 0) AS n_24h,
       h1.mid_1h, r.mid_24h, r.mid_7d, r.mean_mid_24h, r.sd_mid_24h,
       r.mean_hour_vol_7d, r.sd_hour_vol_7d,
       vo.volatility_24h, rs.rsi_14
  FROM items i
  JOIN latest l   ON l.item_id = i.id
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
    item_id, high, low, spread, tax, margin, roi, vol_1h, vol_24h,
    buy_vol_24h, sell_vol_24h, flow_ratio, avg_margin_24h, margin_stability,
    price_change_1h, price_change_24h, price_change_7d, volatility_24h,
    zscore_24h, vol_zscore, rsi_14, est_fill_hours, potential_profit,
    liquidity_score, flip_score, data_age_seconds, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
        $21,$22,$23,$24,$25,$26, now())
ON CONFLICT (item_id) DO UPDATE SET
    high=EXCLUDED.high, low=EXCLUDED.low, spread=EXCLUDED.spread, tax=EXCLUDED.tax,
    margin=EXCLUDED.margin, roi=EXCLUDED.roi, vol_1h=EXCLUDED.vol_1h,
    vol_24h=EXCLUDED.vol_24h, buy_vol_24h=EXCLUDED.buy_vol_24h,
    sell_vol_24h=EXCLUDED.sell_vol_24h, flow_ratio=EXCLUDED.flow_ratio,
    avg_margin_24h=EXCLUDED.avg_margin_24h, margin_stability=EXCLUDED.margin_stability,
    price_change_1h=EXCLUDED.price_change_1h, price_change_24h=EXCLUDED.price_change_24h,
    price_change_7d=EXCLUDED.price_change_7d, volatility_24h=EXCLUDED.volatility_24h,
    zscore_24h=EXCLUDED.zscore_24h, vol_zscore=EXCLUDED.vol_zscore, rsi_14=EXCLUDED.rsi_14,
    est_fill_hours=EXCLUDED.est_fill_hours, potential_profit=EXCLUDED.potential_profit,
    liquidity_score=EXCLUDED.liquidity_score, flip_score=EXCLUDED.flip_score,
    data_age_seconds=EXCLUDED.data_age_seconds, updated_at=now()
"""


async def compute_metrics() -> int:
    """Aggregate in SQL, derive in Python, write back one row per item."""
    rows = await db.fetch(AGGREGATE_SQL)
    payload = []
    for r in rows:
        high, low = r["high"], r["low"]
        exempt = r["tax_exempt"]
        tax = analytics.sale_tax(high, exempt)
        m = analytics.margin(low, high, exempt)
        roi_v = analytics.roi(low, high, exempt)
        spread = (high - low) if (high and low) else None

        vol_24h = int(r["vol_24h"] or 0)
        vol_1h = int(r["vol_1h"] or 0)
        hourly = vol_1h or (vol_24h / 24 if vol_24h else 0)
        fill_h = analytics.est_fill_hours(r["buy_limit"], hourly)
        qty = analytics.fillable_quantity(r["buy_limit"], vol_24h)
        potential = int(m * qty) if m and m > 0 else 0

        # Same rule as the history above: both sides, priced sanely, or nothing.
        mid_now = None
        if high and low and max(high, low) <= SPREAD_GUARD * min(high, low):
            mid_now = (high + low) / 2

        # A z-score needs enough observations to mean anything, and integer
        # prices on cheap items produce near-zero deviations that would otherwise
        # turn a one coin wobble into a 40 sigma "event". Floor the deviation at
        # 0.2% of the mean so the score stays a measure of percentage movement.
        z = None
        if mid_now is not None and int(r["n_24h"] or 0) >= 8 and r["mean_mid_24h"]:
            sd = max(r["sd_mid_24h"] or 0.0, 0.002 * r["mean_mid_24h"], 0.5)
            z = (mid_now - r["mean_mid_24h"]) / sd
        vz = None
        if r["sd_hour_vol_7d"] and r["mean_hour_vol_7d"] is not None and vol_1h:
            vz = (vol_1h - r["mean_hour_vol_7d"]) / r["sd_hour_vol_7d"]

        buy_vol = int(r["buy_vol_24h"] or 0)
        sell_vol = int(r["sell_vol_24h"] or 0)
        total_vol = buy_vol + sell_vol
        flow = buy_vol / total_vol if total_vol else None

        score = analytics.flip_score(
            roi_value=roi_v,
            margin_value=m,
            vol_24h=vol_24h,
            margin_stability=r["margin_stability"],
            est_fill_hours=fill_h,
            data_age_seconds=r["data_age_seconds"],
            potential_profit=potential,
        )

        payload.append(
            (
                r["id"], high, low, spread, tax, m, roi_v, vol_1h, vol_24h,
                buy_vol, sell_vol, flow,
                r["avg_margin_24h"], r["margin_stability"],
                analytics.pct_change(r["mid_1h"], mid_now),
                analytics.pct_change(r["mid_24h"], mid_now),
                analytics.pct_change(r["mid_7d"], mid_now),
                r["volatility_24h"], z, vz, r["rsi_14"], fill_h, potential,
                score.volume * 100, score.total, r["data_age_seconds"],
            )
        )

    await db.executemany(UPSERT_METRICS, payload)
    await db.set_meta("last_metrics_run", str(int(time.time())))
    log.info("metrics recomputed for %s items", len(payload))
    return len(payload)


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
    rows = await db.fetch(
        """SELECT a.id, a.item_id, a.metric, a.op, a.threshold, a.cooldown_s,
                  a.last_fired, i.name, m.*
             FROM alerts a
             JOIN items i ON i.id = a.item_id
             JOIN metrics m ON m.item_id = a.item_id
            WHERE a.active
              AND (a.last_fired IS NULL
                   OR a.last_fired < now() - make_interval(secs => a.cooldown_s))"""
    )
    fired = 0
    for r in rows:
        value = r[r["metric"]] if r["metric"] in ALERT_METRICS else None
        if value is None:
            continue
        hit = value > r["threshold"] if r["op"] == "above" else value < r["threshold"]
        if not hit:
            continue
        label = ALERT_METRICS[r["metric"]]
        msg = f"{r['name']}: {label} is {_fmt(r['metric'], value)} ({r['op']} {_fmt(r['metric'], r['threshold'])})"
        event_id = await db.fetchval(
            """INSERT INTO alert_events (alert_id, item_id, message, value)
               VALUES ($1,$2,$3,$4) RETURNING id""",
            r["id"], r["item_id"], msg, float(value),
        )
        await db.execute("UPDATE alerts SET last_fired = now() WHERE id = $1", r["id"])
        await hub.broadcast(
            "alert",
            {"id": event_id, "alert_id": r["id"], "item_id": r["item_id"],
             "item_name": r["name"], "message": msg, "value": float(value)},
        )
        fired += 1
    return fired


def _fmt(metric: str, value: float) -> str:
    if metric == "roi":
        return f"{value * 100:.2f}%"
    if metric in ("zscore_24h", "flip_score"):
        return f"{value:.2f}"
    return f"{int(value):,} gp" if metric in ("high", "low", "margin") else f"{int(value):,}"


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
        elapsed = time.perf_counter() - started
        await asyncio.sleep(max(interval - elapsed, 1.0))


async def _latest_tick() -> None:
    count = await poll_latest()
    await hub.broadcast("latest", {"items": count, "at": int(time.time())})


async def _metrics_tick() -> None:
    await compute_metrics()
    fired = await evaluate_alerts()
    await hub.broadcast(
        "metrics", {"at": int(time.time()), "alerts_fired": fired}
    )


async def start_background(tasks: list[asyncio.Task]) -> None:
    """Kick off every periodic job. Called once from the app lifespan."""
    await install_sql_helpers()

    if await db.fetchval("SELECT count(*) FROM items") == 0:
        await refresh_mapping()

    # Prime the caches before serving so the UI is never empty on first paint.
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
    ]
    for name, interval, fn, delay in specs:
        tasks.append(asyncio.create_task(_loop(name, interval, fn, delay), name=f"ff-{name}"))

    if settings.backfill_on_start:
        tasks.append(asyncio.create_task(backfill(), name="ff-backfill"))
