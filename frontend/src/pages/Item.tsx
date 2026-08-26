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
      }),
    onSuccess: () => {
      setAlertValue("");
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

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
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
              : "Red is what impatient buyers pay (your sell price). Green is what impatient sellers accept (your buy price). The gap between them is your gross margin."}
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
                {(["roi", "profit", "volume", "stability", "fill", "freshness"] as const).map((key) => (
                  <div className="meter-row" key={key}>
                    <span className="m-label">{key}</span>
                    <span className="meter">
                      <span style={{ width: `${Math.round((breakdown[key] ?? 0) * 100)}%` }} />
                    </span>
                    <span className="m-value">
                      {(breakdown[key] * 100).toFixed(0)}
                      <span style={{ color: "var(--text-faint)" }}>
                        {" ×"}{breakdown.weights[key]?.toFixed(2)}
                      </span>
                    </span>
                  </div>
                ))}
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
            <Row k="Volume 1h" v={gpShort(d?.vol_1h)} />
            <Row k="Volume 24h" v={gpShort(d?.vol_24h)} />
            <Row
              k="Time to fill limit"
              v={duration(d?.est_fill_hours)}
              hint="Assumes you capture about a quarter of one side's flow"
            />
            <Row
              k="Margin stability"
              v={d?.margin_stability == null ? "--" : pct(d.margin_stability, 0, false)}
              hint="Share of the last 24 hours where this flip was profitable"
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
