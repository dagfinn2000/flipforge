import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type ScannerFilters } from "../api";
import ItemTable, { type ColumnKey } from "../components/ItemTable";
import { downloadCsv, gpShort, num } from "../lib/format";

/** Starting points for common flipping styles. */
const PRESETS: { name: string; hint: string; filters: ScannerFilters }[] = [
  {
    name: "Balanced",
    hint: "Liquid items with a stable post-tax edge",
    filters: { min_volume: 1000, min_margin: 1, min_roi: 0.01, max_margin_cv: 1.5, sort: "score" },
  },
  {
    name: "High volume",
    hint: "Fast fills, small margins, many cycles",
    filters: { min_volume: 50_000, min_margin: 1, max_fill_hours: 0.5, sort: "score" },
  },
  {
    name: "Big ticket",
    hint: "Expensive items, large gp per flip",
    filters: { min_price: 1_000_000, min_volume: 50, min_margin: 10_000, sort: "profit" },
  },
  {
    name: "Low capital",
    hint: "Everything under 10k a unit",
    filters: { max_price: 10_000, min_volume: 5_000, min_roi: 0.02, sort: "score" },
  },
  {
    name: "Oversold",
    hint: "Sorted by biggest 24h drop -- possible bounce",
    filters: { min_volume: 2_000, min_margin: 1, sort: "change_24h" },
  },
];

// Every margin figure exported is post-tax, same as on screen.
const CSV_COLUMNS = [
  { key: "id", label: "item_id" },
  { key: "name", label: "name" },
  { key: "members", label: "members" },
  { key: "low", label: "buy_at" },
  { key: "high", label: "sell_at" },
  { key: "breakeven_sell", label: "breakeven_sell" },
  { key: "tax", label: "tax_per_unit" },
  { key: "margin", label: "margin_post_tax" },
  { key: "roi", label: "roi" },
  { key: "potential_profit", label: "gp_per_cycle" },
  { key: "buy_limit", label: "buy_limit" },
  { key: "vol_24h", label: "volume_24h" },
  { key: "est_fill_hours", label: "est_fill_hours" },
  { key: "margin_cv", label: "margin_variability" },
  { key: "flip_score", label: "flip_score" },
  { key: "track_score", label: "month_profitability_score" },
  { key: "track_win_rate", label: "month_win_rate" },
  { key: "track_median_profit", label: "month_median_gp_per_cycle" },
  { key: "track_samples", label: "month_graded_flips" },
  { key: "crossed", label: "crossed_quote" },
  { key: "data_age_seconds", label: "quote_age_seconds" },
];

const COLUMNS: ColumnKey[] = [
  "score", "month", "buy", "sell", "margin", "breakeven", "roi", "profit",
  "vol24", "limit", "fill", "steadiness", "rsi", "change24h", "age",
];

