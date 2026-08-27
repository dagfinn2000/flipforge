import { useEffect, useRef, useState } from "react";
import {
  ColorType, createChart, LineStyle,
  type CandlestickData, type IChartApi, type ISeriesApi,
  type LineData, type HistogramData, type UTCTimestamp,
} from "lightweight-charts";

import type { SeriesPoint } from "../types";
import { clsx, gp, gpShort } from "../lib/format";

const COLORS = {
  buy: "#f2637e",     // instant-buy price: what sellers receive from impatient buyers
  sell: "#4ec9a0",    // instant-sell price: what buyers pay to impatient sellers
  sma: "#e0a458",
  sma50: "#7c6df2",
  vwap: "#6aa6f8",
  band: "rgba(124, 109, 242, 0.35)",
  // The shaded gap between the two lines: this is the gross spread you are
  // trading. The post-tax margin is always smaller, and is the number the rest
  // of the app reports.
  spreadFill: "rgba(124, 109, 242, 0.16)",
  volBuy: "rgba(242, 99, 126, 0.55)",
  volSell: "rgba(78, 201, 160, 0.5)",
  grid: "#1a2231",
  text: "#8695ad",
  // Opaque, and must match the chart background exactly: the lower area series
  // is a mask that hides the part of the fill below the buy line.
  background: "#121722",
};

export type Overlay = "sma20" | "sma50" | "vwap" | "bollinger";
export type ChartMode = "spread" | "candles";

interface Props {
  points: SeriesPoint[];
  overlays: Overlay[];
  mode: ChartMode;
  height?: number;
}

interface Hover {
  time: number | null;
  high: number | null;
  low: number | null;
  margin: number | null;
  volume: number | null;
}

const asTime = (t: number) => t as UTCTimestamp;

type SeriesKind = "Line" | "Candlestick" | "Histogram" | "Area";

/** Turns the wiki's average high/low pair into a drawable OHLC candle.
 *  There is no true open or close in the source data, so the previous
 *  midpoint opens the bar and the current midpoint closes it. */
function toCandles(points: SeriesPoint[]): CandlestickData[] {
  const out: CandlestickData[] = [];
  points.forEach((p, i) => {
    const close = p.mid;
    const open = i > 0 ? points[i - 1].mid : p.mid;
    const high = Math.max(p.high ?? close, open, close);
    const low = Math.min(p.low ?? close, open, close);
    out.push({ time: asTime(p.t), open, high, low, close });
  });
  return out;
}

