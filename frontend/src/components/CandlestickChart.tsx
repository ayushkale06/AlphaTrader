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

export const CandlestickChart = ({ symbol, candles, orders = [], activePosition }: Props) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<ISeriesApi<any> | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersPluginRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const slLineRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tpLineRef = useRef<any>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#475569',
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

      // Render trade execution markers on candles
      if (markersPluginRef.current && orders.length > 0 && uniqueData.length > 0) {
        const markers: SeriesMarker<Time>[] = [];
        const times = uniqueData.map((d) => Number(d.time));

        for (const ord of orders) {
          if (ord.symbol !== symbol) continue;
          const ordTime = Math.floor(new Date(ord.created_at).getTime() / 1000);
          
          // Find closest candle time
          let closestTime = times[0];
          let minDiff = Math.abs(times[0] - ordTime);
          for (const t of times) {
            const diff = Math.abs(t - ordTime);
            if (diff < minDiff) {
              minDiff = diff;
              closestTime = t;
            }
          }

          if (ord.side === 'BUY') {
            markers.push({
              time: closestTime as Time,
              position: 'belowBar',
              color: '#10b981',
              shape: 'arrowUp',
              text: `BUY ${ord.quantity} @ ₹${parseFloat(ord.average_fill_price || ord.limit_price || '0').toFixed(1)}`,
            });
          } else {
            markers.push({
              time: closestTime as Time,
              position: 'aboveBar',
              color: '#ef4444',
              shape: 'arrowDown',
              text: `SELL ${ord.quantity} @ ₹${parseFloat(ord.average_fill_price || ord.limit_price || '0').toFixed(1)}`,
            });
          }
        }

        markers.sort((a, b) => Number(a.time) - Number(b.time));
        markersPluginRef.current.setMarkers(markers);
      }

      // Render Stop-Loss and Take-Profit price lines
      if (seriesRef.current) {
        if (slLineRef.current) {
          seriesRef.current.removePriceLine(slLineRef.current);
          slLineRef.current = null;
        }
        if (tpLineRef.current) {
          seriesRef.current.removePriceLine(tpLineRef.current);
          tpLineRef.current = null;
        }

        if (activePosition && parseFloat(activePosition.quantity) > 0) {
          const sl = activePosition.trailing_stop_price || activePosition.stop_loss_price;
          const tp = activePosition.take_profit_price;

          if (sl) {
            slLineRef.current = seriesRef.current.createPriceLine({
              price: parseFloat(sl),
              color: '#ef4444',
              lineWidth: 2,
              lineStyle: 2, // Dashed
              axisLabelVisible: true,
              title: 'STOP LOSS',
            });
          }

          if (tp) {
            tpLineRef.current = seriesRef.current.createPriceLine({
              price: parseFloat(tp),
              color: '#10b981',
              lineWidth: 2,
              lineStyle: 2, // Dashed
              axisLabelVisible: true,
              title: 'TAKE PROFIT',
            });
          }
        }
      }
    } catch (e) {
      console.error('Error applying candlestick data or markers:', e);
    }
  }, [candles, orders, symbol, activePosition]);

  return (
    <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 mb-3">
        <div className="flex items-center space-x-3">
          <span className="text-lg font-bold text-slate-900">{symbol}</span>
          <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 font-mono font-medium">5m Interval</span>
          {activePosition && parseFloat(activePosition.quantity) > 0 && (
            <span className="text-xs px-2.5 py-0.5 rounded-full bg-indigo-50 border border-indigo-200 text-indigo-700 font-mono font-medium">
              Active: {activePosition.quantity} shares | Entry: ₹{parseFloat(activePosition.average_entry_price).toFixed(2)}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-4 text-xs font-mono">
          <span className="flex items-center gap-1.5 text-emerald-600 font-medium">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500"></span> BUY Fill (▲)
          </span>
          <span className="flex items-center gap-1.5 text-rose-600 font-medium">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-rose-500"></span> SELL Fill (▼)
          </span>
          <span className="text-slate-400">TradingView Lightweight Charts v5</span>
        </div>
      </div>
      <div ref={chartContainerRef} className="w-full h-[380px]" />
    </div>
  );
};

