from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from binance_ai_trader.strategy_lab.models import ChampionStanding, StrategySweepResult


def render_sweep_markdown(
    strategy_id: str,
    database: Path,
    results: Sequence[StrategySweepResult],
    total_combinations: int,
    generated_at: datetime | None = None,
) -> str:
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    lines = [
        f"# Strategy Sweep Report: `{strategy_id}`",
        "",
        f"- **Run timestamp:** {timestamp.isoformat(timespec='seconds')}",
        f"- **Database:** `{database}`",
        f"- **Strategy ID:** `{strategy_id}`",
        f"- **Total parameter combinations tested:** {total_combinations}",
        "",
        "> **Research-only warning:** This report is for strategy research only. "
        "It is not live trading advice and must not be used as an instruction to trade.",
        "",
        "## Top 10",
        "",
        "| Rank | Parameters | Trades | Win rate | Profit factor | Expectancy | Max drawdown | Verdict |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | :--- |",
    ]
    for result in results:
        metrics = result.metrics
        parameters = ", ".join(
            f"{name}={_number(value)}" for name, value in result.parameters.items()
        )
        lines.append(
            "| "
            f"{result.rank} | `{parameters}` | {metrics.total_signals} | "
            f"{metrics.tp2_win_rate:.2f}% | {_optional(metrics.profit_factor)} | "
            f"{metrics.expectancy_r:.4f}R | {metrics.max_drawdown_r:.4f}R | "
            f"**{result.verdict}** |"
        )
    lines.extend(["", "## Best Parameter Set", ""])
    if results:
        best = results[0]
        lines.extend(
            f"- **{name}:** {_number(value)}" for name, value in best.parameters.items()
        )
        lines.append(f"- **Verdict:** {best.verdict}")
    else:
        lines.append("No parameter combinations produced a result.")
    lines.append("")
    return "\n".join(lines)


def write_sweep_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_champion_league_markdown(
    database: Path,
    standings: Sequence[ChampionStanding],
    generated_at: datetime | None = None,
) -> str:
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    lines = [
        "# Strategy Champion League",
        "",
        f"- **Run timestamp:** {timestamp.isoformat(timespec='seconds')}",
        f"- **Week:** {timestamp.date().isocalendar().year}-W{timestamp.date().isocalendar().week:02d}",
        f"- **Database:** `{database}`",
        "",
        "> **Research-only warning:** Champion League results are not live trading advice.",
        "",
        "## Champion",
        "",
    ]
    if standings:
        champion = standings[0]
        lines.extend(
            [
                f"- **Strategy ID:** `{champion.strategy_id}`",
                f"- **Profit factor:** {_optional(champion.metrics.profit_factor)}",
                f"- **Expectancy:** {champion.metrics.expectancy_r:.4f}R",
                f"- **Max drawdown:** {champion.metrics.max_drawdown_r:.4f}R",
                f"- **Trade count:** {champion.metrics.total_signals}",
                f"- **Verdict:** {champion.verdict}",
            ]
        )
    else:
        lines.append("No eligible strategies.")
    lines.extend(
        [
            "",
            "## Leaderboard",
            "",
            "| Rank | Strategy | League score | PF | Expectancy | Max drawdown | Trades | Verdict |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | :--- |",
        ]
    )
    for standing in standings:
        metrics = standing.metrics
        lines.append(
            f"| {standing.rank} | `{standing.strategy_id}` | {standing.score:.6f} | "
            f"{_optional(metrics.profit_factor)} | {metrics.expectancy_r:.4f}R | "
            f"{metrics.max_drawdown_r:.4f}R | {metrics.total_signals} | "
            f"**{standing.verdict}** |"
        )
    lines.append("")
    return "\n".join(lines)


def _optional(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _number(value: float) -> str:
    return f"{value:g}"
