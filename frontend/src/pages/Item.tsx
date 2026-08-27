import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import PriceChart, { type ChartMode, type Overlay } from "../components/PriceChart";
import { ago, clsx, duration, gp, gpShort, num, pct, tone } from "../lib/format";

const TIMEFRAMES = [
  { step: "5m", label: "5m", limit: 288, hint: "24 hours of 5 minute candles" },
  { step: "1h", label: "1h", limit: 336, hint: "2 weeks of hourly candles" },
  { step: "6h", label: "6h", limit: 365, hint: "3 months of 6 hour candles" },
  { step: "24h", label: "1d", limit: 365, hint: "a year of daily candles" },
];

const OVERLAY_OPTIONS: { id: Overlay; label: string }[] = [
  { id: "sma20", label: "SMA 20" },
  { id: "sma50", label: "SMA 50" },
  { id: "vwap", label: "VWAP" },
  { id: "bollinger", label: "Bollinger" },
];

/** What each score term actually measures, shown on hover. */
const COMPONENT_HELP: Record<string, string> = {
  roi: "Post-tax return on the coins tied up, saturating so 500% cannot dwarf 5%",
  profit: "Absolute gp per 4 hour cycle. This is what stops a 1gp feather spread outranking a real trade",
  liquidity: "Units traded on both sides over 24 hours",
  stability: "How steady the margin is: its standard deviation over its own mean",
  fill: "Estimated time to buy a full limit at current flow, against the 4 hour window",
  freshness: "Age of the last real trade behind this quote",
};

