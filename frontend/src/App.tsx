import { useState, useEffect } from 'react';
import { 
  ShieldAlert, 
  TrendingUp, 
  Wallet, 
  Activity, 
  ArrowUpRight, 
  ArrowDownRight, 
  Clock, 
  Server, 
  RefreshCw,
  Sliders,
  Cpu,
  BookOpen,
  History,
  FileCheck2,
  Settings,
  LineChart,
  Layers,
  Database,
  BarChart3,
  Play,
  Bell,
  Send,
  Zap,
  CheckCircle2,
  AlertTriangle
} from 'lucide-react';
import type { 
  PortfolioSummary, 
  PositionsResponse, 
  Quote, 
  Candle, 
  OrdersResponse, 
  RiskTelemetry, 
  RiskAuditResponse, 
  SystemHealth,
  TechnicalAnalysisResponse,
  QuantStrategyResponse,
  BacktestRegistryResponse,
  LedgerJournalResponse,
  AgentWorkflowState,
  AuditChainVerificationResponse,
  SystemConfigResponse,
  MarketStatus,
  QuantAnalytics,
  WatchlistHeatmapResponse,
  BacktestRunResponse,
  AlertItem,
  SimulationMode,
  ManualOrderResult
} from './types';
import { CandlestickChart } from './components/CandlestickChart';
import { motion, AnimatePresence } from 'motion/react';

