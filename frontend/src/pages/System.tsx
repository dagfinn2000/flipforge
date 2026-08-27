import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { ago, clsx, num, pct } from "../lib/format";

function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "--";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} kB`;
  return `${n} B`;
}

function seconds(n: number | null | undefined): string {
  if (n === null || n === undefined) return "--";
  if (n < 90) return `${n}s`;
  if (n < 5400) return `${Math.round(n / 60)}m`;
  if (n < 172800) return `${Math.round(n / 3600)}h`;
  return `${Math.round(n / 86400)}d`;
}

/** A heartbeat reads green while it is on schedule and red once overdue. */
function Beat({ label, value, limit }: {
  label: string; value: number | null | undefined; limit: number;
}) {
  const late = value === null || value === undefined || value > limit;
  return (
    <div className="kv">
      <span className="k">{label}</span>
      <span className={clsx("v", late ? "down" : "up")}>{seconds(value)}</span>
    </div>
  );
}

export default function System() {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const config = useQuery({ queryKey: ["sysconfig"], queryFn: api.config });
  const gaps = useQuery({ queryKey: ["gaps"], queryFn: api.gaps, refetchInterval: 60_000 });
  const storage = useQuery({ queryKey: ["storage"], queryFn: api.storage });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["gaps"] });
    queryClient.invalidateQueries({ queryKey: ["storage"] });
  };
  const fill = useMutation({ mutationFn: api.fillGaps, onSuccess: refresh });
  const clean = useMutation({ mutationFn: api.cleanup, onSuccess: refresh });

  const h = health.data;
  const s = storage.data;
  const largest = s?.tables[0]?.bytes ?? 1;
  const steps = Object.entries(gaps.data?.steps ?? {});
  const worstCoverage = steps.length
    ? Math.min(...steps.map(([, g]) => g.coverage ?? 1))
    : null;

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>System</h1>
        <span className="sub">Whether the data behind every other page is actually healthy</span>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="card stat">
          <div className="label">Status</div>
          <div className={clsx("value", h?.status === "ok" ? "up" : "down")}>{h?.status ?? "--"}</div>
          <div className="foot">up {seconds(h?.uptime_seconds)}</div>
        </div>
        <div className="card stat">
          <div className="label">Items tracked</div>
          <div className="value">{num(h?.items)}</div>
          <div className="foot">{h?.ws_clients ?? 0} live connection(s)</div>
        </div>
        <div className="card stat">
          <div className="label">Database</div>
          <div className="value">{bytes(s?.total_bytes)}</div>
          <div className="foot">cleaned {s?.last_maintenance ? ago(s.last_maintenance) : "never"}</div>
        </div>
        <div className="card stat">
          <div className="label">History coverage</div>
          <div className="value gold">{pct(worstCoverage, 1, false)}</div>
          <div className="foot">worst of the tracked resolutions</div>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-head">
            <h2>Ingest heartbeat</h2>
            <span className="hint">green means on schedule</span>
          </div>
          <div className="card-body">
            <Beat label="Since last price poll" value={h?.seconds_since_price_poll} limit={180} />
            <Beat label="Since last rollup" value={h?.seconds_since_metrics} limit={180} />
            <div className="kv">
              <span className="k">Initial backfill</span>
              <span className={clsx("v", h?.backfill_complete ? "up" : "flat")}>
                {h?.backfill_complete ? "complete" : "running"}
              </span>
            </div>
            <div className="kv">
              <span className="k">Upstream</span>
              <span className="v" style={{ fontSize: 11 }}>{config.data?.source}</span>
            </div>
            <div className="kv">
              <span className="k">Identifying as</span>
              <span className="v" style={{ fontSize: 11 }}>{config.data?.user_agent}</span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Tax policy in force</h2>
            <span className="hint">every margin in the app follows this</span>
          </div>
          <div className="card-body">
            <div className="kv">
              <span className="k">Rate</span>
              <span className="v">{pct(h?.tax_policy.rate, 0, false)} of the sale price</span>
            </div>
            <div className="kv">
              <span className="k">Cap</span>
              <span className="v">{num(h?.tax_policy.cap)} gp per item</span>
            </div>
            <div className="kv">
              <span className="k">Untaxed below</span>
              <span className="v">{h?.tax_policy.free_below} gp</span>
            </div>
            <div className="kv">
              <span className="k">Exempt items</span>
              <span className="v">{num(h?.tax_policy.exempt_count)}</span>
            </div>
            <p style={{ fontSize: 11.5, color: "var(--text-faint)", margin: "10px 0 0" }}>
              The untaxed threshold is derived from the rate rather than configured: tax
              floors per item, so it sits wherever a percent of the price first reaches
              one coin.
            </p>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>History coverage</h2>
          <button className="btn" disabled={fill.isPending} onClick={() => fill.mutate()}>
            {fill.isPending ? "Repairing..." : "Repair gaps now"}
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Resolution</th><th>Covering</th><th>Windows</th>
                <th>Missing</th><th>Coverage</th><th>Oldest gap</th>
              </tr>
            </thead>
            <tbody>
              {steps.map(([step, g]) => (
                <tr key={step}>
                  <td className="mono">{step}</td>
                  <td className="num">{g.covering_hours}h</td>
                  <td className="num">{num(g.windows_considered)}</td>
                  <td className={clsx("num", g.missing_windows ? "down" : "up")}>
                    {num(g.missing_windows)}
                  </td>
                  <td className={clsx("num", (g.coverage ?? 1) >= 0.999 ? "up" : "flat")}>
                    {pct(g.coverage, 2, false)}
                  </td>
                  <td className="num flat">{g.oldest_gap ? ago(g.oldest_gap) : "none"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-body" style={{ fontSize: 11.5, color: "var(--text-faint)", paddingTop: 10 }}>
          Downtime leaves holes in the bulk price series. An hourly pass refetches them,
          newest first, up to {num(gaps.data?.max_requests_per_pass)} upstream requests per
          pass. {num(gaps.data?.known_thin_windows)} window(s) came back genuinely thin and
          are not asked about again. Last run{" "}
          {gaps.data?.last_run ? ago(gaps.data.last_run) : "never"}.
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Storage</h2>
          <button className="btn" disabled={clean.isPending} onClick={() => clean.mutate()}>
            {clean.isPending ? "Cleaning..." : "Run cleanup now"}
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Table</th><th>Rows</th><th>Size</th><th style={{ width: 220 }} /></tr>
            </thead>
            <tbody>
              {s?.tables.map((t) => (
                <tr key={t.table}>
                  <td className="mono">{t.table}</td>
                  <td className="num">{num(t.rows)}</td>
                  <td className="num">{bytes(t.bytes)}</td>
                  <td>
                    <span className="dbar">
                      <span className="dbar-pos" style={{ width: `${(t.bytes / largest) * 100}%` }} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-body" style={{ fontSize: 11.5, color: "var(--text-faint)", paddingTop: 10 }}>
          Retention: 5-minute candles {s?.retention.candles_5m_days}d, hourly{" "}
          {s?.retention.candles_1h_days}d, other resolutions {s?.retention.candles_other_days}d,
          score snapshots {s?.retention.score_snapshots_days}d, outcomes{" "}
          {s?.retention.score_outcomes_days}d. Your trades, watchlist, alert rules and tax
          exemptions are never touched — candles are a cache and can always be refetched.
        </div>
      </div>
    </>
  );
}
