from __future__ import annotations

import json
import logging
import os
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .candidate_builder import build_candidates
from .diagnostics import analyze_candidate_fields, top_missing_fields
from .gemini_client import call_gemini
from .models import (
    PoolStatus,
    PoolSummary,
    SkipResult,
    WatchDecision,
    WatchItem,
)
from .prompt_builder import build_prompt
from .repository import LeaderboardWatchRepository
from .scanner import RankedSymbol, fetch_leaderboard
from .telegram_formatter import (
    format_review,
    format_skipped,
    format_status,
    format_summary,
)

logger = logging.getLogger(__name__)

_SILENT_SKIP_REASONS: frozenset[str] = frozenset({"cooldown_active", "existing_open_recommendation"})


def _send_telegram(
    messages: list[str],
    bot_token: str,
    chat_id: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    sent = 0
    for text in messages:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            sent += 1
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
    if sent == len(messages):
        return {"telegram": "SENT", "telegram_chars": sum(len(m) for m in messages)}
    return {"telegram": "FAILED"}


class LeaderboardWatchService:
    def __init__(
        self,
        db_path: str,
        model: str = "gemini-2.5-flash",
        base_url: str = "https://fapi.binance.com",
        gemini_timeout: float = 60.0,
        gemini_retries: int = 2,
    ) -> None:
        self._db_path = db_path
        self._model = model
        self._base_url = base_url
        self._gemini_timeout = gemini_timeout
        self._gemini_retries = gemini_retries
        self._repo = LeaderboardWatchRepository(db_path)

    def close(self) -> None:
        self._repo.close()

    def update(
        self,
        watch_hours: int = 24,
        top_n: int = 10,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        ranked = fetch_leaderboard(top_n=top_n, base_url=self._base_url, timeout=timeout)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        added = 0
        updated = 0
        for r in ranked:
            existing = self._repo.active_items(limit=9999)
            sym_set = {i.symbol for i in existing}
            item = WatchItem(
                watch_id=str(uuid.uuid4()),
                symbol=r.symbol,
                first_seen_at=now_iso,
                last_seen_at=now_iso,
                first_rank_type=r.rank_type,
                latest_rank_type=r.rank_type,
                best_rank_position=r.rank_position,
                latest_rank_position=r.rank_position,
                first_change_24h=r.change_24h,
                latest_change_24h=r.change_24h,
                quote_volume=r.quote_volume,
                appearances_24h=1,
                status="NEW",
            )
            if r.symbol in sym_set:
                updated += 1
            else:
                added += 1
            self._repo.upsert_item(item)

        expired = self._repo.expire_stale(watch_hours)

        return {
            "status": "OK",
            "ranked_symbols": len(ranked),
            "added": added,
            "updated": updated,
            "expired": expired,
        }

    def status(
        self,
        send_telegram: bool = False,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        telegram_timeout: float = 10.0,
    ) -> dict[str, Any]:
        pool = self._repo.pool_status()
        result: dict[str, Any] = {
            "status": "OK",
            "new": pool.new_count,
            "active": pool.active_count,
            "open": pool.open_count,
            "closed": pool.closed_count,
            "expired": pool.expired_count,
            "top_active": [
                {
                    "symbol": i.symbol,
                    "rank_type": i.latest_rank_type,
                    "rank_position": i.best_rank_position,
                    "change_24h": i.latest_change_24h,
                    "appearances": i.appearances_24h,
                    "status": i.status,
                }
                for i in pool.top_active[:20]
            ],
        }
        if send_telegram and telegram_bot_token and telegram_chat_id:
            msgs = format_status(pool)
            result.update(_send_telegram(msgs, telegram_bot_token, telegram_chat_id, telegram_timeout))
        return result

    def gemini_review(
        self,
        max_candidates: int = 8,
        cooldown_hours: float = 4.0,
        min_gemini_move_pct: float = 8.0,
        send_telegram: bool = False,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        telegram_timeout: float = 10.0,
        gemini_mode: str = "aggressive",
    ) -> dict[str, Any]:
        # Count eligible candidates first — included in every SKIPPED response so
        # callers can verify the pool is healthy even when the key is absent.
        preflight_items = self._repo.items_for_gemini(
            max_n=max_candidates,
            closed_cooldown_hours=cooldown_hours,
            min_move_pct=min_gemini_move_pct,
        )
        candidate_count = len(preflight_items)

        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            skip = SkipResult("gemini_api_key_missing")
            result = {**skip.to_dict(), "candidate_count": candidate_count}
            if send_telegram and telegram_bot_token and telegram_chat_id:
                result.update(_send_telegram(
                    format_skipped(skip), telegram_bot_token, telegram_chat_id, telegram_timeout
                ))
            return result

        if self._repo.has_open_review():
            skip = SkipResult("existing_open_recommendation")
            result = {**skip.to_dict(), "candidate_count": candidate_count}
            if send_telegram and telegram_bot_token and telegram_chat_id and skip.reason not in _SILENT_SKIP_REASONS:
                result.update(_send_telegram(
                    format_skipped(skip), telegram_bot_token, telegram_chat_id, telegram_timeout
                ))
            return result

        last = self._repo.last_review_at()
        if last is not None:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if elapsed < cooldown_hours:
                skip = SkipResult("cooldown_active")
                result = {**skip.to_dict(), "candidate_count": candidate_count}
                if send_telegram and telegram_bot_token and telegram_chat_id and skip.reason not in _SILENT_SKIP_REASONS:
                    result.update(_send_telegram(
                        format_skipped(skip), telegram_bot_token, telegram_chat_id, telegram_timeout
                    ))
                return result

        items = preflight_items
        if not items:
            skip = SkipResult("no_candidates")
            result = {**skip.to_dict(), "candidate_count": 0}
            if send_telegram and telegram_bot_token and telegram_chat_id:
                result.update(_send_telegram(
                    format_skipped(skip), telegram_bot_token, telegram_chat_id, telegram_timeout
                ))
            return result

        candidates = build_candidates(items, base_url=self._base_url, timeout=10.0)
        prompt = build_prompt(candidates, mode=gemini_mode)

        # Field-quality diagnostics on the exact JSON sent to Gemini. Stored on
        # the review for historical UNKNOWN-ratio tracking and surfaced in the
        # NO_TRADE Telegram message so the operator can see *why* no trade fired.
        field_stats = analyze_candidate_fields([c.to_dict() for c in candidates])

        decision, _ = call_gemini(
            prompt,
            api_key=api_key,
            model=self._model,
            timeout=self._gemini_timeout,
            max_retries=self._gemini_retries,
        )

        # Record the per-symbol reject-reason count alongside the field stats so
        # the diagnostic can report it accurately (distinct from decision.reasons).
        field_stats["reject_reasons_count"] = len(decision.reject_reasons)

        review_id = str(uuid.uuid4())
        self._repo.save_review(review_id, decision, field_stats=json.dumps(field_stats))
        self._repo.save_candidates(review_id, candidates)

        telegram_status: dict[str, Any] = {}
        if send_telegram and telegram_bot_token and telegram_chat_id:
            msgs = format_review(decision, stats=field_stats)
            telegram_status = _send_telegram(msgs, telegram_bot_token, telegram_chat_id, telegram_timeout)

        return {
            "status": "OK",
            "review_id": review_id,
            "decision": decision.decision,
            "best_symbol": decision.best_symbol,
            "direction": decision.direction,
            "rating": decision.rating,
            "entry": decision.entry,
            "stop_loss": decision.stop_loss,
            "tp1": decision.tp1,
            "tp2": decision.tp2,
            "rr": decision.rr,
            "risk_level": decision.risk_level,
            "should_trade": decision.should_trade,
            "data_quality": decision.data_quality,
            "candidates_analyzed": len(candidates),
            "model": self._model,
            **telegram_status,
        }

    def settle(self, timeout_hours: float = 48.0) -> dict[str, Any]:
        open_reviews = self._repo.open_reviews()
        settled = []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        ).isoformat(timespec="seconds")

        for rev in open_reviews:
            rid = rev["review_id"]
            created = rev["created_at"]
            if created < cutoff:
                self._repo.settle_review(rid, "TIMEOUT")
                settled.append({"review_id": rid, "outcome": "TIMEOUT"})

        return {"status": "OK", "settled": len(settled), "details": settled}

    def summary(
        self,
        send_telegram: bool = False,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        telegram_timeout: float = 10.0,
    ) -> dict[str, Any]:
        s = self._repo.pool_summary()
        result: dict[str, Any] = {
            "status": "OK",
            "total_reviews": s.total_reviews,
            "trade_count": s.trade_count,
            "no_trade_count": s.no_trade_count,
            "open_count": s.open_count,
            "tp1_count": s.tp1_count,
            "tp2_count": s.tp2_count,
            "sl_count": s.sl_count,
            "timeout_count": s.timeout_count,
            "win_rate": s.win_rate,
        }
        if send_telegram and telegram_bot_token and telegram_chat_id:
            msgs = format_summary(s)
            result.update(_send_telegram(msgs, telegram_bot_token, telegram_chat_id, telegram_timeout))
        return result
