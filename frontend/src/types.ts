export interface ItemRow {
  id: number;
  name: string;
  icon?: string | null;
  icon_url?: string | null;
  members?: boolean;
  buy_limit?: number | null;
  tax_exempt?: boolean;
  highalch?: number | null;
  high?: number | null;
  low?: number | null;
  tax?: number | null;
  /** Post-tax profit per unit. There is no pre-tax figure anywhere in this app. */
  margin?: number | null;
  roi?: number | null;
  breakeven_sell?: number | null;
  /** Instant-sell above instant-buy: a real feed condition, not an error. */
  crossed?: boolean;
  vol_1h?: number | null;
  vol_24h?: number | null;
  flow_ratio?: number | null;
  avg_margin_24h?: number | null;
  /** Stdev of the post-tax spread over its own mean. Lower is steadier. */
  margin_cv?: number | null;
  margin_positive_24h?: number | null;
  price_change_1h?: number | null;
  price_change_24h?: number | null;
  price_change_7d?: number | null;
  volatility_24h?: number | null;
  zscore_24h?: number | null;
  vol_zscore?: number | null;
  rsi_14?: number | null;
  est_fill_hours?: number | null;
  potential_profit?: number | null;
  flip_score?: number | null;
  data_age_seconds?: number | null;
  /** Trailing-month realised profitability, from graded flips. */
  track_score?: number | null;
  track_samples?: number | null;
  track_win_rate?: number | null;
  track_median_profit?: number | null;
  affordable_quantity?: number;
  affordable_profit?: number;
  signal?: string;
  signal_note?: string;
  note?: string | null;
}

export interface ScoreComponent {
  key: string;
  value: number;
  weight: number;
  contribution: number;
  raw: number | null;
}

export interface ScoreBreakdown {
  total: number;
  components: ScoreComponent[];
  notes: string[];
  weights: Record<string, number>;
}

export interface TaxPolicy {
  rate: number;
  cap: number;
  /** Derived from the rate, never hardcoded: at 2% this is 50gp. */
  free_below: number;
  exempt_count: number;
  note: string;
}

export interface LimitWindow {
  limit: number | null;
  used: number;
  remaining: number | null;
  resets_at: number | null;
  window_hours: number;
}

export interface ItemDetail extends ItemRow {
  examine?: string | null;
  value?: number | null;
  lowalch?: number | null;
  high_time?: number | null;
  low_time?: number | null;
  watched: boolean;
  score_breakdown: ScoreBreakdown | null;
  track_breakdown: ScoreBreakdown | null;
  track_median_margin?: number | null;
  track_window_days?: number | null;
  tax_policy: TaxPolicy;
  limit_window: LimitWindow;
  limit_cycle?: { quantity: number; capital: number; profit: number };
}

export interface SeriesPoint {
  t: number;
  high: number | null;
  low: number | null;
  mid: number;
  buy_vol: number;
  sell_vol: number;
  margin: number | null;
  crossed: boolean;
  sma20: number | null;
  sma50: number | null;
  ema12: number | null;
  rsi: number | null;
  vwap: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}

export interface Series {
  item_id: number;
  timestep: string;
  points: SeriesPoint[];
  volatility: number | null;
  crossed_count: number;
}

export interface MarketSummary {
  tracked: number;
  profitable: number;
  fresh: number;
  crossed: number;
  volume_24h: number;
  median_roi: number;
  updated_at: number | null;
  candles: number;
  backfill_complete: boolean;
  last_poll: number | null;
  tax_policy: TaxPolicy;
}

export interface Alert {
  id: number;
  item_id: number;
  name: string;
  icon_url?: string | null;
  metric: string;
  op: string;
  threshold: number;
  hysteresis: number;
  armed: boolean;
  note?: string | null;
  active: boolean;
  cooldown_s: number;
  last_fired?: number | null;
  current_value?: number | null;
  distance?: number | null;
}

export interface AlertEvent {
  id: number;
  alert_id: number | null;
  item_id: number;
  name: string;
  icon_url?: string | null;
  message: string;
  value: number | null;
  seen: boolean;
  created_at: number;
}

