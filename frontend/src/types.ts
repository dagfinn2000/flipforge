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
  spread?: number | null;
  tax?: number | null;
  margin?: number | null;
  roi?: number | null;
  vol_1h?: number | null;
  vol_24h?: number | null;
  flow_ratio?: number | null;
  avg_margin_24h?: number | null;
  margin_stability?: number | null;
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
  affordable_quantity?: number;
  affordable_profit?: number;
  signal?: string;
  signal_note?: string;
  note?: string | null;
}

export interface ScoreBreakdown {
  roi: number;
  profit: number;
  volume: number;
  stability: number;
  fill: number;
  freshness: number;
  total: number;
  notes: string[];
  weights: Record<string, number>;
}

export interface ItemDetail extends ItemRow {
  examine?: string | null;
  value?: number | null;
  lowalch?: number | null;
  high_time?: number | null;
  low_time?: number | null;
  watched: boolean;
  score_breakdown: ScoreBreakdown;
  tax_config: TaxConfig;
  limit_cycle?: { quantity: number; capital: number; profit: number };
}

export interface TaxConfig {
  rate: number;
  cap: number;
  min_price: number;
}

export interface SeriesPoint {
  t: number;
  high: number | null;
  low: number | null;
  mid: number;
  buy_vol: number;
  sell_vol: number;
  margin: number | null;
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
}

export interface MarketSummary {
  tracked: number;
  profitable: number;
  fresh: number;
  volume_24h: number;
  median_roi: number;
  updated_at: number | null;
  candles: number;
  backfill_complete: boolean;
  last_poll: number | null;
  tax_config: TaxConfig;
}

export interface Alert {
  id: number;
  item_id: number;
  name: string;
  icon_url?: string | null;
  metric: string;
  op: string;
  threshold: number;
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
