/** Number and time formatting shared by every view. */

export function gp(value: number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const sign = opts.sign && value > 0 ? "+" : "";
  return sign + Math.round(value).toLocaleString("en-US");
}

/** Compact coin notation: 1.2m, 845k, 3.1b. */
export function gpShort(value: number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const sign = value > 0 && opts.sign ? "+" : value < 0 ? "-" : "";
  const n = Math.abs(value);
  if (n >= 1e9) return `${sign}${trim(n / 1e9)}b`;
  if (n >= 1e6) return `${sign}${trim(n / 1e6)}m`;
  if (n >= 1e4) return `${sign}${trim(n / 1e3)}k`;
  return sign + Math.round(n).toLocaleString("en-US");
}

function trim(n: number): string {
  return n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2);
}

export function pct(value: number | null | undefined, digits = 2, sign = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const s = sign && value > 0 ? "+" : "";
  return `${s}${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function ago(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "never";
  const delta = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "--";
  if (hours < 1 / 60) return "<1m";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours > 99) return "99h+";
  return `${hours.toFixed(1)}h`;
}

/** Green for gains, red for losses, muted for nothing. */
export function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

export function clsx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
