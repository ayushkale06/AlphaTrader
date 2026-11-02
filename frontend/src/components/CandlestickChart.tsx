import { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, CandlestickData, Time, SeriesMarker } from 'lightweight-charts';
import type { Candle, OrderItem, PositionItem } from '../types';

interface Props {
  symbol: string;
  candles: Candle[];
  orders?: OrderItem[];
  activePosition?: PositionItem;
}

// Indian Standard Time (IST) offset in seconds (+05:30 = 5 * 3600 + 30 * 60 = 19800)
const IST_OFFSET_SECONDS = 19800;

export const CandlestickChart: React.FC<Props> = ({
  symbol,
  candles,
  orders = [],
  activePosition,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersPluginRef = useRef<any>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#334155',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      },
      grid: {
        vertLines: { color: '#f1f5f9' },
        horzLines: { color: '#f1f5f9' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 380,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          const date = new Date(time * 1000);
          return date.toLocaleTimeString('en-IN', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
            timeZone: 'UTC', // Already shifted by IST_OFFSET_SECONDS
          });
        },
      },
      localization: {
        priceFormatter: (price: number) => `₹${price.toFixed(2)}`,
        timeFormatter: (time: number) => {
          const date = new Date(time * 1000);
          return date.toLocaleTimeString('en-IN', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
            timeZone: 'UTC', // Already shifted by IST_OFFSET_SECONDS
          });
        },
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
    markersPluginRef.current = createSeriesMarkers(candlestickSeries, []);

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

    // Shift UTC timestamp to IST (+05:30) so Lightweight Charts shows exact NSE exchange hours
    const formattedData: CandlestickData<Time>[] = candles.map((c) => {
      const utcSeconds = Math.floor(new Date(c.close_time).getTime() / 1000);
      return {
        time: (utcSeconds + IST_OFFSET_SECONDS) as Time,
        open: parseFloat(c.open),
        high: parseFloat(c.high),
        low: parseFloat(c.low),
        close: parseFloat(c.close),
      };
    });

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

      // Render trade execution markers on candles in IST
      if (markersPluginRef.current && orders.length > 0 && uniqueData.length > 0) {
        const markers: SeriesMarker<Time>[] = [];
        const symbolOrders = orders
          .filter((o) => o.symbol === symbol && o.status === 'FILLED' && o.average_fill_price)
          .slice(-10);

        for (const ord of symbolOrders) {
          const ordUtcSeconds = Math.floor(new Date(ord.created_at).getTime() / 1000);
          const ordIstSeconds = ordUtcSeconds + IST_OFFSET_SECONDS;

          // Find closest candle time in uniqueData
          let closestCandleTime = uniqueData[0].time;
          let minDiff = Math.abs(Number(uniqueData[0].time) - ordIstSeconds);

          for (const cd of uniqueData) {
            const diff = Math.abs(Number(cd.time) - ordIstSeconds);
            if (diff < minDiff) {
              minDiff = diff;
              closestCandleTime = cd.time;
            }
          }

          const isBuy = ord.side === 'BUY';
          markers.push({
            time: closestCandleTime,
            position: isBuy ? 'belowBar' : 'aboveBar',
            color: isBuy ? '#10b981' : '#ef4444',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
            text: `${ord.side} ${parseFloat(ord.filled_quantity).toFixed(0)} @ ₹${parseFloat(ord.average_fill_price || '0').toFixed(1)}`,
          });
        }

        markers.sort((a, b) => Number(a.time) - Number(b.time));
        markersPluginRef.current.setMarkers(markers);
      }
    } catch (e) {
      console.error('Failed to set candle data or markers:', e);
    }
  }, [candles, orders, symbol]);

  return (
    <div className="relative w-full">
      <div ref={chartContainerRef} className="w-full rounded-xl overflow-hidden border border-slate-100 shadow-inner" />
      <div className="absolute top-3 left-3 bg-white/90 backdrop-blur-xs px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-mono flex items-center space-x-3 shadow-xs">
        <span className="font-bold text-slate-800">{symbol}</span>
        <span className="text-slate-400">|</span>
        <span className="text-slate-600 font-medium">5m Real-Time</span>
        <span className="text-slate-400">|</span>
        <span className="text-indigo-600 font-bold">IST (UTC+5:30)</span>
        {activePosition && (
          <>
            <span className="text-slate-400">|</span>
            <span className="text-emerald-700 font-semibold bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
              Active: {parseFloat(activePosition.quantity).toFixed(0)} shares @ ₹{parseFloat(activePosition.average_entry_price).toFixed(2)}
            </span>
          </>
        )}
      </div>
      <div className="absolute top-3 right-3 flex items-center space-x-3 text-[11px] font-mono bg-white/90 backdrop-blur-xs px-3 py-1 rounded-lg border border-slate-200 shadow-xs">
        <span className="flex items-center gap-1.5 text-emerald-600 font-bold">
          <span className="h-2 w-2 rounded-full bg-emerald-500" /> BUY Fill (▲)
        </span>
        <span className="flex items-center gap-1.5 text-rose-600 font-bold">
          <span className="h-2 w-2 rounded-full bg-rose-500" /> SELL Fill (▼)
        </span>
      </div>
    </div>
  );
};