export interface Trade {
  id: number;
  item_id: number;
  name: string;
  icon_url?: string | null;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  tax_paid: number;
  note?: string | null;
  executed_at: number;
}

export interface Position {
  item_id: number;
  name: string;
  icon_url?: string | null;
  high: number | null;
  low: number | null;
  open_quantity: number;
  cost_basis: number;
  avg_cost: number | null;
  market_value: number;
  realised: number;
  unrealised: number | null;
  total: number;
  tax_paid: number;
  bought_quantity: number;
  sold_quantity: number;
  unmatched_sales: number;
  breakeven_sell: number | null;
  price_change_24h: number | null;
}

export interface PortfolioResponse {
  positions: Position[];
  totals: {
    realised: number;
    unrealised: number;
    capital_deployed: number;
    market_value: number;
    tax_paid: number;
    open_positions: number;
    trades: number;
    total: number;
    return_pct: number | null;
  };
}

export interface AllocationRow {
  item_id: number;
  name: string;
  icon_url?: string | null;
  price: number;
  margin: number;
  roi: number;
  score: number;
  buy_limit: number | null;
  volume_24h: number;
  est_fill_hours: number | null;
  members: boolean;
  quantity: number;
  capital: number;
  profit: number;
  pinned: boolean;
}

export interface AllocatorPlan {
  allocations: AllocationRow[];
  bankroll: number;
  slots: number;
  slots_used: number;
  capital_used: number;
  capital_idle: number;
  expected_profit: number;
  expected_return: number | null;
  notes: string[];
  candidates_considered: number;
  pinned: number[];
  excluded: number[];
  slot_reference: { members: number; free_to_play: number };
}

export interface Decile {
  decile: number;
  samples: number;
  score_min: number;
  score_max: number;
  score_avg: number;
  avg_realised_margin: number | null;
  median_realised_margin: number | null;
  avg_cycle_profit: number | null;
  median_cycle_profit: number | null;
  avg_realised_roi: number | null;
  win_rate: number | null;
}

export interface ValidationResponse {
  horizon: string;
  days: number;
  source: string | null;
  deciles: Decile[];
  sources: Record<string, number>;
  verdict: {
    top_decile_avg_cycle_profit: number | null;
    bottom_decile_avg_cycle_profit: number | null;
    lift: number;
    top_beats_bottom: boolean;
    top_win_rate: number | null;
    bottom_win_rate: number | null;
    best_decile_by_median_profit: number;
    monotonic_win_rate: boolean;
  } | null;
}

export interface ValidationSummary {
  days: number;
  horizons: {
    horizon: string;
    samples: number;
    items: number;
    earliest: number | null;
    latest: number | null;
    score_roi_corr: number | null;
    score_margin_corr: number | null;
    score_cycle_corr: number | null;
    avg_realised_roi: number | null;
  }[];
  snapshots_total: number;
  snapshots_awaiting_grade: number;
  note: string;
}


export interface Health {
  status: string;
  uptime_seconds: number;
  items: number;
  ws_clients: number;
  seconds_since_price_poll: number | null;
  seconds_since_metrics: number | null;
  backfill_complete: boolean;
  tax_policy: TaxPolicy;
}

export interface SystemConfig {
  version: string;
  source: string;
  user_agent: string;
  tax_policy: TaxPolicy;
  poll_seconds: Record<string, number>;
  slots: { members: number; free_to_play: number };
  retention: Record<string, number | string>;
}

export interface GapStep {
  missing_windows: number;
  windows_considered: number;
  covering_hours: number;
  lookback_hours: number;
  oldest_gap: number | null;
  newest_gap: number | null;
  coverage: number | null;
}

export interface GapReport {
  enabled: boolean;
  steps: Record<string, GapStep>;
  known_thin_windows: number;
  last_run: number | null;
  max_requests_per_pass: number;
}

export interface StorageReport {
  total_bytes: number;
  tables: { table: string; bytes: number; rows: number }[];
  retention: Record<string, number | string>;
  last_maintenance: number | null;
}


export interface ScoreHistoryRow {
  ts: number;
  score: number | null;
  buy: number | null;
  sell: number | null;
  predicted_margin: number | null;
  exit_price: number | null;
  realised_margin: number | null;
  realised_roi: number | null;
}
