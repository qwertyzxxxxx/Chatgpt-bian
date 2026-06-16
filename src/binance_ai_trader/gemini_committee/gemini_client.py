from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .models import CommitteeDecision

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in Gemini response")
    return json.loads(text[start:end])


def _parse_decision(data: dict[str, Any], raw: str) -> CommitteeDecision:
    def s(key: str, default: str = "UNKNOWN") -> str:
        v = data.get(key, default)
        return str(v) if v is not None else default

    def lst(key: str) -> list:
        v = data.get(key, [])
        return v if isinstance(v, list) else []

    decision = s("decision", "NO_TRADE").upper()
    if decision not in ("TRADE", "NO_TRADE"):
        decision = "NO_TRADE"

    return CommitteeDecision(
        decision=decision,
        best_symbol=s("best_symbol", "NONE"),
        direction=s("direction", "UNKNOWN").upper(),
        rating=s("rating", "C"),
        entry=s("entry"),
        stop_loss=s("stop_loss"),
        tp1=s("tp1"),
        tp2=s("tp2"),
        rr=s("rr"),
        risk_level=s("risk_level", "HIGH").upper(),
        should_trade=bool(data.get("should_trade", False)),
        reasons=lst("reasons"),
        reject_reasons=lst("reject_reasons"),
        data_quality=s("data_quality", "PARTIAL"),
        raw_response=raw,
    )


def call_gemini(
    prompt: str,
    *,
    api_key: str | None = None,
    model: str = _DEFAULT_MODEL,
    timeout: float = 60.0,
    max_retries: int = 2,
) -> tuple[CommitteeDecision, str]:
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise EnvironmentError("GEMINI_API_KEY not set")

    url = f"{_GEMINI_BASE}/{model}:generateContent?key={key}"
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 2048,
        },
    }).encode()

    raw = ""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
            data = _extract_json(raw)
            decision = _parse_decision(data, raw)
            prompt_hash = _prompt_hash(prompt)
            return decision, prompt_hash
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            logger.warning("Gemini HTTP attempt %d failed: %s", attempt + 1, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Gemini parse attempt %d failed: %s", attempt + 1, exc)
            break

    logger.error("Gemini call failed after %d attempts; returning NO_TRADE", max_retries + 1)
    return CommitteeDecision.no_trade(raw), _prompt_hash(prompt)
