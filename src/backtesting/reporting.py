"""Markdown report generation for backtesting results under docs/backtests/."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.backtesting.engine import BacktestResult

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_BACKTESTS_DIR = REPO_ROOT / "docs" / "backtests"


def generate_backtest_report(
    result: BacktestResult,
    strategy_name: str,
    output_filename: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Generate and write a reproducible backtest summary report to docs/backtests/."""
    target_dir = output_dir or DOCS_BACKTESTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        output_filename
        or f"{strategy_name.lower().replace(' ', '_')}_{result.symbol.lower().replace('.', '_')}.md"
    )
    report_path = target_dir / filename

    m = result.metrics
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    cagr_ci = f"[{m.cagr_ci_95.lower_95:.2f}%, {m.cagr_ci_95.upper_95:.2f}%]"
    sharpe_ci = f"[{m.sharpe_ci_95.lower_95:.2f}, {m.sharpe_ci_95.upper_95:.2f}]"
    disclaimer = (
        result.provenance_disclaimer
        or "This backtest uses historical series for existing NIFTY 50 constituents. "
        "Past index compositions differed. Performance cannot be generalized to live index "
        "tracking without survivorship bias correction."
    )

    total_brokerage = sum((t.brokerage_paid for t in result.trades), Decimal("0"))
    total_stt = sum((t.stt_paid for t in result.trades), Decimal("0"))
    total_exchange = sum((t.exchange_fees_paid for t in result.trades), Decimal("0"))
    total_sebi = sum((t.sebi_fees_paid for t in result.trades), Decimal("0"))
    total_stamp = sum((t.stamp_duty_paid for t in result.trades), Decimal("0"))
    total_gst = sum((t.gst_paid for t in result.trades), Decimal("0"))
    total_slippage = sum((t.slippage_paid for t in result.trades), Decimal("0"))

    lines = [
        f"# Backtest Report: {strategy_name} ({result.symbol})",
        "",
        f"- **Generated At:** {now_str}",
        f"- **Target Asset:** {result.symbol}",
        f"- **Starting Capital:** ₹{float(result.initial_capital):,.2f}",
        (
            f"- **Position Sizing Rule:** Fixed-fractional "
            f"({float(result.allocation_pct * 100):.1f}% portfolio equity per trade)"
        ),
        f"- **Final Equity:** ₹{float(result.final_equity):,.2f}",
        f"- **Total Return:** {m.total_return_pct:.2f}%",
        f"- **CAGR:** {m.cagr_pct:.2f}% (95% CI: {cagr_ci})",
        (
            f"- **Sharpe Ratio:** {m.sharpe_ratio:.2f} (95% CI: {sharpe_ci}, "
            f"rf={result.risk_free_rate * 100:.1f}%, N={result.annualization_factor})"
        ),
        f"- **Sortino Ratio:** {m.sortino_ratio:.2f}",
        f"- **Max Drawdown:** {m.max_drawdown_pct:.2f}% ({m.max_drawdown_duration_bars} bars)",
        f"- **Calmar Ratio:** {m.calmar_ratio:.2f}",
        (
            "- **Stationary Bootstrap Parameter:** Politis & Romano (1994), "
            f"expected block length L={result.bootstrap_block_len} bars, 1000 resamples"
        ),
        f"- **Trial Counter:** runs_evaluated_count = {result.runs_evaluated_count}",
        "",
        "## Performance vs. Benchmark Baselines",
        "",
        (
            f"- **Buy-and-Hold Baseline:** Return {result.baseline_buy_and_hold_return_pct:.2f}%, "
            f"CAGR {result.baseline_buy_and_hold_cagr_pct:.2f}%, "
            f"Sharpe {result.baseline_buy_and_hold_sharpe:.2f}"
        ),
        (
            "- **Seeded Random-Entry Baseline (N=200 seeds):** "
            f"Mean Return {result.baseline_random_entry_mean_return_pct:.2f}%, "
            f"Mean Sharpe {result.baseline_random_entry_mean_sharpe:.2f}"
        ),
        "",
        "## Methodology & Disclaimers",
        "",
        "> [!IMPORTANT]",
        "> **Survivorship Bias Disclaimer:**",
        f"> {disclaimer}",
        "",
        "## Cost Model & Itemized Statutory Breakdown",
        "",
        "- **Model:** Indian Equity Cash Segment (NSE Delivery)",
        "- **Brokerage:** Min(0.03%, ₹20/order)",
        "- **STT:** 0.1% on delivery buys and sells",
        "- **Exchange Fee:** 0.00325% turnover",
        "- **SEBI Fee:** ₹10 / crore (0.0001%)",
        "- **Stamp Duty:** 0.015% on buy turnover",
        "- **GST:** 18% on (brokerage + exchange + sebi charges)",
        "- **Slippage Model:** 5 bps half-spread execution drag",
        "",
        "### Statutory Friction Paid Across Simulation",
        f"- **Brokerage:** ₹{float(total_brokerage):,.2f}",
        f"- **STT (Securities Transaction Tax):** ₹{float(total_stt):,.2f}",
        f"- **Exchange Turnover Charges:** ₹{float(total_exchange):,.2f}",
        f"- **SEBI Turnover Charges:** ₹{float(total_sebi):,.2f}",
        f"- **Stamp Duty:** ₹{float(total_stamp):,.2f}",
        f"- **GST (18% on fees):** ₹{float(total_gst):,.2f}",
        f"- **Execution Half-Spread Slippage:** ₹{float(total_slippage):,.2f}",
        f"- **Total Friction Paid:** ₹{m.total_fees_paid:,.2f}",
        "",
        "## Trade Statistics",
        "",
        f"- **Total Completed Trades:** {m.total_trades}",
        f"- **Winning Trades:** {m.winning_trades}",
        f"- **Losing Trades:** {m.losing_trades}",
        f"- **Win Rate:** {m.win_rate_pct:.1f}%",
        f"- **Profit Factor:** {m.profit_factor:.2f}",
        f"- **Gross Profit:** ₹{m.gross_profit:,.2f}",
        f"- **Gross Loss:** ₹{m.gross_loss:,.2f}",
        "",
    ]

    if result.trades:
        lines.extend(
            [
                "## Trade Log (Sample of Completed Roundtrips)",
                "",
                "| Entry | Exit | Side | Qty | Entry Px | Exit Px | Net P&L | Return |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for tr in result.trades[:25]:  # First 25 trades
            e_t = tr.entry_time.strftime("%Y-%m-%d")
            x_t = tr.exit_time.strftime("%Y-%m-%d")
            lines.append(
                f"| {e_t} | {x_t} | {tr.side.value} | {tr.quantity} | "
                f"₹{tr.entry_price} | ₹{tr.exit_price} | ₹{tr.net_pnl} | {tr.return_pct}% |"
            )
        if len(result.trades) > 25:
            more_count = len(result.trades) - 25
            lines.append(f"| ... | ... | ... | ... | ... | ... | ({more_count} more) | ... |")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return report_path
