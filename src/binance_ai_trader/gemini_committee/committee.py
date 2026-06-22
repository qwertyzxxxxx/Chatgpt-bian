from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .candidate_builder import build_candidates
from .gemini_client import call_gemini
from .models import Candidate, CommitteeDecision, SkipResult
from .prompt_builder import build_prompt
from .repository import CommitteeRepository
from .telegram_formatter import format_skipped, format_trade

logger = logging.getLogger(__name__)

_SILENT_SKIP_REASONS: frozenset[str] = frozenset({"cooldown_active", "existing_open_recommendation"})


def _send_telegram(messages: list[str], bot_token: str, chat_id: str, timeout: float = 10.0) -> dict[str, Any]:
    import json
    import urllib.request

    sent = 0
    for text in messages:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
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


class GeminiCommittee:
    def __init__(
        self,
        db_path: str,
        ai_macro_db_path: str,
        model: str = "gemini-2.5-flash",
        max_candidates: int = 4,
        cooldown_hours: float = 4.0,
        base_url: str = "https://fapi.binance.com",
        gemini_timeout: float = 60.0,
        gemini_retries: int = 2,
    ) -> None:
        self._db_path = db_path
        self._ai_macro_db = ai_macro_db_path
        self._model = model
        self._max_candidates = max_candidates
        self._cooldown_hours = cooldown_hours
        self._base_url = base_url
        self._gemini_timeout = gemini_timeout
        self._gemini_retries = gemini_retries
        self._repo = CommitteeRepository(db_path)

    def close(self) -> None:
        self._repo.close()

    def _check_skips(self) -> SkipResult | None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            return SkipResult("gemini_api_key_missing")

        if self._repo.has_open_trade_recommendation():
            return SkipResult("existing_open_recommendation")

        last = self._repo.last_review_at()
        if last is not None:
            elapsed_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if elapsed_hours < self._cooldown_hours:
                return SkipResult("cooldown_active")

        return None

    def _check_candidates(self, candidates: list[Candidate]) -> SkipResult | None:
        if not candidates:
            return SkipResult("no_candidates")
        return None

    def review(
        self,
        send_telegram: bool = False,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        telegram_timeout: float = 10.0,
    ) -> dict[str, Any]:
        skip = self._check_skips()
        if skip:
            result = skip.to_dict()
            if send_telegram and telegram_bot_token and telegram_chat_id and skip.reason not in _SILENT_SKIP_REASONS:
                msgs = format_skipped(skip)
                result.update(_send_telegram(msgs, telegram_bot_token, telegram_chat_id, telegram_timeout))
            return result

        candidates = build_candidates(
            self._db_path,
            self._ai_macro_db,
            self._max_candidates,
            self._base_url,
        )

        skip = self._check_candidates(candidates)
        if skip:
            result = skip.to_dict()
            if send_telegram and telegram_bot_token and telegram_chat_id and skip.reason not in _SILENT_SKIP_REASONS:
                msgs = format_skipped(skip)
                result.update(_send_telegram(msgs, telegram_bot_token, telegram_chat_id, telegram_timeout))
            return result

        api_key = os.environ.get("GEMINI_API_KEY", "")
        prompt = build_prompt(candidates)
        decision, prompt_hash = call_gemini(
            prompt,
            api_key=api_key,
            model=self._model,
            timeout=self._gemini_timeout,
            max_retries=self._gemini_retries,
        )

        review_id = str(uuid.uuid4())
        self._repo.save_review(review_id, decision, prompt_hash, self._model)
        self._repo.save_candidates(review_id, candidates)

        telegram_status: dict[str, Any] = {}
        if send_telegram and telegram_bot_token and telegram_chat_id:
            msgs = format_trade(decision, candidates_analyzed=len(candidates))
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
            "reasons": decision.reasons,
            "reject_reasons": decision.reject_reasons,
            "data_quality": decision.data_quality,
            "candidates_analyzed": len(candidates),
            "model": self._model,
            **telegram_status,
        }
