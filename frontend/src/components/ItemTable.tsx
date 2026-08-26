import { Link } from "react-router-dom";

import type { ItemRow } from "../types";
import { clsx, duration, gp, gpShort, num, pct, tone } from "../lib/format";

export type ColumnKey =
  | "score" | "buy" | "sell" | "margin" | "roi" | "profit" | "vol24" | "vol1h"
  | "change1h" | "change24h" | "change7d" | "limit" | "rsi" | "fill" | "stability"
  | "volatility" | "signal" | "age" | "affordable";

interface Column {
  key: ColumnKey;
  label: string;
  title?: string;
  sortKey?: string;
  render: (row: ItemRow) => React.ReactNode;
}

const scoreClass = (score: number | null | undefined) =>
  !score ? "s-low" : score >= 70 ? "s-high" : score >= 45 ? "s-mid" : "s-low";

export const COLUMNS: Record<ColumnKey, Column> = {
  score: {
    key: "score", label: "Score", sortKey: "score",
    title: "Composite of post-tax ROI, gp per cycle, liquidity, stability and freshness",
    render: (r) => (
      <span className={clsx("score-pill", scoreClass(r.flip_score))}>
        {r.flip_score ? r.flip_score.toFixed(0) : "--"}
      </span>
    ),
  },
  buy: {
    key: "buy", label: "Buy at", title: "Instant-sell price: what you can likely buy for",
    render: (r) => <span className="mono">{gp(r.low)}</span>,
  },
  sell: {
    key: "sell", label: "Sell at", sortKey: "price",
    title: "Instant-buy price: what you can likely sell for",
    render: (r) => <span className="mono">{gp(r.high)}</span>,
  },
  margin: {
    key: "margin", label: "Margin", sortKey: "margin", title: "Profit per unit after tax",
    render: (r) => <span className={clsx("mono", tone(r.margin))}>{gp(r.margin, { sign: true })}</span>,
  },
  roi: {
    key: "roi", label: "ROI", sortKey: "roi", title: "Post-tax return on the coins tied up",
    render: (r) => <span className={clsx("mono", tone(r.roi))}>{pct(r.roi)}</span>,
  },
  profit: {
    key: "profit", label: "gp / cycle", sortKey: "profit",
    title: "Profit if you fill a realistic quantity within one 4 hour buy limit",
    render: (r) => (
      <span className={clsx("mono", tone(r.potential_profit))}>{gpShort(r.potential_profit)}</span>
    ),
  },
  vol24: {
    key: "vol24", label: "Vol 24h", sortKey: "volume", title: "Units traded in the last 24 hours",
    render: (r) => <span className="mono">{gpShort(r.vol_24h)}</span>,
  },
  vol1h: {
    key: "vol1h", label: "Vol 1h", title: "Units traded in the last hour",
    render: (r) => <span className="mono">{gpShort(r.vol_1h)}</span>,
  },
  change1h: {
    key: "change1h", label: "1h", sortKey: "change_1h",
    render: (r) => <span className={clsx("mono", tone(r.price_change_1h))}>{pct(r.price_change_1h, 1)}</span>,
  },
  change24h: {
    key: "change24h", label: "24h", sortKey: "change_24h",
    render: (r) => <span className={clsx("mono", tone(r.price_change_24h))}>{pct(r.price_change_24h, 1)}</span>,
  },
  change7d: {
    key: "change7d", label: "7d",
    render: (r) => <span className={clsx("mono", tone(r.price_change_7d))}>{pct(r.price_change_7d, 1)}</span>,
  },
  limit: {
    key: "limit", label: "Limit", title: "Units buyable per 4 hours",
    render: (r) => <span className="mono">{r.buy_limit ? num(r.buy_limit) : "--"}</span>,
  },
  rsi: {
    key: "rsi", label: "RSI",
    title: "Relative strength over recent hours. Under 30 is oversold, over 70 overbought",
    render: (r) => (
      <span className={clsx("mono", r.rsi_14 == null ? "flat" : r.rsi_14 > 70 ? "down" : r.rsi_14 < 30 ? "up" : "flat")}>
        {r.rsi_14 == null ? "--" : r.rsi_14.toFixed(0)}
      </span>
    ),
  },
  fill: {
    key: "fill", label: "Fill", title: "Estimated time to buy a full limit at current flow",
    render: (r) => <span className="mono">{duration(r.est_fill_hours)}</span>,
  },
  stability: {
    key: "stability", label: "Stable",
    title: "Share of the last 24 hours where this flip was profitable",
    render: (r) => (
      <span className={clsx("mono", (r.margin_stability ?? 0) > 0.7 ? "up" : (r.margin_stability ?? 0) < 0.4 ? "down" : "flat")}>
        {r.margin_stability == null ? "--" : `${(r.margin_stability * 100).toFixed(0)}%`}
      </span>
    ),
  },
  volatility: {
    key: "volatility", label: "Vol σ", sortKey: "volatility",
    title: "Standard deviation of hourly returns over 24 hours",
    render: (r) => <span className="mono">{r.volatility_24h == null ? "--" : (r.volatility_24h * 100).toFixed(2)}</span>,
  },
  signal: {
    key: "signal", label: "Signal",
    render: (r) => (
      <span className={clsx("tag", r.signal === "breakout" ? "breakout" : "thin")} title={r.signal_note}>
        {r.signal ?? "--"}
      </span>
    ),
  },
  age: {
    key: "age", label: "Age", title: "Seconds since this quote was last seen on the exchange",
    render: (r) => (
      <span className={clsx("mono", (r.data_age_seconds ?? 0) > 900 ? "down" : "flat")}>
        {r.data_age_seconds == null ? "--" : r.data_age_seconds < 90 ? `${r.data_age_seconds}s` : `${Math.round(r.data_age_seconds / 60)}m`}
      </span>
    ),
  },
  affordable: {
    key: "affordable", label: "You can buy",
    title: "Quantity your capital covers, capped by the buy limit",
    render: (r) => (
      <span className="mono">
        {r.affordable_quantity == null ? "--" : num(r.affordable_quantity)}
        {r.affordable_profit ? <span className="up"> ({gpShort(r.affordable_profit)})</span> : null}
      </span>
    ),
  },
};