export default function Scanner() {
  const [filters, setFilters] = useState<ScannerFilters>(PRESETS[0].filters);
  const [preset, setPreset] = useState("Balanced");
  const [capital, setCapital] = useState<string>("");

  const effective: ScannerFilters = {
    limit: 100,
    ...filters,
    max_capital: capital ? Number(capital) : undefined,
  };

  const { data, isFetching } = useQuery({
    queryKey: ["scanner", effective],
    queryFn: () => api.scanner(effective),
  });

  const set = <K extends keyof ScannerFilters>(key: K, value: ScannerFilters[K]) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPreset("Custom");
  };

  const numberField = (
    label: string, key: keyof ScannerFilters, placeholder: string, step = 1,
  ) => (
    <div className="field" key={key}>
      <label>{label}</label>
      <input
        type="number"
        step={step}
        placeholder={placeholder}
        value={(filters[key] as number | undefined) ?? ""}
        onChange={(e) =>
          set(key, (e.target.value === "" ? undefined : Number(e.target.value)) as never)
        }
      />
    </div>
  );

  const columns = capital ? ([...COLUMNS, "affordable"] as ColumnKey[]) : COLUMNS;

  return (
    <>
      <div className="topbar" style={{ margin: "-20px -22px 20px", position: "static" }}>
        <h1>Flip scanner</h1>
        <span className="sub">
          Every tradeable item, ranked. Every margin here is after the sale tax.
        </span>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Filters</h2>
          <span className="hint">
            {isFetching ? "scanning..." : `${num(data?.count ?? 0)} matches`}
          </span>
        </div>
        <div className="card-body">
          <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 14 }}>
            {PRESETS.map((p) => (
              <button
                key={p.name}
                title={p.hint}
                className={`btn small${preset === p.name ? " active" : ""}`}
                onClick={() => {
                  setFilters(p.filters);
                  setPreset(p.name);
                }}
              >
                {p.name}
              </button>
            ))}
          </div>

          <div className="controls">
            {numberField("Min margin", "min_margin", "1")}
            <div className="field">
              <label>Min ROI %</label>
              <input
                type="number"
                step={0.5}
                placeholder="1"
                value={filters.min_roi != null ? +(filters.min_roi * 100).toFixed(2) : ""}
                onChange={(e) =>
                  set("min_roi", e.target.value === "" ? undefined : Number(e.target.value) / 100)
                }
              />
            </div>
            {numberField("Min 24h volume", "min_volume", "1000")}
            {numberField("Min price", "min_price", "any")}
            {numberField("Max price", "max_price", "any")}
            {numberField("Max fill hours", "max_fill_hours", "any", 0.5)}
            <div className="field">
              <label title="Standard deviation of the spread over its own mean. Lower is steadier.">
                Max margin wobble
              </label>
              <input
                type="number"
                step={0.25}
                placeholder="any"
                value={filters.max_margin_cv ?? ""}
                onChange={(e) =>
                  set("max_margin_cv", e.target.value === "" ? undefined : Number(e.target.value))
                }
              />
            </div>
            <div className="field">
              <label title="Trailing-month realised profitability, from graded flips">
                Min month score
              </label>
              <input
                type="number"
                step={5}
                placeholder="0"
                value={filters.min_track_score ?? ""}
                onChange={(e) =>
                  set("min_track_score", e.target.value === "" ? undefined : Number(e.target.value))
                }
              />
            </div>
            <div className="field">
              <label>Min score</label>
              <input
                type="number"
                step={5}
                placeholder="0"
                value={filters.min_score ?? ""}
                onChange={(e) =>
                  set("min_score", e.target.value === "" ? undefined : Number(e.target.value))
                }
              />
            </div>
            <div className="field">
              <label>Your capital (gp)</label>
              <input
                type="number"
                placeholder="e.g. 50000000"
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
            </div>
            <div className="field">
              <label>Membership</label>
              <select
                value={filters.members === undefined ? "" : String(filters.members)}
                onChange={(e) =>
                  set("members", e.target.value === "" ? undefined : e.target.value === "true")
                }
              >
                <option value="">Both</option>
                <option value="true">Members</option>
                <option value="false">Free to play</option>
              </select>
            </div>
            <div className="field">
              <label>Max quote age</label>
              <select
                value={filters.max_age ?? 3600}
                onChange={(e) => set("max_age", Number(e.target.value))}
              >
                <option value={300}>5 minutes</option>
                <option value={900}>15 minutes</option>
                <option value={3600}>1 hour</option>
                <option value={86400}>1 day</option>
              </select>
            </div>
            <div className="field">
              <label>Crossed quotes</label>
              <select
                value={filters.hide_crossed ? "hide" : "show"}
                onChange={(e) => set("hide_crossed", e.target.value === "hide")}
              >
                <option value="show">Show</option>
                <option value="hide">Hide</option>
              </select>
            </div>
            <button
              className="btn"
              onClick={() => {
                setFilters(PRESETS[0].filters);
                setPreset("Balanced");
                setCapital("");
              }}
            >
              Reset
            </button>
          </div>

          {capital && (
            <div style={{ marginTop: 12, fontSize: 12.5, color: "var(--text-dim)" }}>
              Showing what {gpShort(Number(capital))} gp can actually buy, capped by each
              item's 4 hour limit.
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Results</h2>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="hint">click a column header to re-rank</span>
            <button
              className="btn small"
              disabled={!data?.results.length}
              onClick={() =>
                downloadCsv(
                  `flipforge-scanner-${new Date().toISOString().slice(0, 10)}.csv`,
                  CSV_COLUMNS,
                  (data?.results ?? []) as unknown as Record<string, unknown>[],
                )
              }
            >
              Export CSV
            </button>
          </div>
        </div>
        <ItemTable
          rows={data?.results ?? []}
          columns={columns}
          sort={filters.sort}
          onSort={(sortKey) => set("sort", sortKey)}
          emptyTitle={isFetching ? "Scanning the market..." : "No items match those filters"}
          emptyBody="Loosen the volume or margin floor, or widen the quote age."
        />
      </div>
    </>
  );
}