function Row({ k, v, hint }: { k: string; v: React.ReactNode; hint?: string }) {
  return (
    <div className="kv" title={hint}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}

export default function ItemPage() {
  const { id } = useParams();
  const itemId = Number(id);
  const queryClient = useQueryClient();

  const [timeframe, setTimeframe] = useState(TIMEFRAMES[1]);
  const [overlays, setOverlays] = useState<Overlay[]>(["sma20"]);
  const [mode, setMode] = useState<ChartMode>("spread");
  const [qty, setQty] = useState<string>("");
  const [buyAt, setBuyAt] = useState<string>("");
  const [sellAt, setSellAt] = useState<string>("");
  const [alertMetric, setAlertMetric] = useState("margin");
  const [alertOp, setAlertOp] = useState("above");
  const [alertValue, setAlertValue] = useState("");
  const [alertBand, setAlertBand] = useState("");

  const item = useQuery({ queryKey: ["item", itemId], queryFn: () => api.item(itemId) });
  const series = useQuery({
    queryKey: ["series", itemId, timeframe.step],
    queryFn: () => api.series(itemId, timeframe.step, timeframe.limit),
  });

  // Seed the calculator from live prices, then leave the user's edits alone.
  useEffect(() => {
    if (item.data && buyAt === "" && sellAt === "") {
      if (item.data.low) setBuyAt(String(item.data.low));
      if (item.data.high) setSellAt(String(item.data.high));
      if (item.data.buy_limit) setQty(String(item.data.buy_limit));
    }
  }, [item.data, buyAt, sellAt]);

  const calc = useQuery({
    queryKey: ["calc", itemId, buyAt, sellAt, qty],
    queryFn: () =>
      api.calculator(
        itemId,
        buyAt ? Number(buyAt) : undefined,
        sellAt ? Number(sellAt) : undefined,
        qty ? Number(qty) : undefined,
      ),
    enabled: Boolean(buyAt && sellAt),
  });

  const watchToggle = useMutation({
    mutationFn: () =>
      item.data?.watched ? api.unwatch(itemId) : api.watch(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["item", itemId] });
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });

  const createAlert = useMutation({
    mutationFn: () =>
      api.createAlert({
        item_id: itemId,
        metric: alertMetric,
        op: alertOp,
        threshold: alertMetric === "roi" ? Number(alertValue) / 100 : Number(alertValue),
        hysteresis: alertBand
          ? (alertMetric === "roi" ? Number(alertBand) / 100 : Number(alertBand))
          : 0,
      }),
    onSuccess: () => {
      setAlertValue("");
      setAlertBand("");
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const d = item.data;
  const breakdown = d?.score_breakdown;

  const spreadSplit = useMemo(() => {
    if (!d?.flow_ratio) return null;
    return { buy: d.flow_ratio * 100, sell: (1 - d.flow_ratio) * 100 };
  }, [d]);

  if (item.isError) {
    return <div className="empty"><h3>Item not found</h3><p>That item id is not in the mapping.</p></div>;
  }

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <div className="item-hero" style={{ flex: 1 }}>
          {d?.icon_url && <img src={d.icon_url} alt="" />}
          <div>
            <h1>{d?.name ?? "Loading..."}</h1>
            <div className="examine">{d?.examine}</div>
          </div>
          <div style={{ display: "flex", gap: 7, marginLeft: "auto", flexWrap: "wrap" }}>
            {d?.members && <span className="tag members">members</span>}
            {d?.tax_exempt && <span className="tag exempt">tax exempt</span>}
            <button
              className={clsx("btn small", d?.watched && "active")}
              onClick={() => watchToggle.mutate()}
            >
              {d?.watched ? "★ Watching" : "☆ Watch"}
            </button>
          </div>
        </div>
      </div>

      {d?.crossed && (
        <div className="banner">
          Crossed quote: the instant-sell price is above the instant-buy price. The last two
          trades landed out of order, so this margin is genuinely negative rather than an
          error. It is shown as-is rather than clamped.
        </div>
      )}

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <div className="card stat">
          <div className="label">Buy at (instant sell)</div>
          <div className="value">{gp(d?.low)}</div>
          <div className="foot">{d?.low_time ? `seen ${ago(d.low_time)}` : "no recent quote"}</div>
        </div>
        <div className="card stat">
          <div className="label">Sell at (instant buy)</div>
          <div className="value">{gp(d?.high)}</div>
          <div className="foot">{d?.high_time ? `seen ${ago(d.high_time)}` : "no recent quote"}</div>
        </div>
        <div className="card stat">
          <div className="label">Margin after tax</div>
          <div className={`value ${tone(d?.margin)}`}>{gp(d?.margin, { sign: true })}</div>
          <div className="foot">
            {pct(d?.roi)} ROI · {gp(d?.tax)} gp tax per unit
          </div>
        </div>
        <div className="card stat">
          <div className="label">Breakeven sell</div>
          <div className="value">{gp(d?.breakeven_sell)}</div>
          <div className="foot">
            {d?.breakeven_sell && d?.low
              ? `${gp(d.breakeven_sell - d.low, { sign: true })} above your buy, or you lose money`
              : "lowest sell that covers the buy after tax"}
          </div>
        </div>
        <div className="card stat">
          <div className="label">Flip score</div>
          <div className="value gold">{d?.flip_score ? d.flip_score.toFixed(0) : "--"}</div>
          <div className="foot">{gpShort(d?.potential_profit)} gp per 4h cycle</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Price history</h2>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div className="overlay-toggles">
              {OVERLAY_OPTIONS.map((o) => (
                <button
                  key={o.id}
                  className={clsx("btn small", overlays.includes(o.id) && "active")}
                  onClick={() =>
                    setOverlays((prev) =>
                      prev.includes(o.id) ? prev.filter((x) => x !== o.id) : [...prev, o.id],
                    )
                  }
                >
                  {o.label}
                </button>
              ))}
            </div>
            <div className="seg">
              <button className={mode === "spread" ? "active" : ""} onClick={() => setMode("spread")}>
                spread
              </button>
              <button className={mode === "candles" ? "active" : ""} onClick={() => setMode("candles")}>
                candles
              </button>
            </div>
            <div className="seg">
              {TIMEFRAMES.map((t) => (
                <button
                  key={t.step}
                  title={t.hint}
                  className={timeframe.step === t.step ? "active" : ""}
                  onClick={() => setTimeframe(t)}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="card-body">
          {series.isLoading ? (
            <div className="skeleton" style={{ height: 380 }} />
          ) : series.data?.points.length ? (
            <PriceChart points={series.data.points} overlays={overlays} mode={mode} />
          ) : (
            <div className="empty">
              <h3>No history yet</h3>
              <p>This item has not traded in the selected window.</p>
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--text-faint)" }}>
            {mode === "candles"
              ? "Candles are derived: the exchange publishes average high and low per interval, not a true open and close."
              : "Red is what impatient buyers pay (your sell price). Green is what impatient sellers accept (your buy price). The shaded band between them is the gross spread; your post-tax margin is always smaller. Volume bars below are split by side: the lower segment traded at the instant-buy price, the upper at the instant-sell price."}
            {series.data?.volatility != null &&
              ` · volatility ${(series.data.volatility * 100).toFixed(2)}% per interval`}
          </div>
        </div>
      </div>

      <div className="grid cols-3">
        <div className="card">
          <div className="card-head"><h2>Why this score</h2></div>
          <div className="card-body">
            {breakdown ? (
              <>
                {breakdown.components.map((c) => (
                  <div className="meter-row" key={c.key} title={COMPONENT_HELP[c.key]}>
                    <span className="m-label">{c.key}</span>
                    <span className="meter">
                      <span style={{ width: `${Math.round(c.value * 100)}%` }} />
                    </span>
                    <span className="m-value">
                      +{c.contribution.toFixed(1)}
                      <span style={{ color: "var(--text-faint)" }}>
                        {" "}/{(c.weight * 100).toFixed(0)}
                      </span>
                    </span>
                  </div>
                ))}
                <div className="kv" style={{ marginTop: 8, borderTop: "1px solid var(--line)" }}>
                  <span className="k">total</span>
                  <span className="v gold">{breakdown.total.toFixed(1)} / 100</span>
                </div>
                {breakdown.notes.length > 0 && (
                  <ul className="note-list">
                    {breakdown.notes.map((n) => <li key={n}>{n}</li>)}
                  </ul>
                )}
              </>
            ) : (
              <div className="skeleton" style={{ height: 130 }} />
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h2>Market depth</h2></div>
          <div className="card-body">
            <Row k="Buy limit (4h)" v={d?.buy_limit ? num(d.buy_limit) : "none"} />
            {d?.limit_window?.limit != null && (
              <Row
                k="Limit remaining"
                v={
                  <span className={d.limit_window.remaining === 0 ? "down" : "up"}>
                    {num(d.limit_window.remaining)} of {num(d.limit_window.limit)}
                  </span>
                }
                hint={
                  d.limit_window.resets_at
                    ? `Rolling window: your oldest logged buy ages out ${new Date(d.limit_window.resets_at * 1000).toLocaleTimeString()}`
                    : "Rolling 4 hour window, counted from your logged buys"
                }
              />
            )}
            <Row k="Volume 1h" v={gpShort(d?.vol_1h)} />
            <Row k="Volume 24h" v={gpShort(d?.vol_24h)} />
            <Row
              k="Time to fill limit"
              v={duration(d?.est_fill_hours)}
              hint="Assumes you capture about a quarter of one side's flow"
            />
            <Row
              k="Margin steadiness"
              v={
                d?.margin_cv == null
                  ? "--"
                  : `${d.margin_cv < 0.35 ? "firm" : d.margin_cv < 1 ? "loose" : d.margin_cv < 2 ? "jumpy" : "flicker"} (${d.margin_cv.toFixed(2)})`
              }
              hint="Standard deviation of the post-tax spread over its own mean. A margin that only exists in flickers scores badly here."
            />
            <Row
              k="Profitable 24h"
              v={d?.margin_positive_24h == null ? "--" : pct(d.margin_positive_24h, 0, false)}
              hint="Share of the last 24 hours where this flip had a post-tax edge at all"
            />
            <Row k="Volatility 24h" v={d?.volatility_24h == null ? "--" : pct(d.volatility_24h, 2, false)} />
            <Row k="RSI" v={d?.rsi_14 == null ? "--" : d.rsi_14.toFixed(1)} />
            <Row k="High alch" v={d?.highalch ? `${num(d.highalch)} gp` : "--"} />
            {spreadSplit && (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginBottom: 4 }}>
                  Order flow: {spreadSplit.buy.toFixed(0)}% bought instantly
                </div>
                <div className="spread-bar">
                  <span className="buy-side" style={{ width: `${spreadSplit.buy}%` }} />
                  <span className="sell-side" style={{ width: `${spreadSplit.sell}%` }} />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Profit calculator</h2>
            <span className="hint">tax included</span>
          </div>
          <div className="card-body">
            <div className="inline-form" style={{ marginBottom: 12 }}>
              <div className="field">
                <label>Buy at</label>
                <input type="number" value={buyAt} onChange={(e) => setBuyAt(e.target.value)} />
              </div>
              <div className="field">
                <label>Sell at</label>
                <input type="number" value={sellAt} onChange={(e) => setSellAt(e.target.value)} />
              </div>
              <div className="field">
                <label>Quantity</label>
                <input type="number" value={qty} onChange={(e) => setQty(e.target.value)} />
              </div>
            </div>
            {calc.data && (
              <>
                <Row k="Capital required" v={`${gp(calc.data.capital_required as number)} gp`} />
                <Row k="Tax per unit" v={`${gp(calc.data.unit_tax as number)} gp`} />
                <Row k="Total tax" v={`${gp(calc.data.total_tax as number)} gp`} />
                <Row
                  k="Profit"
                  v={
                    <span className={tone(calc.data.profit as number)}>
                      {gp(calc.data.profit as number, { sign: true })} gp
                    </span>
                  }
                />
                <Row k="ROI" v={pct(calc.data.roi as number)} />
                <Row
                  k="Breakeven sell"
                  v={`${gp(calc.data.breakeven_sell as number)} gp`}
                  hint="Selling at your buy price is a loss; this is the lowest price that is not"
                />
                {Boolean(calc.data.over_limit) && (
                  <div className="banner" style={{ marginTop: 10, marginBottom: 0 }}>
                    Quantity exceeds the {num(calc.data.buy_limit as number)} unit buy limit; this
                    would take more than one 4 hour window.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>Alert me when</h2>
          <span className="hint">checked every minute against live data</span>
        </div>
        <div className="card-body">
          <div className="inline-form">
            <div className="field">
              <label>Metric</label>
              <select value={alertMetric} onChange={(e) => setAlertMetric(e.target.value)}>
                <option value="margin">margin (gp)</option>
                <option value="roi">ROI (%)</option>
                <option value="high">sell price (gp)</option>
                <option value="low">buy price (gp)</option>
                <option value="vol_1h">hourly volume</option>
                <option value="flip_score">flip score</option>
                <option value="zscore_24h">price z-score</option>
              </select>
            </div>
            <div className="field">
              <label>Goes</label>
              <select value={alertOp} onChange={(e) => setAlertOp(e.target.value)}>
                <option value="above">above</option>
                <option value="below">below</option>
              </select>
            </div>
            <div className="field">
              <label>Value</label>
              <input
                type="number"
                value={alertValue}
                onChange={(e) => setAlertValue(e.target.value)}
                placeholder={alertMetric === "roi" ? "5" : "1000"}
              />
            </div>
            <div className="field">
              <label title="The value must retreat this far past the threshold before the alert can fire again. Without it, a value hovering on the line fires every minute.">
                Reset band
              </label>
              <input
                type="number"
                value={alertBand}
                onChange={(e) => setAlertBand(e.target.value)}
                placeholder="0"
              />
            </div>
            <button
              className="btn primary"
              disabled={!alertValue || createAlert.isPending}
              onClick={() => createAlert.mutate()}
            >
              {createAlert.isPending ? "Saving..." : "Create alert"}
            </button>
            {createAlert.isSuccess && (
              <span style={{ color: "var(--up)", fontSize: 12.5 }}>Alert created.</span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