interface Props {
  rows: ItemRow[];
  columns: ColumnKey[];
  sort?: string;
  onSort?: (sortKey: string) => void;
  emptyTitle?: string;
  emptyBody?: string;
  action?: (row: ItemRow) => React.ReactNode;
}

export default function ItemTable({
  rows, columns, sort, onSort, emptyTitle = "Nothing to show", emptyBody, action,
}: Props) {
  if (!rows.length) {
    return (
      <div className="empty">
        <h3>{emptyTitle}</h3>
        {emptyBody && <p>{emptyBody}</p>}
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            {columns.map((key) => {
              const col = COLUMNS[key];
              const sortable = Boolean(onSort && col.sortKey);
              return (
                <th
                  key={key}
                  title={col.title}
                  className={clsx(sortable && "sortable", sort && col.sortKey === sort && "sorted")}
                  onClick={sortable ? () => onSort!(col.sortKey!) : undefined}
                >
                  {col.label}
                  {sort && col.sortKey === sort ? " ↓" : ""}
                </th>
              );
            })}
            {action && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <Link to={`/item/${row.id}`} className="item-cell">
                  {row.icon_url && <img src={row.icon_url} alt="" loading="lazy" />}
                  <span>
                    <span className="name">{row.name}</span>
                    <span className="meta">
                      {row.members ? "P2P" : "F2P"}
                      {row.tax_exempt ? " · tax free" : ""}
                    </span>
                  </span>
                </Link>
              </td>
              {columns.map((key) => (
                <td key={key} className="num">{COLUMNS[key].render(row)}</td>
              ))}
              {action && <td>{action(row)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
