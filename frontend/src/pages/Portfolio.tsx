import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { ago, clsx, gp, gpShort, num, pct, tone } from "../lib/format";
import type { ItemRow } from "../types";

function TradeForm({ onDone }: { onDone: () => void }) {
  const [term, setTerm] = useState("");
  const [picked, setPicked] = useState<ItemRow | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");

  const search = useQuery({
    queryKey: ["trade-search", term],
    queryFn: () => api.search(term, 6),
    enabled: term.length > 1 && !picked,
    refetchInterval: false,
  });

  const add = useMutation({
    mutationFn: () =>
      api.addTrade({
        item_id: picked!.id,
        side,
        quantity: Number(quantity),
        price: Number(price),
      }),
    onSuccess: () => {
      setPicked(null); setTerm(""); setQuantity(""); setPrice("");
      onDone();
    },
  });

  return (
    <div className="card-body">
      <div className="inline-form">
        <div className="field" style={{ position: "relative", minWidth: 220 }}>
          <label>Item</label>
          <input
            value={picked ? picked.name : term}
            placeholder="Search an item"
            onChange={(e) => { setPicked(null); setTerm(e.target.value); }}
            style={{ width: "100%", fontFamily: "var(--sans)" }}
          />
          {!picked && (search.data?.results.length ?? 0) > 0 && (
            <div
              className="card"
              style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30, marginTop: 4 }}
            >
              {search.data!.results.map((r) => (
                <div
                  key={r.id}
                  className="palette-row"
                  onClick={() => {
                    setPicked(r);
                    if (!price) setPrice(String(side === "buy" ? r.low ?? "" : r.high ?? ""));
                  }}
                >
                  {r.icon_url && <img src={r.icon_url} alt="" />}
                  <span>{r.name}</span>
                  <span className="right">{gpShort(r.high)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="field">
          <label>Side</label>
          <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")}>
            <option value="buy">buy</option>
            <option value="sell">sell</option>
          </select>
        </div>
        <div className="field">
          <label>Quantity</label>
          <input type="number" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </div>
        <div className="field">
          <label>Price each</label>
          <input type="number" value={price} onChange={(e) => setPrice(e.target.value)} />
        </div>
        <button
          className="btn primary"
          disabled={!picked || !quantity || !price || add.isPending}
          onClick={() => add.mutate()}
        >
          Log trade
        </button>
      </div>
      <div style={{ marginTop: 9, fontSize: 11.5, color: "var(--text-faint)" }}>
        Sales record the tax owed at the moment you log them. Sells are matched against your
        oldest open buys, so realised profit reflects what you actually paid.
      </div>
    </div>
  );
}

export default function Portfolio() {
  const queryClient = useQueryClient();
  const portfolio = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolio });
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => api.trades() });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    queryClient.invalidateQueries({ queryKey: ["trades"] });
  };
  const removeTrade = useMutation({ mutationFn: api.deleteTrade, onSuccess: invalidate });

  const t = portfolio.data?.totals;

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Portfolio</h1>
        <span className="sub">Tax-aware profit and loss on the flips you have logged</span>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="card stat">
          <div className="label">Total P&amp;L</div>
          <div className={`value ${tone(t?.total)}`}>{gp(t?.total, { sign: true })}</div>
          <div className="foot">{t?.return_pct != null ? `${pct(t.return_pct)} on capital` : "no trades yet"}</div>
        </div>
        <div className="card stat">
          <div className="label">Realised</div>
          <div className={`value ${tone(t?.realised)}`}>{gp(t?.realised, { sign: true })}</div>
          <div className="foot">{gp(t?.tax_paid)} gp paid in tax</div>
        </div>
        <div className="card stat">
          <div className="label">Unrealised</div>
          <div className={`value ${tone(t?.unrealised)}`}>{gp(t?.unrealised, { sign: true })}</div>
          <div className="foot">marked at current sell price, after tax</div>
        </div>
        <div className="card stat">
          <div className="label">Capital deployed</div>
          <div className="value">{gpShort(t?.capital_deployed)}</div>
          <div className="foot">{t?.open_positions ?? 0} open positions</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>Log a trade</h2></div>
        <TradeForm onDone={invalidate} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Positions</h2>
          <span className="hint">FIFO cost basis</span>
        </div>
        {portfolio.data?.positions.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th><th>Open</th><th>Avg cost</th><th>Now</th>
                  <th>Realised</th><th>Unrealised</th><th>Total</th><th>Tax</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.data.positions.map((p) => (
                  <tr key={p.item_id}>
                    <td>
                      <Link to={`/item/${p.item_id}`} className="item-cell">
                        {p.icon_url && <img src={p.icon_url} alt="" loading="lazy" />}
                        <span>
                          <span className="name">{p.name}</span>
                          {p.unmatched_sales > 0 && (
                            <span className="meta">{num(p.unmatched_sales)} sold with no logged buy</span>
                          )}
                        </span>
                      </Link>
                    </td>
                    <td className="num">{num(p.open_quantity)}</td>
                    <td className="num">{gp(p.avg_cost)}</td>
                    <td className="num">{gp(p.high)}</td>
                    <td className={clsx("num", tone(p.realised))}>{gp(p.realised, { sign: true })}</td>
                    <td className={clsx("num", tone(p.unrealised))}>{gp(p.unrealised, { sign: true })}</td>
                    <td className={clsx("num", tone(p.total))}>{gp(p.total, { sign: true })}</td>
                    <td className="num flat">{gp(p.tax_paid)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            <h3>No positions yet</h3>
            <p>Log a buy above to start tracking profit and loss.</p>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Trade log</h2>
          <span className="hint">{trades.data?.results.length ?? 0} entries</span>
        </div>
        {trades.data?.results.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th><th>Side</th><th>Qty</th><th>Price</th>
                  <th>Tax</th><th>Value</th><th>When</th><th />
                </tr>
              </thead>
              <tbody>
                {trades.data.results.map((tr) => (
                  <tr key={tr.id}>
                    <td>
                      <Link to={`/item/${tr.item_id}`} className="item-cell">
                        {tr.icon_url && <img src={tr.icon_url} alt="" loading="lazy" />}
                        <span className="name">{tr.name}</span>
                      </Link>
                    </td>
                    <td className={clsx("num", tr.side === "buy" ? "down" : "up")}>{tr.side}</td>
                    <td className="num">{num(tr.quantity)}</td>
                    <td className="num">{gp(tr.price)}</td>
                    <td className="num flat">{gp(tr.tax_paid)}</td>
                    <td className="num">{gpShort(tr.quantity * tr.price)}</td>
                    <td className="num flat">{ago(tr.executed_at)}</td>
                    <td>
                      <button className="btn ghost small" onClick={() => removeTrade.mutate(tr.id)}>
                        delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty"><h3>No trades logged</h3><p>Your ledger is empty.</p></div>
        )}
      </div>
    </>
  );
}
