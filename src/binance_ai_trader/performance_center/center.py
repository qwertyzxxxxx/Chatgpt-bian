from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .loader import load_all
from .repository import PerformanceRepository
from .settler import settle_all
from .stats import compute_all_stats, build_leaderboard
from .reporter import generate_summary_md, generate_leaderboard_md
from .telegram_formatter import send_summary, format_summary, format_leaderboard

log = logging.getLogger(__name__)


class PerformanceCenter:
    def __init__(
        self,
        market_db: str = "data/market_data.db",
        ai_macro_db: str = "data/ai_macro.db",
        summary_report_path: str = "reports/performance_summary.md",
        leaderboard_report_path: str = "reports/performance_leaderboard.md",
    ) -> None:
        self._market_db = market_db
        self._ai_macro_db = ai_macro_db
        self._summary_path = summary_report_path
        self._leaderboard_path = leaderboard_report_path
        self._repo = PerformanceRepository(market_db)

    def sync_sources(self) -> int:
        all_results = load_all(self._market_db, self._ai_macro_db)
        new_count = 0
        for sr in all_results:
            if not self._repo.source_id_exists(sr.source_id):
                self._repo.upsert(sr)
                new_count += 1
        log.info("sync_sources: %d new records imported", new_count)
        return new_count

    def settle(self) -> dict:
        self.sync_sources()
        open_results = self._repo.get_open()
        settled = settle_all(open_results)
        changed = [sr for sr in settled if sr.result != "OPEN"]
        for sr in changed:
            self._repo.update_settled(sr)
        result = {
            "open_before": len(open_results),
            "settled": len(changed),
            "still_open": len(open_results) - len(changed),
        }
        log.info("settle: %s", result)
        return result

    def summary(
        self,
        send_telegram: bool = False,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        telegram_timeout: int = 10,
    ) -> dict:
        self.sync_sources()
        all_results = self._repo.get_all()
        stats = compute_all_stats(all_results)
        lb = build_leaderboard(all_results)

        generate_summary_md(stats, self._summary_path)
        generate_leaderboard_md(lb, self._leaderboard_path)

        telegram_status = "SKIPPED"
        if send_telegram:
            token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
            cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
            if token and cid:
                ok = send_summary(stats, lb, token, cid, telegram_timeout, all_results=all_results)
                telegram_status = "SENT" if ok else "FAILED"
            else:
                telegram_status = "NO_CREDENTIALS"

        output = {
            "status": "OK",
            "telegram": telegram_status,
            "strategies": [
                {
                    "strategy": s.strategy,
                    "total": s.total,
                    "tp1": s.tp1,
                    "tp2": s.tp2,
                    "sl": s.sl,
                    "timeout": s.timeout,
                    "open": s.open_count,
                    "win_rate": s.win_rate,
                    "avg_rr": s.avg_rr,
                    "avg_pnl_pct": s.avg_pnl_pct,
                    "max_consecutive_wins": s.max_consecutive_wins,
                    "max_consecutive_losses": s.max_consecutive_losses,
                }
                for s in stats
            ],
            "leaderboard": [
                {"rank": i + 1, "strategy": s.strategy, "win_rate": s.win_rate, "total": s.total}
                for i, s in enumerate(lb.entries)
            ],
        }
        return output

    def leaderboard(self) -> dict:
        self.sync_sources()
        all_results = self._repo.get_all()
        lb = build_leaderboard(all_results)
        generate_leaderboard_md(lb, self._leaderboard_path)
        return {
            "leaderboard": [
                {"rank": i + 1, "strategy": s.strategy, "win_rate": s.win_rate, "total": s.total, "avg_rr": s.avg_rr}
                for i, s in enumerate(lb.entries)
            ]
        }
