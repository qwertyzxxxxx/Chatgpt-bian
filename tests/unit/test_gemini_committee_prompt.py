import unittest

from binance_ai_trader.gemini_committee.models import Candidate
from binance_ai_trader.gemini_committee.prompt_builder import build_prompt


def _cand(symbol: str) -> Candidate:
    return Candidate(
        symbol=symbol, source="hotlist", direction="LONG",
        entry="100", stop_loss="95", tp1="110", tp2="120", rr="2.00"
    )


class PromptBuilderTest(unittest.TestCase):
    def test_prompt_contains_anti_hallucination_rules(self):
        prompt = build_prompt([_cand("BTCUSDT")])
        self.assertIn("禁止编造", prompt)
        self.assertIn("新闻", prompt)
        self.assertIn("基本面", prompt)
        self.assertIn("链上资金", prompt)

    def test_prompt_contains_required_output_schema(self):
        prompt = build_prompt([_cand("BTCUSDT")])
        self.assertIn('"decision"', prompt)
        self.assertIn('"best_symbol"', prompt)
        self.assertIn('"should_trade"', prompt)
        self.assertIn('"reject_reasons"', prompt)
        self.assertIn('"data_quality"', prompt)

    def test_prompt_contains_candidate_symbol(self):
        prompt = build_prompt([_cand("ETHUSDT")])
        self.assertIn("ETHUSDT", prompt)

    def test_no_trade_instruction_present(self):
        prompt = build_prompt([_cand("XRPUSDT")])
        self.assertIn("NO_TRADE", prompt)

    def test_unknown_fields_instruction(self):
        prompt = build_prompt([_cand("SOLUSDT")])
        self.assertIn("UNKNOWN", prompt)

    def test_multiple_candidates_all_appear(self):
        candidates = [_cand("AAAUSDT"), _cand("BBBUSDT"), _cand("CCCUSDT")]
        prompt = build_prompt(candidates)
        for sym in ("AAAUSDT", "BBBUSDT", "CCCUSDT"):
            self.assertIn(sym, prompt)


if __name__ == "__main__":
    unittest.main()
