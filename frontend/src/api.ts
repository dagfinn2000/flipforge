import type {
  Alert, AlertEvent, AllocatorPlan, ItemDetail, ItemRow, MarketSummary,
  PortfolioResponse, Series, Trade, ValidationResponse, ValidationSummary,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body ? { "content-type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${detail ? ` - ${detail}` : ""}`);
  }
  return res.json() as Promise<T>;
}

const qs = (params: object): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
};

export interface ScannerFilters {
  min_margin?: number;
  min_roi?: number;
  min_volume?: number;
  min_score?: number;
  min_price?: number;
  max_price?: number;
  min_buy_limit?: number;
  max_capital?: number;
  members?: boolean;
  max_age?: number;
  /** Cap on margin variability. Lower means a steadier spread. */
  max_margin_cv?: number;
  max_fill_hours?: number;
  hide_crossed?: boolean;
  sort?: string;
  limit?: number;
}

export interface AllocatorRequest {
  bankroll: number;
  slots: number;
  min_volume?: number;
  min_score?: number;
  max_quote_age?: number;
  members?: boolean | null;
  max_share?: number;
  pinned?: number[];
  excluded?: number[];
}

export const api = {
  summary: () => request<MarketSummary>("/api/market/summary"),
  health: () => request<Record<string, unknown>>("/api/health"),

  scanner: (f: ScannerFilters) =>
    request<{ results: ItemRow[]; count: number; sort: string }>(`/api/scanner${qs(f)}`),
  movers: (window: string, direction: string, limit = 12) =>
    request<{ results: ItemRow[] }>(`/api/market/movers${qs({ window, direction, limit })}`),
  unusual: (limit = 12) =>
    request<{ results: ItemRow[] }>(`/api/market/unusual${qs({ limit })}`),

  search: (q: string, limit = 12) =>
    request<{ results: ItemRow[] }>(`/api/items/search${qs({ q, limit })}`),
  item: (id: number) => request<ItemDetail>(`/api/items/${id}`),
  series: (id: number, timestep: string, limit = 365) =>
    request<Series>(`/api/items/${id}/series${qs({ timestep, limit })}`),
  calculator: (id: number, buy?: number, sell?: number, quantity?: number) =>
    request<Record<string, number | boolean | null>>(
      `/api/items/${id}/calculator${qs({ buy, sell, quantity })}`,
    ),
  config: () => request<Record<string, unknown>>("/api/config"),

  watchlist: () => request<{ results: ItemRow[] }>("/api/watchlist"),
  watch: (item_id: number, note?: string) =>
    request<{ ok: boolean }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ item_id, note }),
    }),
  unwatch: (item_id: number) =>
    request<{ ok: boolean }>(`/api/watchlist/${item_id}`, { method: "DELETE" }),

  alerts: () => request<{ results: Alert[]; metrics: Record<string, string> }>("/api/alerts"),
  createAlert: (body: {
    item_id: number; metric: string; op: string; threshold: number;
    hysteresis?: number; note?: string;
  }) => request<{ ok: boolean; id: number }>("/api/alerts", {
    method: "POST", body: JSON.stringify(body),
  }),
  deleteAlert: (id: number) =>
    request<{ ok: boolean }>(`/api/alerts/${id}`, { method: "DELETE" }),
  toggleAlert: (id: number) =>
    request<{ ok: boolean; active: boolean }>(`/api/alerts/${id}/toggle`, { method: "POST" }),
  alertEvents: (limit = 40) =>
    request<{ results: AlertEvent[] }>(`/api/alerts/events${qs({ limit })}`),
  markEventsSeen: () =>
    request<{ ok: boolean }>("/api/alerts/events/seen", { method: "POST" }),

  portfolio: () => request<PortfolioResponse>("/api/portfolio"),

  allocate: (body: AllocatorRequest) =>
    request<AllocatorPlan>("/api/allocator/solve", {
      method: "POST", body: JSON.stringify(body),
    }),
  allocatorPrefs: () =>
    request<{ results: { item_id: number; mode: string; name: string; icon_url?: string }[] }>(
      "/api/allocator/prefs",
    ),
  setAllocatorPref: (item_id: number, mode: "pin" | "exclude") =>
    request<{ ok: boolean }>("/api/allocator/prefs", {
      method: "POST", body: JSON.stringify({ item_id, mode }),
    }),
  clearAllocatorPref: (item_id: number) =>
    request<{ ok: boolean }>(`/api/allocator/prefs/${item_id}`, { method: "DELETE" }),

  validationDeciles: (horizon: string, days = 7, source?: string) =>
    request<ValidationResponse>(`/api/validation/deciles${qs({ horizon, days, source })}`),
  validationSummary: (days = 7) =>
    request<ValidationSummary>(`/api/validation/summary${qs({ days })}`),
  trades: (item_id?: number) =>
    request<{ results: Trade[] }>(`/api/portfolio/trades${qs({ item_id })}`),
  addTrade: (body: {
    item_id: number; side: string; quantity: number; price: number; note?: string;
  }) => request<{ ok: boolean; id: number; tax_paid: number }>("/api/portfolio/trades", {
    method: "POST", body: JSON.stringify(body),
  }),
  deleteTrade: (id: number) =>
    request<{ ok: boolean }>(`/api/portfolio/trades/${id}`, { method: "DELETE" }),
};