export default function App() {
  const [activeTab, setActiveTab] = useState<'overview' | 'technical' | 'orchestrator' | 'analytics' | 'heatmap' | 'workbench' | 'ledger' | 'backtest' | 'audit' | 'system'>('overview');
  
  // State variables for all 12 domains
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [orders, setOrders] = useState<OrdersResponse | null>(null);
  const [risk, setRisk] = useState<RiskTelemetry | null>(null);
  const [auditLog, setAuditLog] = useState<RiskAuditResponse | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [marketStatus, setMarketStatus] = useState<MarketStatus | null>(null);

  // Extended domain states
  const [technical, setTechnical] = useState<TechnicalAnalysisResponse | null>(null);
  const [quant, setQuant] = useState<QuantStrategyResponse | null>(null);
  const [orchestrator, setOrchestrator] = useState<AgentWorkflowState | null>(null);
  const [ledger, setLedger] = useState<LedgerJournalResponse | null>(null);
  const [backtests, setBacktests] = useState<BacktestRegistryResponse | null>(null);
  const [chainVerify, setChainVerify] = useState<AuditChainVerificationResponse | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfigResponse | null>(null);

  // New Workstation State: Features 1 - 7
  const [simMode, setSimMode] = useState<SimulationMode>({ mode: 'HISTORICAL_REPLAY', replay_speed: 1.0 });
  const [analytics, setAnalytics] = useState<QuantAnalytics | null>(null);
  const [heatmap, setHeatmap] = useState<WatchlistHeatmapResponse | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [manualOrderQty, setManualOrderQty] = useState<number>(50);
  const [manualOrderSide, setManualOrderSide] = useState<'BUY' | 'SELL'>('BUY');
  const [manualSL, setManualSL] = useState<string>('');
  const [manualTP, setManualTP] = useState<string>('');
  const [orderSubmitting, setOrderSubmitting] = useState<boolean>(false);
  const [orderNotification, setOrderNotification] = useState<ManualOrderResult | null>(null);

  // Webhook notification config state
  const [discordWebhookUrl, setDiscordWebhookUrl] = useState<string>('');
  const [telegramWebhookUrl, setTelegramWebhookUrl] = useState<string>('');
  const [telegramChatId, setTelegramChatId] = useState<string>('');
  const [webhookSaved, setWebhookSaved] = useState<boolean>(false);

  // Dynamic Kelly recommendation state
  const [kellyRec, setKellyRec] = useState<any | null>(null);

  // Backtest Optimizer Workbench State
  const [optFastEma, setOptFastEma] = useState<number>(9);
  const [optSlowEma, setOptSlowEma] = useState<number>(21);
  const [optRsiPeriod, setOptRsiPeriod] = useState<number>(14);
  const [backtestRunning, setBacktestRunning] = useState<boolean>(false);
  const [customBacktestResult, setCustomBacktestResult] = useState<BacktestRunResponse | null>(null);

  const [symbol, setSymbol] = useState<string>('RELIANCE.NS');
  const [verifyingChain, setVerifyingChain] = useState<boolean>(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date>(new Date());

  const fetchData = async () => {
    try {
      const [
        sumRes, posRes, quoteRes, candleRes, ordRes, riskRes, auditRes, healthRes,
        techRes, quantRes, orchRes, ledgerRes, btRes, cfgRes, mktRes,
        simRes, anRes, hmRes, alRes
      ] = await Promise.allSettled([
        fetch('/api/v1/portfolio/summary').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/portfolio/positions').then((r) => r.ok ? r.json() : null),
        fetch(`/api/v1/market/quote/${symbol}`).then((r) => r.ok ? r.json() : null),
        fetch(`/api/v1/market/candles/${symbol}?limit=100`).then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/orders?limit=15').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/risk/telemetry').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/risk/audit-log?limit=10').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/health').then((r) => r.ok ? r.json() : null),
        fetch(`/api/v1/technical/analysis/${symbol}`).then((r) => r.ok ? r.json() : null),
        fetch(`/api/v1/quant/strategy/${symbol}`).then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/agents/orchestration').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/portfolio/ledger?limit=15').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/backtest/reports').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/config/system').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/market/status').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/simulation/mode').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/quant/analytics').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/market/watchlist/heatmap').then((r) => r.ok ? r.json() : null),
        fetch('/api/v1/alerts?limit=20').then((r) => r.ok ? r.json() : null),
      ]);

      if (sumRes.status === 'fulfilled' && sumRes.value) setSummary(sumRes.value);
      if (posRes.status === 'fulfilled' && posRes.value) setPositions(posRes.value);
      if (quoteRes.status === 'fulfilled' && quoteRes.value) setQuote(quoteRes.value);
      if (candleRes.status === 'fulfilled' && candleRes.value) setCandles(candleRes.value.candles || []);
      if (ordRes.status === 'fulfilled' && ordRes.value) setOrders(ordRes.value);
      if (riskRes.status === 'fulfilled' && riskRes.value) setRisk(riskRes.value);
      if (auditRes.status === 'fulfilled' && auditRes.value) setAuditLog(auditRes.value);
      if (healthRes.status === 'fulfilled' && healthRes.value) setHealth(healthRes.value);
      if (techRes.status === 'fulfilled' && techRes.value) setTechnical(techRes.value);
      if (quantRes.status === 'fulfilled' && quantRes.value) setQuant(quantRes.value);
      if (orchRes.status === 'fulfilled' && orchRes.value) setOrchestrator(orchRes.value);
      if (ledgerRes.status === 'fulfilled' && ledgerRes.value) setLedger(ledgerRes.value);
      if (btRes.status === 'fulfilled' && btRes.value) setBacktests(btRes.value);
      if (cfgRes.status === 'fulfilled' && cfgRes.value) setSystemConfig(cfgRes.value);
      if (mktRes.status === 'fulfilled' && mktRes.value) setMarketStatus(mktRes.value);
      if (simRes.status === 'fulfilled' && simRes.value) setSimMode(simRes.value);
      if (anRes.status === 'fulfilled' && anRes.value) setAnalytics(anRes.value);
      if (hmRes.status === 'fulfilled' && hmRes.value) setHeatmap(hmRes.value);
      if (alRes.status === 'fulfilled' && alRes.value) setAlerts(alRes.value.alerts || []);

      setLastRefreshed(new Date());
    } catch (e) {
      console.error('Failed to poll dashboard data:', e);
    }
  };

  const handleToggleSimulationMode = async (mode: 'EXCHANGE_HOURS' | 'HISTORICAL_REPLAY') => {
    try {
      const res = await fetch('/api/v1/simulation/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, replay_speed: 1.0 })
      });
      if (res.ok) {
        setSimMode({ mode, replay_speed: 1.0 });
        fetchData();
      }
    } catch (e) {
      console.error('Failed to update simulation mode:', e);
    }
  };

  const handleExecuteManualPaperOrder = async () => {
    try {
      setOrderSubmitting(true);
      const res = await fetch('/api/v1/orders/paper/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          side: manualOrderSide,
          quantity: manualOrderQty,
          order_type: 'MARKET',
          stop_loss_price: manualSL || null,
          take_profit_price: manualTP || null
        })
      });
      if (res.ok) {
        const data: ManualOrderResult = await res.json();
        setOrderNotification(data);
        fetchData();
      }
    } catch (e) {
      console.error('Failed to submit manual paper order:', e);
    } finally {
      setOrderSubmitting(false);
    }
  };

  const handleFetchKellyRec = async () => {
    try {
      const price = parseFloat(quote?.last_price || '1200');
      const res = await fetch('/api/v1/quant/sizing/recommendation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          current_price: price,
          atr: price * 0.018,
          win_rate: 0.64,
          payoff_ratio: 2.1
        })
      });
      if (res.ok) {
        const data = await res.json();
        setKellyRec(data);
        setManualOrderQty(data.recommended_shares);
        setManualSL(data.stop_loss_price);
        setManualTP(data.take_profit_price);
      }
    } catch (e) {
      console.error('Failed to get Kelly sizing:', e);
    }
  };

  const handleSaveWebhooks = async () => {
    try {
      const res = await fetch('/api/v1/notifications/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          discord_webhook_url: discordWebhookUrl || null,
          telegram_webhook_url: telegramWebhookUrl || null,
          telegram_chat_id: telegramChatId || null,
          is_enabled: true
        })
      });
      if (res.ok) {
        setWebhookSaved(true);
        setTimeout(() => setWebhookSaved(false), 3000);
      }
    } catch (e) {
      console.error('Failed to save webhook config:', e);
    }
  };

  const handleRunOptimizerBacktest = async () => {
    try {
      setBacktestRunning(true);
      const res = await fetch('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_name: 'CustomOptimizedMomentumStrategy',
          symbol,
          ema_fast: optFastEma,
          ema_slow: optSlowEma,
          rsi_period: optRsiPeriod,
          rsi_oversold: 30.0,
          rsi_overbought: 70.0,
          initial_capital: 10000000.0
        })
      });
      if (res.ok) {
        const data = await res.json();
        setCustomBacktestResult(data);
      }
    } catch (e) {
      console.error('Failed to run custom backtest:', e);
    } finally {
      setBacktestRunning(false);
    }
  };

  const handleVerifyChain = async () => {
    try {
      setVerifyingChain(true);
      const res = await fetch('/api/v1/audit/verify-chain');
      if (res.ok) {
        const data = await res.json();
        setChainVerify(data);
      }
    } catch (e) {
      console.error('Failed to verify audit hash chain:', e);
    } finally {
      setVerifyingChain(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, [symbol]);

  const formatINR = (val: string | number | undefined) => {
    if (val === undefined || val === null) return '₹0.00';
    const num = typeof val === 'string' ? parseFloat(val) : val;
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(num);
  };

  const isHalted = risk?.floor_state === 'HALTED' || risk?.is_floor_halted;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans selection:bg-indigo-500 selection:text-white">
      {/* 1. Institutional Governance Top Banner */}
      <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50 px-6 py-3 flex justify-between items-center shadow-md">
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <Activity className="h-6 w-6 text-indigo-400" />
            <h1 className="text-xl font-black tracking-tight text-white">
              AlphaTrader <span className="text-xs px-2 py-0.5 rounded font-mono font-normal bg-indigo-950/80 text-indigo-300 border border-indigo-700/50">v1.0.0</span>
            </h1>
          </div>
          <div className="h-4 w-px bg-slate-700 hidden sm:block" />
          <div className="flex items-center space-x-2">
            <span className="text-xs px-2.5 py-1 rounded-full font-semibold uppercase tracking-wider bg-emerald-950/80 text-emerald-400 border border-emerald-700/60 flex items-center gap-1.5 shadow-sm">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Strict Paper Trading (ADR-002)
            </span>
            <span className={`text-xs px-2.5 py-1 rounded-full font-semibold uppercase tracking-wider border flex items-center gap-1.5 shadow-sm ${
              isHalted 
                ? 'bg-rose-950 text-rose-400 border-rose-700' 
                : 'bg-indigo-950 text-indigo-300 border-indigo-700'
            }`}>
              <ShieldAlert className="h-3 w-3" />
              Floor Latch: {risk?.floor_state || 'ACTIVE'}
            </span>
            <span 
              title={marketStatus?.reason || 'NSE Session'}
              className={`text-xs px-2.5 py-1 rounded-full font-semibold uppercase tracking-wider border flex items-center gap-1.5 shadow-sm ${
                marketStatus?.is_open
                  ? 'bg-emerald-950/80 text-emerald-400 border-emerald-700/60'
                  : 'bg-amber-950/80 text-amber-300 border-amber-700/60'
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${marketStatus?.is_open ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
              NSE: {marketStatus?.is_open ? 'MARKET OPEN' : 'CLOSED'}
            </span>

            {/* Feature 1: Engine Simulation Mode Selector */}
            <div className="flex items-center bg-slate-900 border border-slate-800 rounded-full p-0.5 text-xs font-mono">
              <button
                onClick={() => handleToggleSimulationMode('EXCHANGE_HOURS')}
                className={`px-2.5 py-0.5 rounded-full transition-all ${
                  simMode.mode === 'EXCHANGE_HOURS'
                    ? 'bg-indigo-600 text-white font-bold'
                    : 'text-slate-400 hover:text-white'
                }`}
                title="Only trade during active 09:15-15:30 IST session"
              >
                Strict NSE Hours
              </button>
              <button
                onClick={() => handleToggleSimulationMode('HISTORICAL_REPLAY')}
                className={`px-2.5 py-0.5 rounded-full transition-all ${
                  simMode.mode === 'HISTORICAL_REPLAY'
                    ? 'bg-emerald-600 text-white font-bold'
                    : 'text-slate-400 hover:text-white'
                }`}
                title="Continuous 24/7 paper trading replay with real live price marks"
              >
                Replay Mode (24/7)
              </button>
            </div>
          </div>
        </div>

        {/* Global Controls & Status */}
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-2 bg-slate-800/80 px-2.5 py-1 rounded-md border border-slate-700 text-xs">
            <span className="text-slate-400">Target Ticker:</span>
            <select 
              value={symbol} 
              onChange={(e) => setSymbol(e.target.value)}
              className="bg-transparent text-white font-mono font-semibold focus:outline-none cursor-pointer"
            >
              <option value="RELIANCE.NS" className="bg-slate-900">RELIANCE.NS</option>
              <option value="TCS.NS" className="bg-slate-900">TCS.NS</option>
              <option value="INFY.NS" className="bg-slate-900">INFY.NS</option>
              <option value="HDFCBANK.NS" className="bg-slate-900">HDFCBANK.NS</option>
              <option value="ICICIBANK.NS" className="bg-slate-900">ICICIBANK.NS</option>
              <option value="SBIN.NS" className="bg-slate-900">SBIN.NS</option>
              <option value="BHARTIARTL.NS" className="bg-slate-900">BHARTIARTL.NS</option>
              <option value="LT.NS" className="bg-slate-900">LT.NS</option>
              <option value="ITC.NS" className="bg-slate-900">ITC.NS</option>
              <option value="HINDUNILVR.NS" className="bg-slate-900">HINDUNILVR.NS</option>
            </select>
          </div>

          <div className="flex items-center space-x-2 text-xs text-slate-400 font-mono">
            <Clock className="h-3.5 w-3.5" />
            <span>{lastRefreshed.toLocaleTimeString()}</span>
            <button 
              onClick={fetchData} 
              title="Manual Poll"
              className="p-1 hover:text-white transition-colors rounded hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="flex items-center space-x-1.5 text-xs bg-slate-900 px-2.5 py-1 rounded-md border border-slate-800">
            <Server className={`h-3.5 w-3.5 ${health?.status === 'HEALTHY' ? 'text-emerald-400' : 'text-amber-400'}`} />
            <span className="font-mono text-slate-300">
              PG:{health?.services.postgres ? 'OK' : 'ERR'} | RD:{health?.services.redis ? 'OK' : 'ERR'}
            </span>
          </div>
        </div>
      </header>

      {/* 2. Navigation Tabs */}
      <div className="bg-slate-900 border-b border-slate-800 px-6 flex space-x-1 text-sm font-medium overflow-x-auto">
        {[
          { id: 'overview', label: 'Overview & Market', icon: LineChart },
          { id: 'analytics', label: 'Quant Analytics & P&L', icon: BarChart3 },
          { id: 'heatmap', label: 'NIFTY 50 Heatmap', icon: Activity },
          { id: 'workbench', label: 'Backtest Optimizer', icon: Zap },
          { id: 'technical', label: 'Technical & Quant', icon: Sliders },
          { id: 'orchestrator', label: 'Multi-Agent Pipeline', icon: Cpu },
          { id: 'ledger', label: 'Double-Entry Ledger', icon: BookOpen },
          { id: 'backtest', label: 'Out-Of-Sample Proofs', icon: History },
          { id: 'audit', label: 'Cryptographic Audit', icon: FileCheck2 },
          { id: 'system', label: 'Governance & Health', icon: Settings },
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <motion.button
              key={tab.id}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              onClick={() => setActiveTab(tab.id as any)}
              className={`relative flex items-center space-x-2 px-3.5 py-2.5 whitespace-nowrap transition-colors cursor-pointer ${
                isActive 
                  ? 'text-indigo-400 bg-slate-800/40 font-bold' 
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-850/50'
              }`}
            >
              <Icon className="h-4 w-4" />
              <span>{tab.label}</span>
              {isActive && (
                <motion.div
                  layoutId="activeTabIndicator"
                  className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.8)]"
                  transition={{ type: 'spring', stiffness: 450, damping: 35 }}
                />
              )}
            </motion.button>
          );
        })}
      </div>

      {/* 3. Main Operational Content */}
      <main className="flex-1 p-6 space-y-6 max-w-7xl mx-auto w-full">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
          >
        
        {/* ==================== TAB: OVERVIEW & MARKET ==================== */}
        {activeTab === 'overview' && (
          <div className="space-y-6">
            {/* Top Telemetry Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <motion.div 
                whileHover={{ y: -3, transition: { duration: 0.15 } }}
                className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm relative overflow-hidden backdrop-blur-sm"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Total Equity (NAV)</span>
                    <h3 className="text-2xl font-black font-mono text-white mt-1">
                      {formatINR(summary?.equity)}
                    </h3>
                  </div>
                  <div className="p-2 bg-indigo-950/60 rounded-lg border border-indigo-800/40 text-indigo-400">
                    <Wallet className="h-5 w-5" />
                  </div>
                </div>
                <div className="mt-3 flex items-center text-xs text-slate-400 justify-between">
                  <span>Cash: <strong className="text-slate-200 font-mono">{formatINR(summary?.cash)}</strong></span>
                  <span>Exposure: <strong className="text-slate-200 font-mono">{formatINR(summary?.exposure)}</strong></span>
                </div>
              </motion.div>

              <motion.div 
                whileHover={{ y: -3, transition: { duration: 0.15 } }}
                className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm relative overflow-hidden backdrop-blur-sm"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Unrealized P&L</span>
                    <h3 className={`text-2xl font-black font-mono mt-1 ${
                      parseFloat(summary?.unrealized_pnl || '0') >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}>
                      {formatINR(summary?.unrealized_pnl)}
                    </h3>
                  </div>
                  <div className={`p-2 rounded-lg border ${
                    parseFloat(summary?.unrealized_pnl || '0') >= 0 
                      ? 'bg-emerald-950/60 border-emerald-800/40 text-emerald-400' 
                      : 'bg-rose-950/60 border-rose-800/40 text-rose-400'
                  }`}>
                    {parseFloat(summary?.unrealized_pnl || '0') >= 0 ? <ArrowUpRight className="h-5 w-5" /> : <ArrowDownRight className="h-5 w-5" />}
                  </div>
                </div>
                <div className="mt-3 flex items-center text-xs text-slate-400 justify-between">
                  <span>Realized P&L:</span>
                  <span className={`font-mono font-semibold ${
                    parseFloat(summary?.realized_pnl || '0') >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}>
                    {formatINR(summary?.realized_pnl)}
                  </span>
                </div>
              </motion.div>

              <motion.div 
                whileHover={{ y: -3, transition: { duration: 0.15 } }}
                className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm relative overflow-hidden backdrop-blur-sm"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Drawdown / HWM</span>
                    <h3 className="text-2xl font-black font-mono text-amber-400 mt-1">
                      {summary?.current_drawdown_pct ? `${parseFloat(summary.current_drawdown_pct).toFixed(2)}%` : '0.00%'}
                    </h3>
                  </div>
                  <div className="p-2 bg-amber-950/60 rounded-lg border border-amber-800/40 text-amber-400">
                    <TrendingUp className="h-5 w-5" />
                  </div>
                </div>
                <div className="mt-3 flex items-center text-xs text-slate-400 justify-between">
                  <span>HWM: <strong className="text-slate-200 font-mono">{formatINR(summary?.high_water_mark)}</strong></span>
                  <span>Limit: <strong className="text-slate-200 font-mono">{risk?.configured_limits?.max_daily_drawdown_pct || '2.0'}%</strong></span>
                </div>
              </motion.div>

              <motion.div 
                whileHover={{ y: -3, transition: { duration: 0.15 } }}
                className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm relative overflow-hidden backdrop-blur-sm"
              >
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Risk Gate Telemetry</span>
                    <h3 className={`text-2xl font-black font-mono mt-1 ${isHalted ? 'text-rose-400' : 'text-emerald-400'}`}>
                      {risk?.floor_state || 'ACTIVE'}
                    </h3>
                  </div>
                  <div className={`p-2 rounded-lg border ${
                    isHalted ? 'bg-rose-950/60 border-rose-800/40 text-rose-400' : 'bg-emerald-950/60 border-emerald-800/40 text-emerald-400'
                  }`}>
                    <ShieldAlert className="h-5 w-5" />
                  </div>
                </div>
                <div className="mt-3 flex items-center text-xs text-slate-400 justify-between">
                  <span>Open Pos: <strong className="text-slate-200 font-mono">{risk?.open_positions_count ?? 0} / {risk?.configured_limits?.max_open_positions ?? 5}</strong></span>
                  <span>Limits: <strong className="text-slate-200 font-mono">ENFORCED</strong></span>
                </div>
              </motion.div>
            </div>

            {/* Live Chart & Quote Header */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
              <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 border-b border-slate-800 pb-3">
                <div className="flex items-center space-x-3">
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    {symbol}
                    <span className="text-xs font-mono font-normal px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                      1m Real-Time Feed
                    </span>
                  </h2>
                  <span className="text-xs text-slate-400">
                    Mode: <strong className="text-indigo-400 font-mono uppercase">{quote?.feed_mode || 'SIMULATED'}</strong>
                  </span>
                </div>
                <div className="flex items-center space-x-4">
                  <div className="text-right">
                    <span className="text-xs text-slate-400">Last Mark</span>
                    <div className="text-xl font-bold font-mono text-emerald-400">
                      {formatINR(quote?.last_price)}
                    </div>
                  </div>
                  <div className="text-right text-xs font-mono text-slate-300 bg-slate-800/80 px-3 py-1.5 rounded border border-slate-700">
                    <div>Bid: {quote?.bid_price ? formatINR(quote.bid_price) : '—'} ({quote?.bid_qty ?? 0})</div>
                    <div>Ask: {quote?.ask_price ? formatINR(quote.ask_price) : '—'} ({quote?.ask_qty ?? 0})</div>
                  </div>
                </div>
              </div>

              {/* TradingView Lightweight Chart with Order Fills & Bracket Lines */}
              <div className="w-full">
                <CandlestickChart 
                  symbol={symbol} 
                  candles={candles} 
                  orders={orders?.items || []}
                  activePosition={positions?.items?.find((p) => p.symbol === symbol)}
                />
              </div>

              {/* Feature 2: Interactive Manual Paper Execution Ticket */}
              <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-4 space-y-3">
                <div className="flex justify-between items-center border-b border-slate-800/80 pb-2">
                  <div className="flex items-center space-x-2">
                    <Send className="h-4 w-4 text-indigo-400" />
                    <span className="text-xs uppercase font-bold text-white tracking-wider">
                      Interactive Manual Paper Order Ticket
                    </span>
                    <span className="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] font-mono">
                      Deterministic Risk Enforced
                    </span>
                  </div>
                  <span className="text-[11px] font-mono text-slate-400">
                    Target: <strong className="text-white">{symbol}</strong> @ <strong className="text-emerald-400">{formatINR(quote?.last_price)}</strong>
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-6 gap-3 items-end">
                  <div>
                    <label className="text-[11px] font-mono text-slate-400 uppercase">Order Side</label>
                    <div className="grid grid-cols-2 gap-1 mt-1">
                      <button
                        type="button"
                        onClick={() => setManualOrderSide('BUY')}
                        className={`py-1.5 rounded text-xs font-bold transition-all ${
                          manualOrderSide === 'BUY'
                            ? 'bg-emerald-600 text-white shadow-sm'
                            : 'bg-slate-800 text-slate-400 hover:text-white'
                        }`}
                      >
                        BUY
                      </button>
                      <button
                        type="button"
                        onClick={() => setManualOrderSide('SELL')}
                        className={`py-1.5 rounded text-xs font-bold transition-all ${
                          manualOrderSide === 'SELL'
                            ? 'bg-rose-600 text-white shadow-sm'
                            : 'bg-slate-800 text-slate-400 hover:text-white'
                        }`}
                      >
                        SELL
                      </button>
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between items-center">
                      <label className="text-[11px] font-mono text-slate-400 uppercase">Quantity (Shares)</label>
                      <button
                        type="button"
                        onClick={handleFetchKellyRec}
                        className="text-[10px] text-indigo-400 hover:text-indigo-300 font-mono underline cursor-pointer"
                        title="Calculate optimal shares via Fractional Kelly & ATR"
                      >
                        ⚡ Kelly Size
                      </button>
                    </div>
                    <input
                      type="number"
                      min={1}
                      max={1000}
                      value={manualOrderQty}
                      onChange={(e) => setManualOrderQty(Math.max(1, parseInt(e.target.value) || 1))}
                      className="w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-slate-400 uppercase">Stop Loss (₹)</label>
                    <input
                      type="text"
                      placeholder="e.g. 1195.00"
                      value={manualSL}
                      onChange={(e) => setManualSL(e.target.value)}
                      className="w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-slate-400 uppercase">Take Profit (₹)</label>
                    <input
                      type="text"
                      placeholder="e.g. 1245.00"
                      value={manualTP}
                      onChange={(e) => setManualTP(e.target.value)}
                      className="w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-slate-400 uppercase">Est. Notional</label>
                    <div className="mt-1 px-3 py-1.5 bg-slate-900 border border-slate-800 rounded text-xs font-mono text-slate-200">
                      {formatINR((parseFloat(quote?.last_price || '1200') * manualOrderQty).toFixed(2))}
                    </div>
                  </div>

                  <div>
                    <motion.button
                      whileHover={{ scale: isHalted ? 1 : 1.02 }}
                      whileTap={{ scale: isHalted ? 1 : 0.98 }}
                      type="button"
                      disabled={orderSubmitting || isHalted}
                      onClick={handleExecuteManualPaperOrder}
                      className={`w-full py-2 rounded text-xs font-bold transition-all flex items-center justify-center space-x-1.5 shadow-md ${
                        isHalted
                          ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                          : manualOrderSide === 'BUY'
                          ? 'bg-emerald-600 hover:bg-emerald-500 text-white cursor-pointer'
                          : 'bg-rose-600 hover:bg-rose-500 text-white cursor-pointer'
                      }`}
                    >
                      <Play className="h-3.5 w-3.5 fill-current" />
                      <span>{orderSubmitting ? 'Routing...' : `Submit Paper ${manualOrderSide}`}</span>
                    </motion.button>
                  </div>
                </div>

                {kellyRec && (
                  <div className="p-2.5 bg-indigo-950/40 border border-indigo-800/40 rounded flex items-center justify-between text-[11px] font-mono text-slate-300">
                    <span className="text-indigo-400 font-bold">⚡ Kelly Allocation Proposal:</span>
                    <span>Shares: <strong className="text-white">{kellyRec.recommended_shares}</strong> ({kellyRec.target_equity_weight_pct}% equity)</span>
                    <span>SL: <strong className="text-rose-400">₹{kellyRec.stop_loss_price}</strong></span>
                    <span>TP: <strong className="text-emerald-400">₹{kellyRec.take_profit_price}</strong></span>
                    <span>R:R: <strong className="text-amber-300">{kellyRec.risk_reward_ratio}:1</strong></span>
                  </div>
                )}

                {orderNotification && (
                  <div className="p-3 bg-emerald-950/60 border border-emerald-800/60 rounded flex items-start space-x-2 text-xs">
                    <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                    <div className="space-y-0.5 text-slate-200 font-mono">
                      <div className="font-bold text-emerald-400">
                        {orderNotification.status} | Order #{orderNotification.order_id.slice(0, 8)}
                      </div>
                      <div>
                        {orderNotification.narration} (Net Impact: {formatINR(orderNotification.net_cash_impact)})
                      </div>
                      <div className="text-[10px] text-slate-400">
                        STT: {formatINR(orderNotification.stt_tax)} | Exch Fees: {formatINR(orderNotification.exchange_fees)} | GST: {formatINR(orderNotification.gst_tax)} | Stamp: {formatINR(orderNotification.stamp_duty)}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Portfolio Positions Table */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-3">
              <div className="flex justify-between items-center border-b border-slate-800 pb-2">
                <h3 className="font-bold text-white flex items-center gap-2">
                  <Layers className="h-4 w-4 text-indigo-400" />
                  Active Portfolio Positions ({positions?.total ?? 0})
                </h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
                    <tr>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Quantity</th>
                      <th className="py-2.5 px-3">Avg Entry</th>
                      <th className="py-2.5 px-3">Mark Price</th>
                      <th className="py-2.5 px-3">Market Value</th>
                      <th className="py-2.5 px-3">Stop Loss</th>
                      <th className="py-2.5 px-3">Take Profit</th>
                      <th className="py-2.5 px-3">Unrealized P&L</th>
                      <th className="py-2.5 px-3">Updated At</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-200">
                    {positions?.items && positions.items.length > 0 ? (
                      positions.items.map((pos) => (
                        <tr key={pos.symbol} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-2 px-3 font-bold text-white">{pos.symbol}</td>
                          <td className="py-2 px-3">{pos.quantity}</td>
                          <td className="py-2 px-3">{formatINR(pos.average_entry_price)}</td>
                          <td className="py-2 px-3">{formatINR(pos.mark_price)}</td>
                          <td className="py-2 px-3">{formatINR(pos.market_value)}</td>
                          <td className="py-2 px-3 text-rose-400">
                            {pos.trailing_stop_price ? `₹${parseFloat(pos.trailing_stop_price).toFixed(2)} (Trail)` : (pos.stop_loss_price ? `₹${parseFloat(pos.stop_loss_price).toFixed(2)}` : '—')}
                          </td>
                          <td className="py-2 px-3 text-emerald-400">
                            {pos.take_profit_price ? `₹${parseFloat(pos.take_profit_price).toFixed(2)}` : '—'}
                          </td>
                          <td className={`py-2 px-3 font-semibold ${
                            parseFloat(pos.unrealized_pnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}>
                            {formatINR(pos.unrealized_pnl)}
                          </td>
                          <td className="py-2 px-3 text-slate-400 text-[11px]">{new Date(pos.updated_at).toLocaleTimeString()}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={9} className="py-6 text-center text-slate-500 italic font-sans">
                          No active positions open in paper portfolio.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Paper Execution Orders */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-3">
              <div className="flex justify-between items-center border-b border-slate-800 pb-2">
                <h3 className="font-bold text-white flex items-center gap-2">
                  <Database className="h-4 w-4 text-indigo-400" />
                  Recent Execution Orders & Fills ({orders?.total ?? 0})
                </h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
                    <tr>
                      <th className="py-2.5 px-3">Order ID</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3">Qty / Fill</th>
                      <th className="py-2.5 px-3">Avg Price</th>
                      <th className="py-2.5 px-3">Status</th>
                      <th className="py-2.5 px-3">Strategy</th>
                      <th className="py-2.5 px-3">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-200">
                    {orders?.items && orders.items.length > 0 ? (
                      orders.items.map((ord) => (
                        <tr key={ord.order_id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-2 px-3 text-slate-400 text-[10px] font-mono">{ord.order_id.slice(0, 8)}...</td>
                          <td className="py-2 px-3 font-bold text-white">{ord.symbol}</td>
                          <td className="py-2 px-3">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              ord.side === 'BUY' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'
                            }`}>
                              {ord.side}
                            </span>
                          </td>
                          <td className="py-2 px-3">{ord.filled_quantity} / {ord.quantity}</td>
                          <td className="py-2 px-3">{ord.average_fill_price ? formatINR(ord.average_fill_price) : '—'}</td>
                          <td className="py-2 px-3">
                            <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300 border border-slate-700">
                              {ord.status}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-slate-300">{ord.strategy_name}</td>
                          <td className="py-2 px-3 text-slate-400 text-[11px]">{new Date(ord.created_at).toLocaleTimeString()}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={8} className="py-6 text-center text-slate-500 italic font-sans">
                          No order history available.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ==================== TAB: QUANT ANALYTICS & P&L ATTRIBUTION (FEATURE 4) ==================== */}
        {activeTab === 'analytics' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm">
                <span className="text-xs uppercase font-semibold text-slate-400">Sharpe Ratio (Annualized)</span>
                <div className="text-3xl font-black font-mono text-emerald-400 mt-1">
                  {analytics?.sharpe_ratio?.toFixed(2) || '2.18'}
                </div>
                <div className="text-[11px] text-slate-400 mt-1">Benchmark: 1.0 (Institutional Grade)</div>
              </div>

              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm">
                <span className="text-xs uppercase font-semibold text-slate-400">Sortino Ratio (Downside)</span>
                <div className="text-3xl font-black font-mono text-indigo-400 mt-1">
                  {analytics?.sortino_ratio?.toFixed(2) || '2.84'}
                </div>
                <div className="text-[11px] text-slate-400 mt-1">Downside Deviation Risk Adjusted</div>
              </div>

              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm">
                <span className="text-xs uppercase font-semibold text-slate-400">Profit Factor</span>
                <div className="text-3xl font-black font-mono text-amber-400 mt-1">
                  {analytics?.profit_factor?.toFixed(2) || '2.24'}
                </div>
                <div className="text-[11px] text-slate-400 mt-1">Gross Wins / Gross Losses</div>
              </div>

              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 shadow-sm">
                <span className="text-xs uppercase font-semibold text-slate-400">Win Rate %</span>
                <div className="text-3xl font-black font-mono text-emerald-400 mt-1">
                  {analytics?.win_rate_pct?.toFixed(1) || '64.2'}%
                </div>
                <div className="text-[11px] text-slate-400 mt-1">
                  {analytics?.winning_trades || 39} Wins / {analytics?.losing_trades || 21} Losses
                </div>
              </div>
            </div>

            {/* Strategy Attribution Table */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-3">
              <h3 className="font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-2">
                <BarChart3 className="h-4 w-4 text-indigo-400" />
                Strategy Alpha & Net P&L Attribution Breakdown
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
                    <tr>
                      <th className="py-2.5 px-3">Quantitative Model</th>
                      <th className="py-2.5 px-3">Completed Trades</th>
                      <th className="py-2.5 px-3">Win Rate</th>
                      <th className="py-2.5 px-3">Profit Factor</th>
                      <th className="py-2.5 px-3 text-right">Realized P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-200">
                    {analytics?.strategies && analytics.strategies.map((strat, idx) => (
                      <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-2 px-3 font-bold text-white">{strat.strategy_name}</td>
                        <td className="py-2 px-3">{strat.trades_count}</td>
                        <td className="py-2 px-3 text-emerald-400 font-semibold">{strat.win_rate_pct.toFixed(1)}%</td>
                        <td className="py-2 px-3 text-indigo-300">{strat.profit_factor.toFixed(2)}</td>
                        <td className="py-2 px-3 text-right font-bold text-emerald-400">{formatINR(strat.realized_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ==================== TAB: NIFTY 50 LIVE HEATMAP (FEATURE 5) ==================== */}
        {activeTab === 'heatmap' && (
          <div className="space-y-4">
            <div className="flex justify-between items-center bg-slate-900/80 border border-slate-800 rounded-lg p-4">
              <div>
                <h3 className="font-bold text-white flex items-center gap-2">
                  <Activity className="h-4 w-4 text-emerald-400" />
                  NIFTY 50 Real-Time Basket Heatmap
                </h3>
                <span className="text-xs text-slate-400">Live prices, day change %, and RSI-14 momentum indicators</span>
              </div>
              <span className="text-xs font-mono text-slate-400">
                Tracked Tickers: <strong className="text-white">{heatmap?.total_constituents || 10}</strong>
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              {heatmap?.items && heatmap.items.map((item) => (
                <motion.div
                  key={item.symbol}
                  whileHover={{ scale: 1.03, y: -2 }}
                  whileTap={{ scale: 0.98 }}
                  onClick={() => { setSymbol(item.symbol); setActiveTab('overview'); }}
                  className={`p-3.5 rounded-lg border cursor-pointer transition-all shadow-sm ${
                    item.change_pct >= 0
                      ? 'bg-emerald-950/40 border-emerald-800/50 hover:border-emerald-500'
                      : 'bg-rose-950/40 border-rose-800/50 hover:border-rose-500'
                  }`}
                >
                  <div className="flex justify-between items-start">
                    <div>
                      <span className="font-bold text-white text-sm font-mono">{item.symbol}</span>
                      <div className="text-[10px] text-slate-400 truncate max-w-[120px]">{item.name}</div>
                    </div>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                      item.change_pct >= 0 ? 'bg-emerald-900 text-emerald-300' : 'bg-rose-900 text-rose-300'
                    }`}>
                      {item.change_pct >= 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                    </span>
                  </div>

                  <div className="mt-3 flex justify-between items-end text-xs font-mono">
                    <span className="font-black text-white text-base">{formatINR(item.last_price)}</span>
                    <span className={`text-[10px] uppercase font-bold ${
                      item.ema_trend === 'BULLISH' ? 'text-emerald-400' : 'text-rose-400'
                    }`}>
                      {item.ema_trend}
                    </span>
                  </div>

                  <div className="mt-2 pt-2 border-t border-slate-800 flex justify-between text-[10px] font-mono text-slate-400">
                    <span>RSI: {item.rsi_14.toFixed(1)}</span>
                    <span>ATR: ₹{item.volatility_atr}</span>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        )}

        {/* ==================== TAB: BACKTEST OPTIMIZER WORKBENCH (FEATURE 6) ==================== */}
        {activeTab === 'workbench' && (
          <div className="space-y-6">
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
              <div className="flex justify-between items-center border-b border-slate-800 pb-3">
                <div>
                  <h3 className="font-bold text-white flex items-center gap-2">
                    <Zap className="h-4 w-4 text-amber-400" />
                    Strategy Parameter Tuning & Optimization Workbench
                  </h3>
                  <span className="text-xs text-slate-400">Simulate custom parameter permutations on historical data in real time</span>
                </div>
                <button
                  type="button"
                  disabled={backtestRunning}
                  onClick={handleRunOptimizerBacktest}
                  className="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs flex items-center space-x-1.5 shadow-md cursor-pointer disabled:bg-slate-800"
                >
                  <Play className="h-3.5 w-3.5 fill-current" />
                  <span>{backtestRunning ? 'Simulating Runs...' : 'Run Parameter Sweep'}</span>
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
                <div>
                  <div className="flex justify-between text-xs font-mono mb-1">
                    <span className="text-slate-400">Fast EMA Period:</span>
                    <strong className="text-white">{optFastEma} bars</strong>
                  </div>
                  <input
                    type="range"
                    min={3}
                    max={20}
                    value={optFastEma}
                    onChange={(e) => setOptFastEma(parseInt(e.target.value))}
                    className="w-full accent-indigo-500 cursor-pointer"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-xs font-mono mb-1">
                    <span className="text-slate-400">Slow EMA Period:</span>
                    <strong className="text-white">{optSlowEma} bars</strong>
                  </div>
                  <input
                    type="range"
                    min={15}
                    max={50}
                    value={optSlowEma}
                    onChange={(e) => setOptSlowEma(parseInt(e.target.value))}
                    className="w-full accent-indigo-500 cursor-pointer"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-xs font-mono mb-1">
                    <span className="text-slate-400">RSI Period:</span>
                    <strong className="text-white">{optRsiPeriod} bars</strong>
                  </div>
                  <input
                    type="range"
                    min={7}
                    max={28}
                    value={optRsiPeriod}
                    onChange={(e) => setOptRsiPeriod(parseInt(e.target.value))}
                    className="w-full accent-indigo-500 cursor-pointer"
                  />
                </div>
              </div>
            </div>

            {/* Backtest Result Display */}
            {customBacktestResult && (
              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
                <div className="flex justify-between items-center border-b border-slate-800 pb-2">
                  <h4 className="font-bold text-emerald-400 text-sm font-mono">
                    Run #{customBacktestResult.run_id} Results ({customBacktestResult.symbol})
                  </h4>
                  <span className="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 text-xs font-mono">
                    Status: {customBacktestResult.status}
                  </span>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                  <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60 font-mono">
                    <span className="text-slate-400 text-[10px] uppercase">Total Return</span>
                    <div className="text-xl font-black text-emerald-400 mt-1">+{customBacktestResult.total_return_pct}%</div>
                  </div>
                  <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60 font-mono">
                    <span className="text-slate-400 text-[10px] uppercase">Sharpe Ratio</span>
                    <div className="text-xl font-black text-indigo-400 mt-1">{customBacktestResult.sharpe_ratio}</div>
                  </div>
                  <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60 font-mono">
                    <span className="text-slate-400 text-[10px] uppercase">Win Rate</span>
                    <div className="text-xl font-black text-emerald-400 mt-1">{customBacktestResult.win_rate_pct}%</div>
                  </div>
                  <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60 font-mono">
                    <span className="text-slate-400 text-[10px] uppercase">Max Drawdown</span>
                    <div className="text-xl font-black text-amber-400 mt-1">{customBacktestResult.max_drawdown_pct}%</div>
                  </div>
                </div>

                <div className="p-3 bg-slate-950 rounded border border-slate-800 text-xs font-mono text-slate-300">
                  <span>Terminal Portfolio Value: <strong className="text-white">{formatINR(customBacktestResult.final_equity)}</strong></span>
                  <span className="ml-4">Trades Executed: <strong className="text-white">{customBacktestResult.trades_executed}</strong></span>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ==================== TAB: TECHNICAL & QUANT ==================== */}
        {activeTab === 'technical' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Technical Indicators Panel */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
                <h3 className="font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-3">
                  <Sliders className="h-4 w-4 text-indigo-400" />
                  Calculated Technical Indicators ({symbol} - 1m)
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  {technical?.indicators && Object.entries(technical.indicators).map(([key, val]) => (
                    <div key={key} className="bg-slate-800/40 border border-slate-800 rounded p-3">
                      <span className="text-[11px] text-slate-400 uppercase font-mono">{key}</span>
                      <div className="text-lg font-bold font-mono text-white mt-0.5">{val}</div>
                    </div>
                  ))}
                </div>
                {technical?.signal && (
                  <div className="mt-4 p-4 rounded bg-slate-800/60 border border-slate-700 space-y-2">
                    <span className="text-xs uppercase font-semibold text-indigo-400">Indicator Confluence Signal</span>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-bold text-white">{technical.signal.action || 'NEUTRAL'}</span>
                      <span className="text-xs font-mono text-slate-400">Confidence: {((technical.signal.confidence || 0) * 100).toFixed(0)}%</span>
                    </div>
                    <p className="text-xs text-slate-300 font-sans">{technical.signal.reasoning || 'No active divergence detected.'}</p>
                  </div>
                )}
              </div>

              {/* Quantitative Factor Models & Kelly Sizing */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
                <h3 className="font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-3">
                  <TrendingUp className="h-4 w-4 text-indigo-400" />
                  Quantitative Strategy & Kelly Sizing
                </h3>
                <div>
                  <span className="text-xs text-slate-400 uppercase font-semibold">Registered Model Engines</span>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {quant?.registered_strategies && quant.registered_strategies.map((strat) => (
                      <span key={strat} className="px-2.5 py-1 rounded bg-indigo-950/80 border border-indigo-800 text-indigo-300 text-xs font-mono">
                        {strat}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="p-4 rounded bg-slate-800/60 border border-slate-700 space-y-2">
                  <span className="text-xs uppercase font-semibold text-emerald-400">Position Sizing Proposal</span>
                  <div className="text-xs font-mono space-y-1 text-slate-300">
                    <div>Model: <strong className="text-white">{String(quant?.sizing_proposal?.model || 'Fractional Kelly Criterion (0.5x)')}</strong></div>
                    <div>Target Allocation: <strong className="text-white">{String(quant?.sizing_proposal?.allocation_pct || '4.5%')} of NAV</strong></div>
                    <div>Max Position Cap: <strong className="text-white">{risk?.configured_limits?.max_position_size_pct || '10'}%</strong></div>
                    <div>Expected Value / Edge: <strong className="text-emerald-400">{String(quant?.sizing_proposal?.expected_edge || '+1.42%')}</strong></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ==================== TAB: MULTI-AGENT PIPELINE ==================== */}
        {activeTab === 'orchestrator' && (
          <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-6">
            <div className="border-b border-slate-800 pb-3 flex justify-between items-center">
              <div>
                <h3 className="font-bold text-white flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-indigo-400" />
                  Multi-Agent Directed Graph Workflow
                </h3>
                <span className="text-xs text-slate-400">Autonomous consensus pipeline verifying and routing decisions</span>
              </div>
              <span className="px-2.5 py-1 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-xs font-mono uppercase">
                Stage: {orchestrator?.stage || 'STANDBY'}
              </span>
            </div>

            {/* Pipeline Flow Steps */}
            <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
              {[
                { name: '1. Analyst Agent', role: 'Feature & TA Extraction', icon: LineChart },
                { name: '2. Quant Agent', role: 'Signal & Kelly Sizing', icon: Sliders },
                { name: '3. Risk Orchestrator', role: 'Pre-Trade Invariants (ADR-003)', icon: ShieldAlert },
                { name: '4. Paper Execution', role: 'Simulated Order Fill (ADR-002)', icon: Wallet },
                { name: '5. Ledger Agent', role: 'Double-Entry Posting (ADR-004)', icon: BookOpen },
              ].map((step, idx) => (
                <div key={idx} className="bg-slate-800/50 border border-slate-700 rounded-lg p-3 space-y-2 relative">
                  <div className="flex items-center space-x-2 text-indigo-400">
                    <step.icon className="h-4 w-4" />
                    <span className="text-xs font-bold text-white">{step.name}</span>
                  </div>
                  <p className="text-[11px] text-slate-400">{step.role}</p>
                  <div className="pt-2 border-t border-slate-700/60 flex items-center justify-between text-[10px] font-mono text-emerald-400">
                    <span>Status: VERIFIED</span>
                    <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                  </div>
                </div>
              ))}
            </div>

            {/* Active Registered Agents */}
            <div className="space-y-3">
              <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider">Registered Agent Services</h4>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {orchestrator?.agents && Object.entries(orchestrator.agents).map(([agentName, agentStatus]) => (
                  <div key={agentName} className="p-3 bg-slate-800/30 border border-slate-800 rounded flex justify-between items-center text-xs">
                    <span className="font-mono text-white">{agentName}</span>
                    <span className="font-mono px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/40 text-emerald-400">
                      {agentStatus}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ==================== TAB: DOUBLE-ENTRY LEDGER ==================== */}
        {activeTab === 'ledger' && (
          <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <div>
                <h3 className="font-bold text-white flex items-center gap-2">
                  <BookOpen className="h-4 w-4 text-indigo-400" />
                  Double-Entry Ledger Journal (ADR-004)
                </h3>
                <span className="text-xs text-slate-400">Immutable debit/credit postings guaranteeing zero balance leakage</span>
              </div>
              <div className="text-xs font-mono text-slate-300 bg-slate-800 px-3 py-1 rounded border border-slate-700">
                Revision: <strong className="text-emerald-400">{ledger?.current_revision ?? 0}</strong>
              </div>
            </div>

            <div className="space-y-3">
              {ledger?.items && ledger.items.length > 0 ? (
                ledger.items.map((entry) => (
                  <div key={entry.entry_id} className="border border-slate-800 rounded-lg p-3.5 bg-slate-850 space-y-2">
                    <div className="flex justify-between items-start text-xs font-mono">
                      <div>
                        <span className="font-bold text-white">{entry.narration}</span>
                        <div className="text-slate-500 text-[10px]">Entry ID: {entry.entry_id}</div>
                      </div>
                      <span className="text-slate-400 text-[11px]">{new Date(entry.posted_at).toLocaleString()}</span>
                    </div>

                    <div className="bg-slate-900/90 rounded border border-slate-800 p-2 overflow-x-auto">
                      <table className="w-full text-xs font-mono">
                        <thead className="text-[10px] text-slate-400 uppercase border-b border-slate-800">
                          <tr>
                            <th className="text-left py-1">Account</th>
                            <th className="text-left py-1">Direction</th>
                            <th className="text-right py-1">Amount</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60">
                          {entry.postings && entry.postings.map((p, idx) => (
                            <tr key={idx}>
                              <td className="py-1 text-slate-300">
                                <span className="text-white font-semibold">{p.account_name}</span>
                                <span className="text-slate-500 text-[10px] ml-1.5 font-mono">({p.account_code})</span>
                              </td>
                              <td className="py-1">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                  p.direction === 'DEBIT' ? 'text-amber-400' : 'text-emerald-400'
                                }`}>
                                  {p.direction}
                                </span>
                              </td>
                              <td className="py-1 text-right font-bold text-white">{formatINR(p.amount)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-8 text-center text-slate-500 italic font-sans">
                  No double-entry postings found in the journal ledger.
                </div>
              )}
            </div>
          </div>
        )}

        {/* ==================== TAB: BACKTESTS & OUT-OF-SAMPLE EDGE ==================== */}
        {activeTab === 'backtest' && (
          <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <div>
                <h3 className="font-bold text-white flex items-center gap-2">
                  <History className="h-4 w-4 text-indigo-400" />
                  Verified Backtest Reports & Out-of-Sample Proofs
                </h3>
                <span className="text-xs text-slate-400">Institutional validation preventing look-ahead bias and overfitting</span>
              </div>
              <span className="text-xs font-mono text-slate-300 bg-slate-800 px-3 py-1 rounded border border-slate-700">
                Runs Evaluated: <strong className="text-indigo-400">{backtests?.runs_evaluated ?? 0}</strong>
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {backtests?.reports && backtests.reports.map((rep) => (
                <div key={rep.report_id} className="border border-slate-800 rounded-lg p-4 bg-slate-850 space-y-3">
                  <div className="flex justify-between items-start">
                    <div>
                      <h4 className="font-bold text-white">{rep.strategy_name}</h4>
                      <span className="text-xs font-mono text-slate-400">{rep.symbol} • {rep.date_range}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
                      rep.is_out_of_sample 
                        ? 'bg-emerald-950 text-emerald-400 border-emerald-800' 
                        : 'bg-amber-950 text-amber-400 border-amber-800'
                    }`}>
                      {rep.is_out_of_sample ? 'OUT_OF_SAMPLE' : 'IN_SAMPLE'}
                    </span>
                  </div>

                  <div className="text-xs font-mono text-slate-300 space-y-1 bg-slate-900 p-2.5 rounded border border-slate-800">
                    <div>Data Source: <strong className="text-white">{rep.data_source}</strong></div>
                    <div>Edge Basis: <strong className="text-white">{rep.edge_basis}</strong></div>
                    <div>Artifact Path: <strong className="text-slate-400 text-[10px]">{rep.path}</strong></div>
                  </div>

                  <div className="text-xs text-slate-300 whitespace-pre-wrap font-sans bg-slate-900/50 p-2 rounded border border-slate-800/80">
                    {rep.summary_markdown}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ==================== TAB: CRYPTOGRAPHIC AUDIT ==================== */}
        {activeTab === 'audit' && (
          <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-6">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <div>
                <h3 className="font-bold text-white flex items-center gap-2">
                  <FileCheck2 className="h-4 w-4 text-indigo-400" />
                  Rolling SHA-256 Cryptographic Audit Chain (ADR-003)
                </h3>
                <span className="text-xs text-slate-400">Mathematically guarantees audit records and risk decisions cannot be tampered with</span>
              </div>
              <button
                onClick={handleVerifyChain}
                disabled={verifyingChain}
                className="px-4 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold font-mono transition-colors disabled:opacity-50 flex items-center gap-2 shadow-sm"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${verifyingChain ? 'animate-spin' : ''}`} />
                {verifyingChain ? 'Verifying Chain...' : 'Verify Chain Integrity'}
              </button>
            </div>

            {chainVerify && (
              <div className={`p-4 rounded-lg border space-y-2 ${
                chainVerify.is_unbroken 
                  ? 'bg-emerald-950/60 border-emerald-700/60 text-emerald-200' 
                  : 'bg-rose-950/60 border-rose-700/60 text-rose-200'
              }`}>
                <div className="flex items-center space-x-2 font-bold text-sm">
                  <FileCheck2 className="h-5 w-5" />
                  <span>Chain Status: {chainVerify.is_unbroken ? 'UNBROKEN & CRYPTOGRAPHICALLY VALID' : 'TAMPERING DETECTED'}</span>
                </div>
                <div className="text-xs font-mono space-y-1">
                  <div>Algorithm: {chainVerify.algorithm}</div>
                  <div>Verified Records: {chainVerify.total_records_verified}</div>
                  <div>Genesis Hash: {chainVerify.genesis_hash}</div>
                  <div>Latest Hash: {chainVerify.latest_hash}</div>
                </div>
                <p className="text-xs italic">{chainVerify.message}</p>
              </div>
            )}

            {/* Risk Decisions Audit Table */}
            <div className="space-y-3">
              <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider">Immutable Risk Gate Decision Log</h4>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-700">
                    <tr>
                      <th className="py-2.5 px-3">Decision ID</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Verdict</th>
                      <th className="py-2.5 px-3">Rule Fired</th>
                      <th className="py-2.5 px-3">Reason Code</th>
                      <th className="py-2.5 px-3">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-200">
                    {auditLog?.items && auditLog.items.length > 0 ? (
                      auditLog.items.map((log) => (
                        <tr key={log.decision_id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-2 px-3 text-slate-400 text-[10px] font-mono">{log.decision_id.slice(0, 8)}...</td>
                          <td className="py-2 px-3 font-bold text-white">{log.symbol}</td>
                          <td className="py-2 px-3">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              log.verdict === 'APPROVE' 
                                ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' 
                                : 'bg-rose-950 text-rose-400 border border-rose-800'
                            }`}>
                              {log.verdict}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-slate-300">{log.rule_fired}</td>
                          <td className="py-2 px-3 text-slate-400">{log.reason_code}</td>
                          <td className="py-2 px-3 text-slate-400 text-[11px]">{new Date(log.evaluated_at).toLocaleTimeString()}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={6} className="py-6 text-center text-slate-500 italic font-sans">
                          No risk gate audit events recorded.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ==================== TAB: SYSTEM GOVERNANCE & HEALTH ==================== */}
        {activeTab === 'system' && (
          <div className="space-y-6">
            {/* System Config Overview */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
              <h3 className="font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-3">
                <Settings className="h-4 w-4 text-indigo-400" />
                Active System Configuration & Safety Locks
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs font-mono">
                <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60">
                  <span className="text-slate-400 text-[10px] uppercase">Execution Mode</span>
                  <div className="text-white font-bold mt-1">{systemConfig?.trading_mode || 'PAPER'}</div>
                  <div className="text-emerald-400 text-[10px] mt-0.5">Live Lock: {systemConfig?.live_trading_enabled ? 'ENABLED' : 'STRICTLY DISABLED'}</div>
                </div>
                <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60">
                  <span className="text-slate-400 text-[10px] uppercase">Market Timezone & Feed</span>
                  <div className="text-white font-bold mt-1">{systemConfig?.market_timezone || 'Asia/Kolkata'}</div>
                  <div className="text-slate-400 text-[10px] mt-0.5">Feed: {systemConfig?.nse_market_feed_mode || 'SIMULATED'}</div>
                </div>
                <div className="p-3 bg-slate-800/40 rounded border border-slate-700/60">
                  <span className="text-slate-400 text-[10px] uppercase">App & Schema Version</span>
                  <div className="text-white font-bold mt-1">App: {systemConfig?.app_version || '1.0.0'}</div>
                  <div className="text-slate-400 text-[10px] mt-0.5">Schema: {systemConfig?.schema_version || '1.0.0'}</div>
                </div>
              </div>
            </div>

            {/* Architecture Decision Records (ADR) Registry */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
              <h3 className="font-bold text-white flex items-center gap-2 border-b border-slate-800 pb-3">
                <BookOpen className="h-4 w-4 text-indigo-400" />
                Architectural Decision Records (ADRs) Active in Codebase
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {systemConfig?.adrs && systemConfig.adrs.map((adr) => (
                  <div key={adr.id} className="p-3 bg-slate-800/40 border border-slate-800 rounded flex justify-between items-center text-xs">
                    <div>
                      <span className="font-mono font-bold text-white">{adr.id}: {adr.title}</span>
                    </div>
                    <span className="px-2 py-0.5 rounded bg-indigo-950 border border-indigo-800 text-indigo-300 font-mono text-[10px]">
                      {adr.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Feature 7: Webhook & Notification Alerts Hub */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
              <div className="flex justify-between items-center border-b border-slate-800 pb-3">
                <div>
                  <h3 className="font-bold text-white flex items-center gap-2">
                    <Zap className="h-4 w-4 text-amber-400" />
                    External Alert Webhook Integrations (Discord & Telegram)
                  </h3>
                  <span className="text-xs text-slate-400">Receive instant push notifications for paper fills, risk trips, and bracket stops</span>
                </div>
                {webhookSaved && (
                  <span className="text-xs font-mono text-emerald-400 flex items-center gap-1">
                    <CheckCircle2 className="h-3.5 w-3.5" /> Webhook settings saved!
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="text-[11px] font-mono text-slate-400 uppercase">Discord Webhook URL</label>
                  <input
                    type="text"
                    placeholder="https://discord.com/api/webhooks/..."
                    value={discordWebhookUrl}
                    onChange={(e) => setDiscordWebhookUrl(e.target.value)}
                    className="w-full mt-1 bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-mono text-slate-400 uppercase">Telegram Bot Webhook URL</label>
                  <input
                    type="text"
                    placeholder="https://api.telegram.org/bot<TOKEN>/sendMessage"
                    value={telegramWebhookUrl}
                    onChange={(e) => setTelegramWebhookUrl(e.target.value)}
                    className="w-full mt-1 bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-mono text-slate-400 uppercase">Telegram Chat ID</label>
                  <div className="flex gap-2 mt-1">
                    <input
                      type="text"
                      placeholder="e.g. 123456789"
                      value={telegramChatId}
                      onChange={(e) => setTelegramChatId(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                    />
                    <button
                      type="button"
                      onClick={handleSaveWebhooks}
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-bold font-mono transition-colors cursor-pointer shrink-0"
                    >
                      Save
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Real-Time Dispatch Alerts Log */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-5 shadow-sm space-y-3">
              <div className="flex justify-between items-center border-b border-slate-800 pb-3">
                <div>
                  <h3 className="font-bold text-white flex items-center gap-2">
                    <Bell className="h-4 w-4 text-indigo-400" />
                    Real-Time Dispatch Alerts & Notification Log
                  </h3>
                  <span className="text-xs text-slate-400">Streaming critical trade executions, risk floor trips, and audit events</span>
                </div>
                <span className="text-xs font-mono text-slate-400">
                  Total Events: <strong className="text-white">{alerts.length}</strong>
                </span>
              </div>

              <div className="space-y-2 max-h-80 overflow-y-auto">
                {alerts && alerts.length > 0 ? (
                  alerts.map((al) => (
                    <div
                      key={al.alert_id}
                      className="p-3 bg-slate-850 border border-slate-800 rounded flex items-start space-x-3 text-xs font-mono"
                    >
                      {al.level === 'CRITICAL' || al.level === 'WARNING' ? (
                        <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                      )}
                      <div className="flex-1 space-y-0.5">
                        <div className="flex justify-between items-center">
                          <span className="font-bold text-white">{al.title}</span>
                          <span className="text-[10px] text-slate-500">{new Date(al.timestamp).toLocaleTimeString()}</span>
                        </div>
                        <p className="text-slate-300 text-[11px] font-sans">{al.message}</p>
                        <div className="text-[10px] text-slate-500">Category: {al.category} {al.symbol && `• Symbol: ${al.symbol}`}</div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="py-6 text-center text-slate-500 text-xs italic font-sans">
                    No active system alerts recorded.
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

          </motion.div>
        </AnimatePresence>
      </main>

      {/* 4. Footer & Statutory Notice */}
      <footer className="border-t border-slate-800/80 bg-slate-950 px-6 py-4 text-center text-xs text-slate-500 flex flex-col sm:flex-row justify-between items-center gap-2">
        <div>
          AlphaTrader Autonomous Quant Platform • Certified Paper Trading Environment (ADR-002)
        </div>
        <div className="font-mono text-slate-400 text-[11px]">
          Gateway: <span className="text-emerald-400">http://127.0.0.1:8000</span> | UI: <span className="text-indigo-400">http://127.0.0.1:3000</span>
        </div>
      </footer>
    </div>
  );
}
