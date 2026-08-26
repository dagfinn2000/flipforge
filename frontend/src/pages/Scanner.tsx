import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type ScannerFilters } from "../api";
import ItemTable, { type ColumnKey } from "../components/ItemTable";
import { gpShort, num } from "../lib/format";

/** Starting points for common flipping styles. */
const PRESETS: { name: string; hint: string; filters: ScannerFilters }[] = [
  {
    name: "Balanced",
    hint: "Liquid items with a stable post-tax edge",
    filters: { min_volume: 1000, min_margin: 1, min_roi: 0.01, min_stability: 0.5, sort: "score" },
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

const COLUMNS: ColumnKey[] = [
  "score", "buy", "sell", "margin", "roi", "profit",
  "vol24", "limit", "fill", "stability", "rsi", "change24h", "age",
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
          Every tradeable item, ranked. All margins are after the sale tax.
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
              <label>Min stability %</label>
              <input
                type="number"
                step={5}
                placeholder="0"
                value={filters.min_stability != null ? +(filters.min_stability * 100).toFixed(0) : ""}
                onChange={(e) =>
                  set("min_stability", e.target.value === "" ? undefined : Number(e.target.value) / 100)
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
          <span className="hint">click a column header to re-rank</span>
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
