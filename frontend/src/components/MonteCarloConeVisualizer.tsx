import React from 'react';
import type { PriceTrajectoryDay } from '../types';

interface Props {
  currentPrice: number;
  trajectory: PriceTrajectoryDay[];
}

export const MonteCarloConeVisualizer: React.FC<Props> = ({ currentPrice, trajectory }) => {
  if (!trajectory || trajectory.length === 0) return null;

  // Find min and max for svg scaling
  const allPrices = [
    currentPrice,
    ...trajectory.map((t) => t.q05_bear),
    ...trajectory.map((t) => t.q95_bull),
  ];
  const minPrice = Math.min(...allPrices) * 0.99;
  const maxPrice = Math.max(...allPrices) * 1.01;
  const range = maxPrice - minPrice || 1;

  const width = 800;
  const height = 260;
  const paddingX = 40;
  const paddingY = 25;
  const plotWidth = width - paddingX * 2;
  const plotHeight = height - paddingY * 2;

  const getX = (index: number) => paddingX + (index / trajectory.length) * plotWidth;
  const getY = (val: number) => paddingY + plotHeight - ((val - minPrice) / range) * plotHeight;

  // Build outer cone (95% bull down to 5% bear)
  const bullPoints = trajectory.map((t, i) => `${getX(i + 1)},${getY(t.q95_bull)}`);
  const bearPointsRev = [...trajectory].reverse().map((t, i) => `${getX(trajectory.length - i)},${getY(t.q05_bear)}`);
  const outerConePath = `M ${getX(0)},${getY(currentPrice)} L ${bullPoints.join(' L ')} L ${bearPointsRev.join(' L ')} Z`;

  // Build inner cone (75% down to 25%)
  const q75Points = trajectory.map((t, i) => `${getX(i + 1)},${getY(t.q75_upper)}`);
  const q25PointsRev = [...trajectory].reverse().map((t, i) => `${getX(trajectory.length - i)},${getY(t.q25_lower)}`);
  const innerConePath = `M ${getX(0)},${getY(currentPrice)} L ${q75Points.join(' L ')} L ${q25PointsRev.join(' L ')} Z`;

  // Median trajectory line
  const medianPoints = [
    `${getX(0)},${getY(currentPrice)}`,
    ...trajectory.map((t, i) => `${getX(i + 1)},${getY(t.q50_median)}`),
  ];
  const medianLinePath = `M ${medianPoints.join(' L ')}`;

  return (
    <div className="w-full bg-slate-900/90 rounded-2xl p-4 border border-slate-800 text-slate-100 shadow-xl relative overflow-hidden backdrop-blur-md">
      <div className="flex justify-between items-center text-xs font-mono text-slate-400 mb-2 px-2 border-b border-slate-800/80 pb-2">
        <div className="flex items-center space-x-2">
          <span className="h-2 w-2 rounded-full bg-indigo-500 animate-pulse" />
          <span className="font-bold text-white tracking-wider uppercase text-[11px]">
            Monte Carlo Multi-Path Cone (2,000 Paths)
          </span>
        </div>
        <div className="flex items-center space-x-4 text-[10px]">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> 95% Bull Ceiling
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-indigo-400" /> 50% Median Drift
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-rose-500" /> 5% Bear Floor
          </span>
        </div>
      </div>

      <div className="w-full overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto min-w-[600px] select-none">
          <defs>
            <linearGradient id="outerConeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#6366f1" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#10b981" stopOpacity="0.08" />
            </linearGradient>
            <linearGradient id="innerConeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#6366f1" stopOpacity="0.35" />
              <stop offset="100%" stopColor="#818cf8" stopOpacity="0.25" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {[0.25, 0.5, 0.75].map((pct) => {
            const y = paddingY + plotHeight * pct;
            return (
              <line
                key={pct}
                x1={paddingX}
                y1={y}
                x2={width - paddingX}
                y2={y}
                stroke="#334155"
                strokeDasharray="4 4"
                strokeWidth="1"
              />
            );
          })}

          {/* Shaded 90% outer confidence interval */}
          <path d={outerConePath} fill="url(#outerConeGrad)" />

          {/* Shaded 50% interquartile confidence interval */}
          <path d={innerConePath} fill="url(#innerConeGrad)" />

          {/* Bull 95% line */}
          <path
            d={`M ${getX(0)},${getY(currentPrice)} L ${bullPoints.join(' L ')}`}
            fill="none"
            stroke="#10b981"
            strokeWidth="1.5"
            strokeDasharray="3 3"
          />

          {/* Bear 5% line */}
          <path
            d={`M ${getX(0)},${getY(currentPrice)} L ${[...bearPointsRev].reverse().join(' L ')}`}
            fill="none"
            stroke="#f43f5e"
            strokeWidth="1.5"
            strokeDasharray="3 3"
          />

          {/* 50% Median Trajectory Line */}
          <path
            d={medianLinePath}
            fill="none"
            stroke="#818cf8"
            strokeWidth="2.5"
          />

          {/* Current mark anchor dot */}
          <circle cx={getX(0)} cy={getY(currentPrice)} r="5" fill="#6366f1" stroke="#ffffff" strokeWidth="2" />
          <text x={getX(0)} y={getY(currentPrice) - 10} fill="#ffffff" fontSize="10" fontWeight="bold" textAnchor="middle" fontFamily="monospace">
            ₹{currentPrice.toFixed(2)}
          </text>

          {/* Final Day 10 labels */}
          {trajectory.length > 0 && (
            <>
              <circle
                cx={getX(trajectory.length)}
                cy={getY(trajectory[trajectory.length - 1].q95_bull)}
                r="4"
                fill="#10b981"
              />
              <text
                x={getX(trajectory.length) + 6}
                y={getY(trajectory[trajectory.length - 1].q95_bull) + 4}
                fill="#34d399"
                fontSize="10"
                fontFamily="monospace"
                fontWeight="bold"
              >
                ₹{trajectory[trajectory.length - 1].q95_bull.toFixed(2)}
              </text>

              <circle
                cx={getX(trajectory.length)}
                cy={getY(trajectory[trajectory.length - 1].q05_bear)}
                r="4"
                fill="#f43f5e"
              />
              <text
                x={getX(trajectory.length) + 6}
                y={getY(trajectory[trajectory.length - 1].q05_bear) + 4}
                fill="#fb7185"
                fontSize="10"
                fontFamily="monospace"
                fontWeight="bold"
              >
                ₹{trajectory[trajectory.length - 1].q05_bear.toFixed(2)}
              </text>
            </>
          )}

          {/* X axis dates */}
          {trajectory.map((t, i) => (
            <text
              key={t.day}
              x={getX(i + 1)}
              y={height - 6}
              fill="#94a3b8"
              fontSize="9"
              fontFamily="monospace"
              textAnchor="middle"
            >
              {t.date.split(',')[0]}
            </text>
          ))}
        </svg>
      </div>
    </div>
  );
};
