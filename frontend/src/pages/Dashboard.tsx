import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import ItemTable from "../components/ItemTable";
import { ago, gpShort, num, pct } from "../lib/format";

function Stat({ label, value, foot, tone }: {
  label: string; value: string; foot?: string; tone?: string;
}) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [moverWindow, setMoverWindow] = useState("24h");

  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const top = useQuery({
    queryKey: ["dash-top"],
    queryFn: () => api.scanner({ limit: 10, min_volume: 1000, min_margin: 1, sort: "score" }),
  });
  const gainers = useQuery({
    queryKey: ["movers", moverWindow, "up"],
    queryFn: () => api.movers(moverWindow, "up", 8),
  });
  const losers = useQuery({
    queryKey: ["movers", moverWindow, "down"],
    queryFn: () => api.movers(moverWindow, "down", 8),
  });
  const unusual = useQuery({ queryKey: ["unusual"], queryFn: () => api.unusual(8) });

  const s = summary.data;

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Market overview</h1>
        <span className="sub">
          {s ? `updated ${ago(s.last_poll)} · ${num(s.candles)} candles stored` : "loading market data"}
        </span>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat
          label="Items tracked"
          value={s ? num(s.tracked) : "--"}
          foot={s ? `${num(s.fresh)} quoted in the last 5 min · ${num(s.crossed)} crossed` : undefined}
        />
        <Stat
          label="Profitable after tax"
          value={s ? num(s.profitable) : "--"}
          foot={s ? `${pct(s.profitable / Math.max(s.tracked, 1), 0, false)} of the market · median ROI ${pct(s.median_roi, 1, false)}` : undefined}
          tone="gold"
        />
        <Stat
          label="Volume traded 24h"
          value={s ? gpShort(s.volume_24h) : "--"}
          foot="units across every tracked item"
        />
        <Stat
          label="Sale tax"
          value={s ? pct(s.tax_policy.rate, 0, false) : "--"}
          foot={s ? `free under ${s.tax_policy.free_below}gp · capped at ${gpShort(s.tax_policy.cap)}` : undefined}
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Best flips right now</h2>
          <Link to="/scanner" className="btn small">Open scanner →</Link>
        </div>
        <ItemTable
          rows={top.data?.results ?? []}
          columns={["score", "month", "buy", "sell", "margin", "breakeven", "roi", "profit", "vol24", "limit", "fill"]}
          emptyTitle={top.isLoading ? "Loading market data..." : "No profitable flips found"}
          emptyBody="If this persists, price history is probably still building."
        />
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-head">
            <h2>Gainers</h2>
            <div className="seg">
              {["1h", "24h", "7d"].map((w) => (
                <button
                  key={w}
                  className={moverWindow === w ? "active" : ""}
                  onClick={() => setMoverWindow(w)}
                >
                  {w}
                </button>
              ))}
            </div>
          </div>
          <ItemTable
            rows={gainers.data?.results ?? []}
            columns={["sell", "change1h", "change24h", "vol24"]}
            emptyTitle="No movers yet"
            emptyBody="Needs a full window of price history."
          />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Fallers</h2>
            <span className="hint">window: {moverWindow}</span>
          </div>
          <ItemTable
            rows={losers.data?.results ?? []}
            columns={["sell", "change1h", "change24h", "vol24"]}
            emptyTitle="No movers yet"
            emptyBody="Needs a full window of price history."
          />
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Unusual activity</h2>
          <span className="hint">
            price or volume outside this item's own recent normal
          </span>
        </div>
        <ItemTable
          rows={unusual.data?.results ?? []}
          columns={["signal", "sell", "change24h", "vol1h", "vol24", "volatility"]}
          emptyTitle="Nothing unusual"
          emptyBody="Every tracked item is trading inside its normal range."
        />
      </div>
    </>
  );
}
