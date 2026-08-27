import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { clsx, duration, gp, gpShort, num, pct } from "../lib/format";
import type { AllocatorPlan } from "../types";

const PRESET_BANKROLLS = [1_000_000, 10_000_000, 50_000_000, 200_000_000, 1_000_000_000];

export default function Allocator() {
  const queryClient = useQueryClient();
  const [bankroll, setBankroll] = useState("50000000");
  const [slots, setSlots] = useState(8);
  const [minVolume, setMinVolume] = useState("2000");
  const [minScore, setMinScore] = useState("30");
  const [maxShare, setMaxShare] = useState("35");
  const [plan, setPlan] = useState<AllocatorPlan | null>(null);

  const prefs = useQuery({ queryKey: ["allocator-prefs"], queryFn: api.allocatorPrefs });

  const solve = useMutation({
    mutationFn: () =>
      api.allocate({
        bankroll: Number(bankroll),
        slots,
        min_volume: Number(minVolume) || 0,
        min_score: Number(minScore) || 0,
        max_share: Number(maxShare) / 100,
      }),
    onSuccess: setPlan,
  });

  const setPref = useMutation({
    mutationFn: ({ id, mode }: { id: number; mode: "pin" | "exclude" }) =>
      api.setAllocatorPref(id, mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["allocator-prefs"] });
      solve.mutate();
    },
  });

  const clearPref = useMutation({
    mutationFn: (id: number) => api.clearAllocatorPref(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["allocator-prefs"] });
      solve.mutate();
    },
  });

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Slot allocator</h1>
        <span className="sub">
          What to actually buy with the gold and slots you have, not just what ranks highest
        </span>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Your position</h2>
          <span className="hint">
            {plan ? `${num(plan.candidates_considered)} items considered` : "set it up and solve"}
          </span>
        </div>
        <div className="card-body">
          <div className="controls">
            <div className="field">
              <label>Bankroll (gp)</label>
              <input
                type="number"
                value={bankroll}
                onChange={(e) => setBankroll(e.target.value)}
                style={{ width: 160 }}
              />
            </div>
            <div className="field">
              <label>Slots</label>
              <div className="seg">
                {[3, 8].map((n) => (
                  <button key={n} className={slots === n ? "active" : ""} onClick={() => setSlots(n)}>
                    {n === 3 ? "3 (f2p)" : "8 (members)"}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>Min 24h volume</label>
              <input type="number" value={minVolume} onChange={(e) => setMinVolume(e.target.value)} />
            </div>
            <div className="field">
              <label>Min score</label>
              <input type="number" value={minScore} onChange={(e) => setMinScore(e.target.value)} />
            </div>
            <div className="field">
              <label>Max per item %</label>
              <input
                type="number"
                value={maxShare}
                onChange={(e) => setMaxShare(e.target.value)}
                title="Diversification cap: no single item may absorb more than this share of the bankroll"
              />
            </div>
            <button className="btn primary" disabled={solve.isPending} onClick={() => solve.mutate()}>
              {solve.isPending ? "Solving..." : "Solve"}
            </button>
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 12, flexWrap: "wrap" }}>
            {PRESET_BANKROLLS.map((b) => (
              <button key={b} className="btn small" onClick={() => setBankroll(String(b))}>
                {gpShort(b)}
              </button>
            ))}
          </div>
        </div>
      </div>

      {plan && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div className="card stat">
              <div className="label">Expected profit</div>
              <div className="value up">{gp(plan.expected_profit, { sign: true })}</div>
              <div className="foot">per 4 hour cycle, after tax</div>
            </div>
            <div className="card stat">
              <div className="label">Capital deployed</div>
              <div className="value">{gpShort(plan.capital_used)}</div>
              <div className="foot">
                {pct(plan.capital_used / Math.max(plan.bankroll, 1), 0, false)} of bankroll
              </div>
            </div>
            <div className="card stat">
              <div className="label">Return on deployed</div>
              <div className="value gold">{pct(plan.expected_return, 2, false)}</div>
              <div className="foot">per cycle</div>
            </div>
            <div className="card stat">
              <div className="label">Slots used</div>
              <div className="value">{plan.slots_used} / {plan.slots}</div>
              <div className="foot">{gpShort(plan.capital_idle)} gp idle</div>
            </div>
          </div>

          {plan.notes.map((n) => (
            <div className="banner" key={n}>{n}</div>
          ))}

          <div className="card">
            <div className="card-head">
              <h2>The basket</h2>
              <span className="hint">pin to force an item in, exclude to rule it out</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Item</th><th>Buy at</th><th>Qty</th><th>Capital</th>
                    <th>Margin</th><th>ROI</th><th>Profit</th><th>Fill</th><th>Score</th><th />
                  </tr>
                </thead>
                <tbody>
                  {plan.allocations.map((a) => (
                    <tr key={a.item_id}>
                      <td>
                        <Link to={`/item/${a.item_id}`} className="item-cell">
                          {a.icon_url && <img src={a.icon_url} alt="" loading="lazy" />}
                          <span>
                            <span className="name">{a.name}</span>
                            <span className="meta">
                              {a.pinned ? "pinned · " : ""}
                              limit {a.buy_limit ? num(a.buy_limit) : "none"}
                            </span>
                          </span>
                        </Link>
                      </td>
                      <td className="num">{gp(a.price)}</td>
                      <td className="num">{num(a.quantity)}</td>
                      <td className="num">{gpShort(a.capital)}</td>
                      <td className="num up">{gp(a.margin, { sign: true })}</td>
                      <td className="num">{pct(a.roi)}</td>
                      <td className="num up">{gpShort(a.profit)}</td>
                      <td className="num">{duration(a.est_fill_hours)}</td>
                      <td className="num">
                        <span className={clsx("score-pill", a.score >= 70 ? "s-high" : a.score >= 45 ? "s-mid" : "s-low")}>
                          {a.score.toFixed(0)}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn small"
                          onClick={() => setPref.mutate({ id: a.item_id, mode: a.pinned ? "exclude" : "pin" })}
                        >
                          {a.pinned ? "unpin" : "pin"}
                        </button>{" "}
                        <button
                          className="btn ghost small"
                          onClick={() => setPref.mutate({ id: a.item_id, mode: "exclude" })}
                        >
                          exclude
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {(prefs.data?.results.length ?? 0) > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head"><h2>Pins and exclusions</h2></div>
          <div className="card-body" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {prefs.data!.results.map((p) => (
              <span key={p.item_id} className="tag" style={{ padding: "4px 8px" }}>
                {p.mode === "pin" ? "📌" : "🚫"} {p.name}
                <button
                  className="btn ghost small"
                  style={{ marginLeft: 6 }}
                  onClick={() => clearPref.mutate(p.item_id)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {!plan && !solve.isPending && (
        <div className="card">
          <div className="empty">
            <h3>Nothing solved yet</h3>
            <p>
              Set your bankroll and slot count, then hit Solve. The allocator maximises
              expected post-tax profit per 4 hour cycle subject to buy limits, your capital,
              and a per-item diversification cap.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
