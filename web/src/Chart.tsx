import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { WireCandle } from "./generated/engine-snapshot";
import { decimalPrice } from "./format";

const number = (value: string) => decimalPrice(value) ?? 0;
const instant = (value: string) =>
  Number(BigInt(value) / 1_000_000_000n) as UTCTimestamp;

export default function Chart({
  candles,
  timeframe,
  instrumentKey,
}: {
  candles: WireCandle[];
  timeframe: string;
  instrumentKey: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastTime = useRef<number>(0);

  useEffect(() => {
    if (!container.current) return;
    const api = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0d171e" },
        textColor: "#83939d",
        fontFamily: "IBM Plex Mono, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e2b33" },
        horzLines: { color: "#1e2b33" },
      },
      rightPriceScale: { borderColor: "#2b3a43" },
      timeScale: {
        borderColor: "#2b3a43",
        timeVisible: true,
        secondsVisible: true,
      },
      crosshair: {
        vertLine: { color: "#71909a" },
        horzLine: { color: "#71909a" },
      },
      localization: {
        locale: "en-IN",
        timeFormatter: (value: Time) =>
          new Date(Number(value) * 1000).toLocaleTimeString("en-IN", {
            timeZone: "Asia/Kolkata",
            hour12: false,
          }),
      },
    });
    chart.current = api;
    candleSeries.current = api.addSeries(CandlestickSeries, {
      upColor: "#52d6a2",
      downColor: "#ec8178",
      wickUpColor: "#52d6a2",
      wickDownColor: "#ec8178",
      borderVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    volumeSeries.current = api.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      color: "#4b839b",
    });
    volumeSeries.current
      .priceScale()
      .applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    lastTime.current = 0;
    return () => {
      api.remove();
      chart.current = null;
      candleSeries.current = null;
      volumeSeries.current = null;
    };
  }, [instrumentKey, timeframe]);

  useEffect(() => {
    const selected = candles
      .filter((c) => c.timeframe_ns === timeframe)
      .sort((a, b) => (BigInt(a.start_ns) < BigInt(b.start_ns) ? -1 : 1))
      .slice(-200);
    if (!candleSeries.current || !volumeSeries.current || selected.length === 0)
      return;
    const mapped = selected.map((c) => ({
      time: instant(c.start_ns),
      open: number(c.open_nanos),
      high: number(c.high_nanos),
      low: number(c.low_nanos),
      close: number(c.close_nanos),
    }));
    const volumes = selected
      .filter((c) => c.volume != null)
      .map((c) => ({
        time: instant(c.start_ns),
        value: Number(BigInt(c.volume!)),
        color: c.close_nanos >= c.open_nanos ? "#245f53" : "#673e44",
      }));
    const last = mapped[mapped.length - 1];
    if (lastTime.current === 0 || Number(last.time) < lastTime.current) {
      candleSeries.current.setData(mapped);
      volumeSeries.current.setData(volumes);
      chart.current?.timeScale().fitContent();
    } else if (Number(last.time) >= lastTime.current) {
      candleSeries.current.update(last);
      if (selected[selected.length - 1].volume != null)
        volumeSeries.current.update(volumes[volumes.length - 1]);
    }
    lastTime.current = Number(last.time);
  }, [candles, timeframe, instrumentKey]);

  return (
    <div className="chart-wrap">
      <div
        ref={container}
        className="chart"
        aria-label="Quote-sampled OHLC candle chart"
      />
      {candles.length === 0 && (
        <div className="chart-empty">Awaiting replay candles</div>
      )}
    </div>
  );
}
