from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.ai_macro.models import (
    AIMacroPerformance,
    AIMacroScore,
    AIMacroTrade,
    MacroAnalysis,
)

_INITIAL_BALANCE = Decimal("1000")
_TRADE_SIZE = Decimal("200")

_STATE_ICON = {"BULL": "📈", "BEAR": "📉", "RANGE": "📊", "RISK_OFF": "⚠️"}
_STATUS_LABEL = {"TP2": "TP2 ✅✅", "TP1": "TP1 ✅", "STOP": "STOP ❌", "EXPIRED": "Expired ⏰", "OPEN": "Open 📌"}


def calculate_performance(trades: tuple[AIMacroTrade, ...]) -> AIMacroPerformance:
    open_list = [t for t in trades if t.status == "OPEN"]
    closed_list = [t for t in trades if t.status != "OPEN"]

    tp1_count = sum(1 for t in closed_list if t.status == "TP1")
    tp2_count = sum(1 for t in closed_list if t.status == "TP2")
    stop_count = sum(1 for t in closed_list if t.status == "STOP")
    expired_count = sum(1 for t in closed_list if t.status == "EXPIRED")
    win_count = tp1_count + tp2_count

    n_closed = len(closed_list)
    win_rate = (Decimal(win_count * 100) / Decimal(n_closed)).quantize(Decimal("0.1")) if n_closed else Decimal("0.0")
    tp1_rate = (Decimal(tp1_count * 100) / Decimal(n_closed)).quantize(Decimal("0.1")) if n_closed else Decimal("0.0")
    tp2_rate = (Decimal(tp2_count * 100) / Decimal(n_closed)).quantize(Decimal("0.1")) if n_closed else Decimal("0.0")

    closed_pnls = [t.pnl_pct for t in closed_list if t.pnl_pct is not None]
    avg_pnl = (
        (sum(closed_pnls, Decimal("0")) / Decimal(len(closed_pnls))).quantize(Decimal("0.01"))
        if closed_pnls
        else Decimal("0.00")
    )
    trade_pnl = sum(_TRADE_SIZE * p / Decimal("100") for p in closed_pnls)
    virtual_balance = (_INITIAL_BALANCE + trade_pnl).quantize(Decimal("0.01"))

    return AIMacroPerformance(
        total_trades=len(trades),
        open_trades=len(open_list),
        closed_trades=n_closed,
        win_count=win_count,
        tp1_count=tp1_count,
        tp2_count=tp2_count,
        stop_count=stop_count,
        expired_count=expired_count,
        win_rate=win_rate,
        tp1_rate=tp1_rate,
        tp2_rate=tp2_rate,
        avg_pnl_pct=avg_pnl,
        virtual_balance=virtual_balance,
    )


def render_ai_macro_report(
    analysis: MacroAnalysis,
    scores: list[AIMacroScore],
    new_trades: list[AIMacroTrade],
    skipped_count: int,
) -> str:
    icon = _STATE_ICON.get(analysis.market_state, "")
    lines = [
        "# AI Macro Report",
        "",
        f"- **Generated at (UTC):** {analysis.generated_at}",
        f"- **Market State:** {analysis.market_state} {icon}",
        f"- **Risk Grade:** {analysis.risk_grade}",
        f"- **Trade Bias:** {analysis.trade_bias}",
        f"- **BTC 24h:** {float(analysis.btc_change_pct):+.2f}%",
        f"- **ETH 24h:** {float(analysis.eth_change_pct):+.2f}%",
        "",
        "## Candidate Scores",
        "",
        "| Symbol | Direction | Score | Trend | Momentum | Volume | Structure | Risk |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in sorted(scores, key=lambda x: -x.score):
        lines.append(
            f"| `{s.symbol}` | {s.direction} | **{s.score}** | "
            f"{s.trend_score} | {s.momentum_score} | {s.volume_score} | "
            f"{s.structure_score} | {s.risk_score} |"
        )
    lines += ["", "## New Virtual Trades", ""]
    if new_trades:
        for t in new_trades:
            lines += [
                f"### `{t.symbol}` ({t.direction})",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| Score | {t.score}/100 |",
                f"| Entry | {t.entry} |",
                f"| Stop Loss | {t.stop_loss} |",
                f"| TP1 | {t.tp1} |",
                f"| TP2 | {t.tp2} |",
                f"| Market State | {t.market_state} |",
                f"| Risk Grade | {t.risk_grade} |",
                f"| Reason | {t.reason[:100]} |",
                "",
            ]
    else:
        lines.append("_No new virtual trades created._")
    if skipped_count > 0:
        lines.append(f"\n> {skipped_count} candidate(s) scored below threshold or max open trades reached.")
    lines += ["", "> Research only. No live trading is performed.", ""]
    return "\n".join(lines)


def render_ai_macro_performance(performance: AIMacroPerformance) -> str:
    closed = performance.closed_trades
    lines = [
        "# AI Macro Performance",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total Trades | {performance.total_trades} |",
        f"| Open | {performance.open_trades} |",
        f"| Closed | {closed} |",
        f"| Win (TP1+TP2) | {performance.win_count} |",
        f"| TP1 Hits | {performance.tp1_count} |",
        f"| TP2 Hits | {performance.tp2_count} |",
        f"| Stops | {performance.stop_count} |",
        f"| Expired | {performance.expired_count} |",
        f"| Win Rate | {float(performance.win_rate):.1f}% |",
        f"| TP1 Rate | {float(performance.tp1_rate):.1f}% |",
        f"| TP2 Rate | {float(performance.tp2_rate):.1f}% |",
        f"| Avg PnL | {float(performance.avg_pnl_pct):+.2f}% |",
        f"| Virtual Balance | {float(performance.virtual_balance):.2f} U |",
        "",
        "> Research only. No live trading is performed.",
        "",
    ]
    return "\n".join(lines)
