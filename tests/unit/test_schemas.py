"""
Unit Tests for AlphaTrader Common Data Schemas
Verifies Pydantic v2 validation logic, boundary constraints, and custom validators.
Uses Python's standard unittest framework for zero-dependency execution.
"""

import unittest
from datetime import datetime
from pydantic import ValidationError

from src.common.schemas import (
    SignalAction,
    RiskDecision,
    OrderStatus,
    MarketTick,
    TechnicalIndicators,
    OrderProposal,
    RiskEvaluation,
    ExecutedOrder,
    Position,
    PortfolioState,
)


class TestSchemas(unittest.TestCase):

    def test_market_tick_valid(self):
        tick = MarketTick(
            symbol="NIFTY50_RELIANCE",
            bid_price=2450.50,
            ask_price=2451.00,
            last_price=2450.75,
            volume=125000,
            vwap=2448.20
        )
        self.assertEqual(tick.symbol, "NIFTY50_RELIANCE")
        self.assertGreaterEqual(tick.ask_price, tick.bid_price)
        self.assertEqual(tick.volume, 125000)

    def test_market_tick_invalid_ask_less_than_bid(self):
        with self.assertRaises(ValidationError) as ctx:
            MarketTick(
                symbol="NIFTY50_RELIANCE",
                bid_price=2500.00,
                ask_price=2400.00,  # Invalid: Ask < Bid
                last_price=2450.00,
                volume=100
            )
        self.assertIn("Ask price (2400.0) cannot be less than bid price (2500.0)", str(ctx.exception))

    def test_order_proposal_valid_buy(self):
        proposal = OrderProposal(
            proposal_id="prop-101",
            symbol="NIFTY50_TCS",
            action=SignalAction.BUY,
            quantity=50,
            target_price=3600.00,
            stop_loss_price=3550.00,  # Valid: Stop loss < Target price for BUY
            confidence_score=0.85,
            strategy_name="TrendFollowing_EMA"
        )
        self.assertEqual(proposal.action, SignalAction.BUY)
        self.assertEqual(proposal.confidence_score, 0.85)

    def test_order_proposal_invalid_buy_stop_loss(self):
        with self.assertRaises(ValidationError) as ctx:
            OrderProposal(
                proposal_id="prop-102",
                symbol="NIFTY50_TCS",
                action=SignalAction.BUY,
                quantity=50,
                target_price=3600.00,
                stop_loss_price=3650.00,  # Invalid: Stop loss > Target price for BUY
                confidence_score=0.85,
                strategy_name="TrendFollowing_EMA"
            )
        self.assertIn("Stop loss (3650.0) for BUY order must be below target price (3600.0)", str(ctx.exception))

    def test_order_proposal_invalid_sell_stop_loss(self):
        with self.assertRaises(ValidationError) as ctx:
            OrderProposal(
                proposal_id="prop-103",
                symbol="NIFTY50_INFY",
                action=SignalAction.SELL,
                quantity=100,
                target_price=1400.00,
                stop_loss_price=1350.00,  # Invalid: Stop loss < Target price for SELL
                confidence_score=0.90,
                strategy_name="MeanReversion_RSI"
            )
        self.assertIn("Stop loss (1350.0) for SELL order must be above target price (1400.0)", str(ctx.exception))

    def test_technical_indicators_rsi_bounds(self):
        indicators = TechnicalIndicators(
            symbol="NIFTY50_HDFCBANK",
            ema_20=1600.0,
            ema_50=1580.0,
            ema_200=1520.0,
            rsi_14=65.5,
            macd_line=12.4,
            macd_signal=10.1,
            macd_hist=2.3,
            bband_upper=1650.0,
            bband_lower=1550.0,
            vwap=1595.0
        )
        self.assertEqual(indicators.rsi_14, 65.5)

        with self.assertRaises(ValidationError):
            TechnicalIndicators(
                symbol="NIFTY50_HDFCBANK",
                ema_20=1600.0,
                ema_50=1580.0,
                ema_200=1520.0,
                rsi_14=105.0,  # Invalid: RSI > 100
                macd_line=12.4,
                macd_signal=10.1,
                macd_hist=2.3,
                bband_upper=1650.0,
                bband_lower=1550.0,
                vwap=1595.0
            )

    def test_risk_evaluation_schema(self):
        eval_result = RiskEvaluation(
            proposal_id="prop-101",
            symbol="NIFTY50_TCS",
            decision=RiskDecision.APPROVED,
            drawdown_check_passed=True,
            position_limit_check_passed=True,
            slippage_check_passed=True,
            current_daily_drawdown_pct=0.45
        )
        self.assertEqual(eval_result.decision, RiskDecision.APPROVED)
        self.assertIsNone(eval_result.rejection_reason)

    def test_portfolio_state_schema(self):
        portfolio = PortfolioState(
            cash_balance=500000.0,
            equity_value=250000.0,
            total_portfolio_value=750000.0,
            daily_realized_pnl=1250.0,
            daily_unrealized_pnl=3400.0,
            daily_drawdown_pct=0.25,
            active_positions={
                "NIFTY50_TCS": Position(
                    symbol="NIFTY50_TCS",
                    quantity=50,
                    average_entry_price=3500.0,
                    current_market_price=3600.0,
                    unrealized_pnl=5000.0,
                    realized_pnl=0.0
                )
            }
        )
        self.assertEqual(portfolio.total_portfolio_value, 750000.0)
        self.assertIn("NIFTY50_TCS", portfolio.active_positions)


if __name__ == '__main__':
    unittest.main()
