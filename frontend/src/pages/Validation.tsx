import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { clsx, gp, gpShort, num, pct } from "../lib/format";

const HORIZONS = ["1h", "4h", "24h"];

/** Bar whose width is relative to the largest value in the column. */
function Bar({ value, max }: { value: number; max: number }) {
  const positive = value >= 0;
  const width = max > 0 ? Math.min(Math.abs(value) / max, 1) * 100 : 0;
  return (
    <span className="dbar">
      <span
        className={positive ? "dbar-pos" : "dbar-neg"}
        style={{ width: `${width}%` }}
      />
    </span>
  );
}

export default function Validation() {
  const [horizon, setHorizon] = useState("4h");
  const [days, setDays] = useState(7);

  const deciles = useQuery({
    queryKey: ["validation-deciles", horizon, days],
    queryFn: () => api.validationDeciles(horizon, days),
  });
  const summary = useQuery({
    queryKey: ["validation-summary", days],
    queryFn: () => api.validationSummary(days),
  });

  const rows = deciles.data?.deciles ?? [];
  const verdict = deciles.data?.verdict;
  const maxProfit = Math.max(...rows.map((r) => Math.abs(r.median_cycle_profit ?? 0)), 1);

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Score validation</h1>
        <span className="sub">
          Did the score actually predict anything? Measured, not asserted.
        </span>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-body" style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
          <div className="field">
            <label>Holding period</label>
            <div className="seg">
              {HORIZONS.map((h) => (
                <button key={h} className={horizon === h ? "active" : ""} onClick={() => setHorizon(h)}>
                  {h}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label>Lookback</label>
            <div className="seg">
              {[3, 7, 14, 30].map((d) => (
                <button key={d} className={days === d ? "active" : ""} onClick={() => setDays(d)}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
          <div style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-faint)" }}>
            {summary.data
              ? `${num(summary.data.snapshots_total)} snapshots · ${num(summary.data.snapshots_awaiting_grade)} awaiting grade`
              : "loading"}
          </div>
        </div>
      </div>

      {verdict && (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <div className="card stat">
            <div className="label">Top decile</div>
            <div className="value up">{gpShort(verdict.top_decile_avg_cycle_profit)}</div>
            <div className="foot">avg gp per cycle, after tax</div>
          </div>
          <div className="card stat">
            <div className="label">Bottom decile</div>
            <div className="value down">{gpShort(verdict.bottom_decile_avg_cycle_profit)}</div>
            <div className="foot">avg gp per cycle, after tax</div>
          </div>
          <div className="card stat">
            <div className="label">Win rate spread</div>
            <div className="value">
              {pct(verdict.bottom_win_rate, 0, false)} → {pct(verdict.top_win_rate, 0, false)}
            </div>
            <div className="foot">share of flips that ended profitable</div>
          </div>
          <div className="card stat">
            <div className="label">Verdict</div>
            <div className={clsx("value", verdict.top_beats_bottom ? "up" : "down")}>
              {verdict.top_beats_bottom ? "predictive" : "not predictive"}
            </div>
            <div className="foot">
              best decile by median profit: #{verdict.best_decile_by_median_profit}
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Realised outcome by score decile</h2>
          <span className="hint">
            what a flip entered at each snapshot actually returned {horizon} later
          </span>
        </div>
        {rows.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Decile</th>
                  <th>Score range</th>
                  <th>Samples</th>
                  <th title="Per unit, after tax">Median margin</th>
                  <th title="Per unit margin times the quantity you could realistically buy in one 4h cycle">
                    Median gp / cycle
                  </th>
                  <th />
                  <th>Win rate</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.decile}>
                    <td className="mono">{r.decile}</td>
                    <td className="num">{r.score_min?.toFixed(1)} – {r.score_max?.toFixed(1)}</td>
                    <td className="num flat">{num(r.samples)}</td>
                    <td className={clsx("num", (r.median_realised_margin ?? 0) >= 0 ? "up" : "down")}>
                      {gp(r.median_realised_margin, { sign: true })}
                    </td>
                    <td className={clsx("num", (r.median_cycle_profit ?? 0) >= 0 ? "up" : "down")}>
                      {gpShort(r.median_cycle_profit, { sign: true })}
                    </td>
                    <td style={{ width: 190 }}>
                      <Bar value={r.median_cycle_profit ?? 0} max={maxProfit} />
                    </td>
                    <td className="num">{pct(r.win_rate, 1, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            <h3>{deciles.isLoading ? "Loading..." : "No graded snapshots yet"}</h3>
            <p>
              Scores are snapshotted hourly and graded once the holding period has elapsed.
              Come back after an hour or two.
            </p>
          </div>
        )}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head"><h2>Coverage</h2></div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Horizon</th><th>Samples</th><th>Items</th><th title="Correlation of score with realised gp per cycle">corr</th></tr>
              </thead>
              <tbody>
                {summary.data?.horizons.map((h) => (
                  <tr key={h.horizon}>
                    <td className="mono">{h.horizon}</td>
                    <td className="num">{num(h.samples)}</td>
                    <td className="num">{num(h.items)}</td>
                    <td className="num">{h.score_cycle_corr?.toFixed(3) ?? "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h2>How to read this</h2></div>
          <div className="card-body" style={{ fontSize: 12.5, color: "var(--text-dim)", lineHeight: 1.65 }}>
            <p style={{ marginTop: 0 }}>
              Every hour the scoreboard is frozen. Once the holding period passes, each
              frozen row is graded against what the market actually did: buy at the
              instant-sell price recorded then, exit at the item's average instant-buy
              price one period later, minus tax.
            </p>
            <p>
              <strong style={{ color: "var(--text)" }}>Judge it on gp per cycle, not per unit.</strong>{" "}
              A 1gp margin on an item with a 30,000 buy limit beats a 70k margin on one
              with a limit of 8, and per-unit figures hide that completely.
            </p>
            <p>
              Deciles scoring 0 are items with no post-tax edge at the time. They are
              included deliberately: they are what the model says to avoid, and they
              should lose money here.
            </p>
            {deciles.data?.sources && (
              <p style={{ marginBottom: 0 }}>
                Sources: {Object.entries(deciles.data.sources)
                  .map(([k, v]) => `${num(v)} ${k}`)
                  .join(", ")}. Reconstructed rows were rebuilt from stored candles for
                hours predating this install; they use hourly averages instead of live
                quotes and cannot recover quote freshness, so treat them as indicative.
              </p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