export default function PriceChart({ points, overlays, mode, height = 380 }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<SeriesKind>>>({});
  const [hover, setHover] = useState<Hover | null>(null);

  // Build the chart once; data and overlay changes update it in place.
  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: COLORS.background },
        textColor: COLORS.text,
        fontFamily: "SF Mono, JetBrains Mono, ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: COLORS.grid, style: LineStyle.Dotted },
        horzLines: { color: COLORS.grid, style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: "#2b3648", scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderColor: "#2b3648", timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: 1,
        vertLine: { color: "#4a5568", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#2b3648" },
        horzLine: { color: "#4a5568", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#2b3648" },
      },
      handleScale: { axisPressedMouseMove: { price: false } },
    });
    chartRef.current = chart;

    const resize = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    resize.observe(container.current);
    chart.applyOptions({ width: container.current.clientWidth });

    return () => {
      resize.disconnect();
      chart.remove();
      chartRef.current = null;
      series.current = {};
    };
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !points.length) return;

    // Rebuild every series so toggling an overlay never leaves a ghost behind.
    Object.values(series.current).forEach((s) => chart.removeSeries(s));
    series.current = {};

    const add = <K extends SeriesKind>(key: string, s: ISeriesApi<K>) => {
      series.current[key] = s as ISeriesApi<SeriesKind>;
      return s;
    };

    if (mode === "candles") {
      const candles = add("candles", chart.addCandlestickSeries({
        upColor: COLORS.sell, downColor: COLORS.buy,
        wickUpColor: COLORS.sell, wickDownColor: COLORS.buy,
        borderVisible: false, priceLineVisible: false,
      }));
      candles.setData(toCandles(points));
    } else {
      // Shade the gap between the two prices. lightweight-charts has no band
      // primitive, so this is the standard two-area trick: fill from the upper
      // line down to the axis, then paint an opaque area under the lower line
      // in the background colour to mask everything below it. Only points where
      // both sides traded take part, so the band is never guessed at.
      const bothSides = points.filter((p) => p.high != null && p.low != null);

      const shade = add("shade", chart.addAreaSeries({
        topColor: COLORS.spreadFill, bottomColor: COLORS.spreadFill,
        lineColor: "rgba(0,0,0,0)", lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      }));
      shade.setData(
        bothSides.map((p) => ({ time: asTime(p.t), value: p.high! })) as LineData[],
      );

      const mask = add("mask", chart.addAreaSeries({
        topColor: COLORS.background, bottomColor: COLORS.background,
        lineColor: "rgba(0,0,0,0)", lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      }));
      mask.setData(
        bothSides.map((p) => ({ time: asTime(p.t), value: p.low! })) as LineData[],
      );

      const sell = add("sell", chart.addLineSeries({
        color: COLORS.buy, lineWidth: 2, priceLineVisible: false,
        title: "sell at", lastValueVisible: true,
      }));
      const buy = add("buy", chart.addLineSeries({
        color: COLORS.sell, lineWidth: 2, priceLineVisible: false,
        title: "buy at", lastValueVisible: true,
      }));
      sell.setData(
        points.filter((p) => p.high != null).map((p) => ({ time: asTime(p.t), value: p.high! })) as LineData[],
      );
      buy.setData(
        points.filter((p) => p.low != null).map((p) => ({ time: asTime(p.t), value: p.low! })) as LineData[],
      );
    }

    const overlaySpecs: { id: Overlay; key: keyof SeriesPoint; color: string; label: string; dashed?: boolean }[] = [
      { id: "sma20", key: "sma20", color: COLORS.sma, label: "SMA 20" },
      { id: "sma50", key: "sma50", color: COLORS.sma50, label: "SMA 50" },
      { id: "vwap", key: "vwap", color: COLORS.vwap, label: "VWAP" },
    ];
    overlaySpecs
      .filter((spec) => overlays.includes(spec.id))
      .forEach((spec) => {
        const line = add(spec.id, chart.addLineSeries({
          color: spec.color, lineWidth: 1, priceLineVisible: false,
          lastValueVisible: false, title: spec.label,
        }));
        line.setData(
          points
            .filter((p) => p[spec.key] != null)
            .map((p) => ({ time: asTime(p.t), value: p[spec.key] as number })) as LineData[],
        );
      });

    if (overlays.includes("bollinger")) {
      (["bb_upper", "bb_lower"] as const).forEach((key) => {
        const line = add(key, chart.addLineSeries({
          color: COLORS.band, lineWidth: 1, lineStyle: LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false,
        }));
        line.setData(
          points.filter((p) => p[key] != null).map((p) => ({ time: asTime(p.t), value: p[key]! })) as LineData[],
        );
      });
    }

    // Volume, split by side, on its own scale pinned to the bottom quarter.
    // Two histograms stacked by drawing the total first and the buy side over
    // it, so the lower segment of each bar is volume that traded at the
    // instant-buy price and the upper segment is volume that traded at the
    // instant-sell price. A combined bar hides which side of the book is
    // actually active, which is half of what the chart is for.
    const totalVolume = add("volume_total", chart.addHistogramSeries({
      priceFormat: { type: "volume" }, priceScaleId: "volume",
      priceLineVisible: false, lastValueVisible: false, color: COLORS.volSell,
    }));
    const buyVolume = add("volume_buy", chart.addHistogramSeries({
      priceFormat: { type: "volume" }, priceScaleId: "volume",
      priceLineVisible: false, lastValueVisible: false, color: COLORS.volBuy,
    }));
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    totalVolume.setData(
      points.map((p) => ({ time: asTime(p.t), value: p.buy_vol + p.sell_vol })) as HistogramData[],
    );
    buyVolume.setData(
      points.map((p) => ({ time: asTime(p.t), value: p.buy_vol })) as HistogramData[],
    );

    chart.timeScale().fitContent();

    const byTime = new Map(points.map((p) => [p.t, p]));
    const onMove = (param: { time?: unknown }) => {
      const point = param.time ? byTime.get(param.time as number) : undefined;
      setHover(
        point
          ? {
              time: point.t, high: point.high, low: point.low,
              margin: point.margin, volume: point.buy_vol + point.sell_vol,
            }
          : null,
      );
    };
    chart.subscribeCrosshairMove(onMove);
    return () => chart.unsubscribeCrosshairMove(onMove);
  }, [points, overlays, mode]);

  const last = points[points.length - 1];
  const shown = hover ?? (last
    ? { time: last.t, high: last.high, low: last.low, margin: last.margin, volume: last.buy_vol + last.sell_vol }
    : null);

  return (
    <div className="chart-shell">
      {shown && (
        <div className="chart-legend">
          <div className="row">
            <span className="key">sell at</span>
            <span style={{ color: COLORS.buy }}>{gp(shown.high)}</span>
          </div>
          <div className="row">
            <span className="key">buy at</span>
            <span style={{ color: COLORS.sell }}>{gp(shown.low)}</span>
          </div>
          <div className="row">
            <span className="key">margin</span>
            <span className={clsx(shown.margin && shown.margin > 0 ? "up" : "down")}>
              {gp(shown.margin, { sign: true })}
            </span>
          </div>
          <div className="row">
            <span className="key">volume</span>
            <span>{gpShort(shown.volume)}</span>
          </div>
          {shown.time && (
            <div className="row" style={{ color: "var(--text-faint)", fontSize: 10.5 }}>
              <span>{new Date(shown.time * 1000).toLocaleString()}</span>
            </div>
          )}
        </div>
      )}
      <div ref={container} />
    </div>
  );
}
