import json
import unittest
from unittest.mock import MagicMock, patch

from binance_ai_trader.gemini_committee.gemini_client import (
    _extract_json,
    _parse_decision,
    call_gemini,
)
from binance_ai_trader.gemini_committee.models import CommitteeDecision

_TRADE_PAYLOAD = {
    "decision": "TRADE",
    "best_symbol": "BTCUSDT",
    "direction": "LONG",
    "rating": "A",
    "entry": "50000",
    "stop_loss": "48000",
    "tp1": "52000",
    "tp2": "54000",
    "rr": "2.00",
    "risk_level": "LOW",
    "should_trade": True,
    "reasons": ["strong trend", "good RR"],
    "reject_reasons": [{"symbol": "ETHUSDT", "reason": "weak momentum"}],
    "data_quality": "GOOD",
}

_NO_TRADE_PAYLOAD = {
    "decision": "NO_TRADE",
    "best_symbol": "NONE",
    "direction": "UNKNOWN",
    "rating": "C",
    "entry": "UNKNOWN",
    "stop_loss": "UNKNOWN",
    "tp1": "UNKNOWN",
    "tp2": "UNKNOWN",
    "rr": "UNKNOWN",
    "risk_level": "HIGH",
    "should_trade": False,
    "reasons": ["no suitable setup"],
    "reject_reasons": [],
    "data_quality": "POOR",
}


class ExtractJsonTest(unittest.TestCase):
    def test_plain_json(self):
        text = json.dumps(_TRADE_PAYLOAD)
        result = _extract_json(text)
        self.assertEqual(result["decision"], "TRADE")

    def test_json_with_code_fence(self):
        text = "```json\n" + json.dumps(_TRADE_PAYLOAD) + "\n```"
        result = _extract_json(text)
        self.assertEqual(result["best_symbol"], "BTCUSDT")

    def test_raises_on_no_json(self):
        with self.assertRaises(ValueError):
            _extract_json("no json here at all")


class ParseDecisionTest(unittest.TestCase):
    def test_parses_trade_correctly(self):
        d = _parse_decision(_TRADE_PAYLOAD, "raw")
        self.assertEqual(d.decision, "TRADE")
        self.assertEqual(d.best_symbol, "BTCUSDT")
        self.assertTrue(d.should_trade)
        self.assertEqual(d.risk_level, "LOW")
        self.assertEqual(len(d.reasons), 2)

    def test_parses_no_trade_correctly(self):
        d = _parse_decision(_NO_TRADE_PAYLOAD, "raw")
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertEqual(d.best_symbol, "NONE")
        self.assertFalse(d.should_trade)

    def test_invalid_decision_defaults_to_no_trade(self):
        data = dict(_TRADE_PAYLOAD, decision="INVALID_VALUE")
        d = _parse_decision(data, "raw")
        self.assertEqual(d.decision, "NO_TRADE")

    def test_missing_fields_use_defaults(self):
        d = _parse_decision({}, "raw")
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertEqual(d.best_symbol, "NONE")


class CallGeminiMissingKeyTest(unittest.TestCase):
    def test_raises_when_key_missing(self):
        import os
        env_backup = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(EnvironmentError):
                call_gemini("test prompt", api_key="")
        finally:
            if env_backup:
                os.environ["GEMINI_API_KEY"] = env_backup


class CallGeminiMockedTest(unittest.TestCase):
    def _mock_response(self, payload: dict) -> MagicMock:
        raw_text = json.dumps(payload)
        body = {"candidates": [{"content": {"parts": [{"text": raw_text}]}}]}
        body_bytes = json.dumps(body).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=body_bytes)
        return mock_resp

    def test_returns_trade_decision(self):
        with patch("urllib.request.urlopen", return_value=self._mock_response(_TRADE_PAYLOAD)):
            decision, phash = call_gemini("prompt", api_key="fake-key")
        self.assertEqual(decision.decision, "TRADE")
        self.assertIsInstance(phash, str)
        self.assertEqual(len(phash), 16)

    def test_returns_no_trade_decision(self):
        with patch("urllib.request.urlopen", return_value=self._mock_response(_NO_TRADE_PAYLOAD)):
            decision, phash = call_gemini("prompt", api_key="fake-key")
        self.assertEqual(decision.decision, "NO_TRADE")
        self.assertFalse(decision.should_trade)


if __name__ == "__main__":
    unittest.main()
