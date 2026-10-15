import { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, CandlestickData, Time } from 'lightweight-charts';
import type { Candle } from '../types';

interface Props {
  symbol: string;
  candles: Candle[];
}

export const CandlestickChart = ({ symbol, candles }: Props) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<ISeriesApi<any> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 380,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || candles.length === 0) return;

    const formattedData: CandlestickData<Time>[] = candles.map((c) => ({
      time: Math.floor(new Date(c.close_time).getTime() / 1000) as Time,
      open: parseFloat(c.open),
      high: parseFloat(c.high),
      low: parseFloat(c.low),
      close: parseFloat(c.close),
    }));

    formattedData.sort((a, b) => Number(a.time) - Number(b.time));
    const uniqueData: CandlestickData<Time>[] = [];
    for (const item of formattedData) {
      if (uniqueData.length === 0 || uniqueData[uniqueData.length - 1].time !== item.time) {
        uniqueData.push(item);
      }
    }

    try {
      seriesRef.current.setData(uniqueData);
      chartRef.current?.timeScale().fitContent();
    } catch (e) {
      console.error('Error applying candlestick data:', e);
    }
  }, [candles]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg">
      <div className="flex justify-between items-center mb-3">
        <div className="flex items-center space-x-3">
          <span className="text-lg font-bold text-slate-100">{symbol}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">5m Interval</span>
        </div>
        <span className="text-xs text-slate-500 font-mono">TradingView Lightweight Charts v5</span>
      </div>
      <div ref={chartContainerRef} className="w-full h-[380px]" />
    </div>
  );
};
