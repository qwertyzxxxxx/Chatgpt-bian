"""Unit tests for leaderboard_watch module."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from binance_ai_trader.leaderboard_watch.models import (
    PoolStatus,
    PoolSummary,
    SkipResult,
    WatchCandidateForGemini,
    WatchDecision,
    WatchItem,
)
from binance_ai_trader.leaderboard_watch.repository import LeaderboardWatchRepository
from binance_ai_trader.leaderboard_watch.scanner import RankedSymbol, fetch_leaderboard
from binance_ai_trader.leaderboard_watch.service import LeaderboardWatchService
from binance_ai_trader.leaderboard_watch.prompt_builder import build_prompt
from binance_ai_trader.leaderboard_watch.telegram_formatter import (
    format_review,
    format_skipped,
    format_status,
    format_summary,
)
from binance_ai_trader.leaderboard_watch.gemini_client import _parse_decision, _extract_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_item(symbol: str, rank_type: str = "GAINER", position: int = 1, status: str = "NEW") -> WatchItem:
    return WatchItem(
        watch_id=f"test-id-{symbol}",
        symbol=symbol,
        first_seen_at=_now(),
        last_seen_at=_now(),
        first_rank_type=rank_type,
        latest_rank_type=rank_type,
        best_rank_position=position,
        latest_rank_position=position,
        first_change_24h="15.5",
        latest_change_24h="15.5",
        quote_volume="5000000.0",
        appearances_24h=1,
        status=status,
    )


class TestWatchItemModel(unittest.TestCase):
    def test_watch_item_fields(self):
        item = _make_item("BTCUSDT")
        self.assertEqual(item.symbol, "BTCUSDT")
        self.assertEqual(item.status, "NEW")
        self.assertEqual(item.best_rank_position, 1)

    def test_watch_decision_no_trade(self):
        d = WatchDecision.no_trade("raw")
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertFalse(d.should_trade)
        self.assertEqual(d.best_symbol, "NONE")

    def test_skip_result_to_dict(self):
        s = SkipResult("test_reason")
        d = s.to_dict()
        self.assertEqual(d["status"], "SKIPPED")
        self.assertEqual(d["reason"], "test_reason")

    def test_watch_candidate_for_gemini_to_dict(self):
        c = WatchCandidateForGemini(
            symbol="ETHUSDT",
            latest_rank_type="GAINER",
            latest_rank_position=1,
            best_rank_position=1,
            latest_change_24h="20.0",
            first_change_24h="15.0",
            quote_volume="1000000.0",
            active_duration_minutes=120,
            appearances_24h=3,
            gainer_candidate=True,
            loser_candidate=False,
            volume_candidate=False,
        )
        d = c.to_dict()
        self.assertEqual(d["symbol"], "ETHUSDT")
        self.assertTrue(d["gainer_candidate"])
        self.assertFalse(d["loser_candidate"])
        self.assertEqual(d["m15"], {})


class TestRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.repo = LeaderboardWatchRepository(self._tmp.name)

    def tearDown(self):
        self.repo.close()
        os.unlink(self._tmp.name)

    def test_tables_created(self):
        tables = {
            row[0]
            for row in self.repo._con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("leaderboard_watch_items", tables)
        self.assertIn("leaderboard_watch_reviews", tables)
        self.assertIn("leaderboard_watch_candidates", tables)

    def test_upsert_new_item(self):
        item = _make_item("BTCUSDT")
        self.repo.upsert_item(item)
        actives = self.repo.active_items(limit=10)
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].symbol, "BTCUSDT")
        self.assertEqual(actives[0].status, "NEW")

    def test_upsert_updates_existing(self):
        item = _make_item("BTCUSDT")
        self.repo.upsert_item(item)
        item2 = _make_item("BTCUSDT", rank_type="VOLUME", position=3)
        self.repo.upsert_item(item2)
        actives = self.repo.active_items(limit=10)
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].appearances_24h, 2)
        self.assertEqual(actives[0].status, "ACTIVE")
        self.assertEqual(actives[0].best_rank_position, 1)

    def test_upsert_updates_latest_rank_type(self):
        item = _make_item("SOLUSDT", rank_type="GAINER", position=2)
        self.repo.upsert_item(item)
        item2 = _make_item("SOLUSDT", rank_type="VOLUME", position=5)
        self.repo.upsert_item(item2)
        actives = self.repo.active_items(limit=10)
        self.assertEqual(actives[0].latest_rank_type, "VOLUME")
        self.assertEqual(actives[0].best_rank_position, 2)

    def test_pool_status_empty(self):
        ps = self.repo.pool_status()
        self.assertEqual(ps.new_count, 0)
        self.assertEqual(ps.active_count, 0)
        self.assertEqual(ps.top_active, [])

    def test_pool_status_counts(self):
        self.repo.upsert_item(_make_item("BTCUSDT", status="NEW"))
        self.repo.upsert_item(_make_item("ETHUSDT", status="NEW"))
        ps = self.repo.pool_status()
        self.assertEqual(ps.new_count, 2)
        self.assertEqual(len(ps.top_active), 2)

    def test_save_review_no_trade(self):
        decision = WatchDecision.no_trade("raw")
        self.repo.save_review("rev-001", decision)
        self.assertFalse(self.repo.has_open_review())

    def test_save_review_trade_sets_open(self):
        self.repo.upsert_item(_make_item("BTCUSDT"))
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="MEDIUM",
            should_trade=True,
            reasons=["test"],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="{}",
        )
        self.repo.save_review("rev-002", decision)
        self.assertTrue(self.repo.has_open_review())
        actives = self.repo.active_items(limit=10)
        btc = next((i for i in actives if i.symbol == "BTCUSDT"), None)
        self.assertIsNone(btc)

    def test_settle_review(self):
        self.repo.upsert_item(_make_item("ETHUSDT"))
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="ETHUSDT",
            direction="LONG",
            rating="B",
            entry="3000",
            stop_loss="2900",
            tp1="3100",
            tp2="3200",
            rr="1.5",
            risk_level="HIGH",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="PARTIAL",
            raw_response="{}",
        )
        self.repo.save_review("rev-003", decision)
        self.repo.settle_review("rev-003", "TP1")
        self.assertFalse(self.repo.has_open_review())

    def test_pool_summary_empty(self):
        s = self.repo.pool_summary()
        self.assertEqual(s.total_reviews, 0)
        self.assertEqual(s.win_rate, "N/A")

    def test_pool_summary_with_data(self):
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="",
        )
        self.repo.upsert_item(_make_item("BTCUSDT"))
        self.repo.save_review("rev-004", decision)
        self.repo.settle_review("rev-004", "TP1")
        s = self.repo.pool_summary()
        self.assertEqual(s.tp1_count, 1)
        self.assertEqual(s.win_rate, "100.0%")

    def test_expire_stale(self):
        from datetime import timedelta
        old_time = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat(timespec="seconds")
        item = _make_item("XRPUSDT")
        item.last_seen_at = old_time
        self.repo.upsert_item(item)
        self.repo._con.execute(
            "UPDATE leaderboard_watch_items SET last_seen_at=? WHERE symbol=?",
            (old_time, "XRPUSDT"),
        )
        self.repo._con.commit()
        expired = self.repo.expire_stale(watch_hours=24)
        self.assertGreaterEqual(expired, 1)

    def test_items_for_gemini_excludes_open(self):
        self.repo.upsert_item(_make_item("BTCUSDT"))
        self.repo.upsert_item(_make_item("ETHUSDT"))
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="",
        )
        self.repo.save_review("rev-005", decision)
        candidates = self.repo.items_for_gemini(max_n=20)
        syms = [c.symbol for c in candidates]
        self.assertNotIn("BTCUSDT", syms)
        self.assertIn("ETHUSDT", syms)

    def test_last_review_at_none_when_empty(self):
        self.assertIsNone(self.repo.last_review_at())

    def test_open_reviews_returns_trade_only(self):
        self.repo.upsert_item(_make_item("BTCUSDT"))
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="",
        )
        self.repo.save_review("rev-006", decision)
        self.repo.save_review("rev-007", WatchDecision.no_trade())
        open_revs = self.repo.open_reviews()
        self.assertEqual(len(open_revs), 1)
        self.assertEqual(open_revs[0]["review_id"], "rev-006")


class TestScanner(unittest.TestCase):
    def test_fetch_leaderboard_network_failure(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = fetch_leaderboard(top_n=5)
        self.assertEqual(result, [])

    def test_fetch_leaderboard_parses_tickers(self):
        tickers = [
            {"symbol": "BTCUSDT", "priceChangePercent": "10.0", "quoteVolume": "5000000"},
            {"symbol": "ETHUSDT", "priceChangePercent": "-5.0", "quoteVolume": "3000000"},
            {"symbol": "SOLUSDT", "priceChangePercent": "2.0", "quoteVolume": "8000000"},
            {"symbol": "BNBUSDT", "priceChangePercent": "8.0", "quoteVolume": "4000000"},
            {"symbol": "XRPUSDT", "priceChangePercent": "-1.0", "quoteVolume": "2000000"},
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(tickers).encode()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_leaderboard(top_n=2)

        self.assertGreater(len(result), 0)
        syms = [r.symbol for r in result]
        self.assertIn("BTCUSDT", syms)

    def test_ranked_symbol_fields(self):
        r = RankedSymbol(
            symbol="BTCUSDT",
            rank_type="GAINER",
            rank_position=1,
            change_24h="10.0",
            quote_volume="5000000",
        )
        self.assertEqual(r.rank_type, "GAINER")
        self.assertEqual(r.rank_position, 1)

    def test_filters_non_usdt_symbols(self):
        tickers = [
            {"symbol": "BTCUSDT", "priceChangePercent": "10.0", "quoteVolume": "5000000"},
            {"symbol": "ETHBTC", "priceChangePercent": "5.0", "quoteVolume": "1000000"},
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(tickers).encode()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_leaderboard(top_n=5)

        syms = [r.symbol for r in result]
        self.assertNotIn("ETHBTC", syms)


class TestPromptBuilder(unittest.TestCase):
    def test_build_prompt_contains_candidates(self):
        c = WatchCandidateForGemini(
            symbol="BTCUSDT",
            latest_rank_type="GAINER",
            latest_rank_position=1,
            best_rank_position=1,
            latest_change_24h="15.0",
            first_change_24h="10.0",
            quote_volume="5000000",
            active_duration_minutes=120,
            appearances_24h=3,
            gainer_candidate=True,
            loser_candidate=False,
            volume_candidate=False,
        )
        prompt = build_prompt([c])
        self.assertIn("BTCUSDT", prompt)
        self.assertIn("gainer_candidate", prompt)
        self.assertIn("量化交易分析员", prompt)

    def test_build_prompt_anti_hallucination_rules(self):
        prompt = build_prompt([])
        self.assertIn("禁止编造", prompt)
        self.assertIn("NO_TRADE", prompt)

    def test_build_prompt_output_format(self):
        prompt = build_prompt([])
        self.assertIn('"decision"', prompt)
        self.assertIn('"best_symbol"', prompt)
        self.assertIn('"stop_loss"', prompt)


class TestGeminiClient(unittest.TestCase):
    def test_extract_json_plain(self):
        text = '{"decision": "NO_TRADE", "best_symbol": "NONE"}'
        result = _extract_json(text)
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_extract_json_with_code_block(self):
        text = '```json\n{"decision": "NO_TRADE"}\n```'
        result = _extract_json(text)
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_extract_json_raises_on_invalid(self):
        with self.assertRaises(ValueError):
            _extract_json("no json here")

    def test_parse_decision_no_trade(self):
        data = {
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
            "reasons": ["no opportunity"],
            "reject_reasons": [],
            "data_quality": "PARTIAL",
        }
        decision = _parse_decision(data, "raw")
        self.assertEqual(decision.decision, "NO_TRADE")
        self.assertFalse(decision.should_trade)

    def test_parse_decision_trade(self):
        data = {
            "decision": "TRADE",
            "best_symbol": "BTCUSDT",
            "direction": "LONG",
            "rating": "A",
            "entry": "50000",
            "stop_loss": "49000",
            "tp1": "51000",
            "tp2": "52000",
            "rr": "2.0",
            "risk_level": "MEDIUM",
            "should_trade": True,
            "reasons": ["strong trend"],
            "reject_reasons": [],
            "data_quality": "GOOD",
        }
        decision = _parse_decision(data, "raw")
        self.assertEqual(decision.decision, "TRADE")
        self.assertTrue(decision.should_trade)
        self.assertEqual(decision.best_symbol, "BTCUSDT")

    def test_parse_decision_normalizes_invalid_decision(self):
        data = {"decision": "MAYBE", "should_trade": False}
        decision = _parse_decision(data, "raw")
        self.assertEqual(decision.decision, "NO_TRADE")


class TestTelegramFormatter(unittest.TestCase):
    def test_format_status_empty_pool(self):
        ps = PoolStatus(
            new_count=0, active_count=0, open_count=0,
            closed_count=0, expired_count=0, top_active=[],
        )
        msgs = format_status(ps)
        self.assertGreater(len(msgs), 0)
        self.assertIn("排行榜观察池", msgs[0])

    def test_format_status_with_items(self):
        ps = PoolStatus(
            new_count=3, active_count=5, open_count=1,
            closed_count=2, expired_count=10,
            top_active=[_make_item("BTCUSDT"), _make_item("ETHUSDT")],
        )
        msgs = format_status(ps)
        text = "".join(msgs)
        self.assertIn("BTCUSDT", text)
        self.assertIn("3", text)

    def test_format_review_no_trade(self):
        decision = WatchDecision.no_trade("raw")
        msgs = format_review(decision)
        text = "".join(msgs)
        self.assertIn("NO_TRADE", text)
        self.assertIn("排行榜", text)

    def test_format_review_trade(self):
        decision = WatchDecision(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A+",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=["strong momentum"],
            reject_reasons=[{"symbol": "ETHUSDT", "reason": "weak volume"}],
            data_quality="GOOD",
        )
        msgs = format_review(decision)
        text = "".join(msgs)
        self.assertIn("BTCUSDT", text)
        self.assertIn("LONG", text)
        self.assertIn("50000", text)
        self.assertIn("ETHUSDT", text)

    def test_format_skipped(self):
        msgs = format_skipped(SkipResult("gemini_api_key_missing"))
        text = "".join(msgs)
        self.assertIn("gemini_api_key_missing", text)

    def test_format_summary(self):
        s = PoolSummary(
            total_reviews=10,
            trade_count=5,
            no_trade_count=5,
            open_count=1,
            tp1_count=2,
            tp2_count=1,
            sl_count=1,
            timeout_count=0,
            win_rate="75.0%",
        )
        msgs = format_summary(s)
        text = "".join(msgs)
        self.assertIn("75.0%", text)
        self.assertIn("排行榜", text)

    def test_chunks_long_message(self):
        ps = PoolStatus(
            new_count=0, active_count=100, open_count=0,
            closed_count=0, expired_count=0,
            top_active=[_make_item(f"COIN{i:03d}USDT") for i in range(20)],
        )
        msgs = format_status(ps)
        for msg in msgs:
            self.assertLessEqual(len(msg), 4096)


class TestService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.svc = LeaderboardWatchService(db_path=self._tmp.name)

    def tearDown(self):
        self.svc.close()
        os.unlink(self._tmp.name)

    def test_update_with_mocked_scanner(self):
        ranked = [
            RankedSymbol("BTCUSDT", "GAINER", 1, "10.0", "5000000"),
            RankedSymbol("ETHUSDT", "LOSER", 1, "-5.0", "3000000"),
        ]
        with patch("binance_ai_trader.leaderboard_watch.service.fetch_leaderboard", return_value=ranked):
            result = self.svc.update()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["ranked_symbols"], 2)

    def test_status_empty(self):
        result = self.svc.status()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["new"], 0)

    def test_status_after_update(self):
        ranked = [RankedSymbol("BTCUSDT", "GAINER", 1, "10.0", "5000000")]
        with patch("binance_ai_trader.leaderboard_watch.service.fetch_leaderboard", return_value=ranked):
            self.svc.update()
        result = self.svc.status()
        self.assertEqual(result["new"], 1)

    def test_gemini_review_skips_when_no_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            result = self.svc.gemini_review()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "gemini_api_key_missing")

    def test_gemini_review_skips_no_candidates(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = self.svc.gemini_review()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "no_candidates")

    def test_gemini_review_skips_cooldown(self):
        from binance_ai_trader.leaderboard_watch.models import WatchDecision as WD

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            self.svc._repo.save_review("rev-cooldown", WD.no_trade())
            result = self.svc.gemini_review(cooldown_hours=4.0)
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "cooldown_active")

    def test_gemini_review_skips_when_open_recommendation(self):
        from binance_ai_trader.leaderboard_watch.models import WatchDecision as WD

        self.svc._repo.upsert_item(_make_item("BTCUSDT"))
        trade = WD(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="",
        )
        self.svc._repo.save_review("rev-open", trade)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = self.svc.gemini_review()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "existing_open_recommendation")

    def test_gemini_review_calls_gemini_when_candidates_exist(self):
        from binance_ai_trader.leaderboard_watch.models import WatchDecision as WD

        ranked = [RankedSymbol("SOLUSDT", "GAINER", 1, "20.0", "5000000")]
        with patch("binance_ai_trader.leaderboard_watch.service.fetch_leaderboard", return_value=ranked):
            self.svc.update()

        no_trade = WD.no_trade("mocked")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch(
                "binance_ai_trader.leaderboard_watch.service.build_candidates",
                return_value=[WatchCandidateForGemini(
                    symbol="SOLUSDT",
                    latest_rank_type="GAINER",
                    latest_rank_position=1,
                    best_rank_position=1,
                    latest_change_24h="20.0",
                    first_change_24h="20.0",
                    quote_volume="5000000",
                    active_duration_minutes=60,
                    appearances_24h=1,
                    gainer_candidate=True,
                    loser_candidate=False,
                    volume_candidate=False,
                )],
            ):
                with patch(
                    "binance_ai_trader.leaderboard_watch.service.call_gemini",
                    return_value=(no_trade, "hash123"),
                ):
                    result = self.svc.gemini_review()

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_settle_times_out_old_reviews(self):
        from datetime import timedelta
        from binance_ai_trader.leaderboard_watch.models import WatchDecision as WD

        self.svc._repo.upsert_item(_make_item("BTCUSDT"))
        trade = WD(
            decision="TRADE",
            best_symbol="BTCUSDT",
            direction="LONG",
            rating="A",
            entry="50000",
            stop_loss="49000",
            tp1="51000",
            tp2="52000",
            rr="2.0",
            risk_level="LOW",
            should_trade=True,
            reasons=[],
            reject_reasons=[],
            data_quality="GOOD",
            raw_response="",
        )
        self.svc._repo.save_review("rev-settle", trade)
        old_time = (
            datetime.now(timezone.utc) - timedelta(hours=50)
        ).isoformat(timespec="seconds")
        self.svc._repo._con.execute(
            "UPDATE leaderboard_watch_reviews SET created_at=? WHERE review_id=?",
            (old_time, "rev-settle"),
        )
        self.svc._repo._con.commit()
        result = self.svc.settle(timeout_hours=48.0)
        self.assertEqual(result["settled"], 1)

    def test_summary_empty(self):
        result = self.svc.summary()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["total_reviews"], 0)
        self.assertEqual(result["win_rate"], "N/A")


class TestEngineIntegration(unittest.TestCase):
    def test_default_tasks_includes_leaderboard_when_provided(self):
        from datetime import timedelta
        from binance_ai_trader.runner.engine import RunnerTask, default_tasks

        dummy = lambda: None
        tasks = default_tasks(
            scan=dummy,
            evaluate=dummy,
            paper_simulate=dummy,
            daily_report=dummy,
            auto_research=dummy,
            collect_history=dummy,
            leaderboard_update=dummy,
            leaderboard_gemini=dummy,
        )
        event_types = [t.event_type for t in tasks]
        self.assertIn("leaderboard_update", event_types)
        self.assertIn("leaderboard_gemini", event_types)

        update_task = next(t for t in tasks if t.event_type == "leaderboard_update")
        self.assertEqual(update_task.interval, timedelta(minutes=15))

        gemini_task = next(t for t in tasks if t.event_type == "leaderboard_gemini")
        self.assertEqual(gemini_task.interval, timedelta(hours=4))

    def test_default_tasks_excludes_leaderboard_when_none(self):
        from binance_ai_trader.runner.engine import default_tasks

        dummy = lambda: None
        tasks = default_tasks(
            scan=dummy,
            evaluate=dummy,
            paper_simulate=dummy,
            daily_report=dummy,
            auto_research=dummy,
            collect_history=dummy,
        )
        event_types = [t.event_type for t in tasks]
        self.assertNotIn("leaderboard_update", event_types)
        self.assertNotIn("leaderboard_gemini", event_types)


class TestCLIParser(unittest.TestCase):
    def test_leaderboard_watch_in_known_commands(self):
        from binance_ai_trader.entrypoints.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "update"])
        self.assertEqual(args.command, "leaderboard-watch")
        self.assertEqual(args.lw_command, "update")

    def test_leaderboard_watch_update_defaults(self):
        from binance_ai_trader.entrypoints.cli import build_parser
        from pathlib import Path

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "update"])
        self.assertEqual(args.watch_hours, 24)
        self.assertEqual(args.top_n, 10)
        self.assertEqual(args.database, Path("data/leaderboard_watch.db"))

    def test_leaderboard_watch_gemini_review_defaults(self):
        from binance_ai_trader.entrypoints.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "gemini-review"])
        self.assertEqual(args.max_candidates, 20)
        self.assertEqual(args.cooldown_hours, 4.0)
        self.assertFalse(args.send_telegram)

    def test_leaderboard_watch_status_subcommand(self):
        from binance_ai_trader.entrypoints.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "status"])
        self.assertEqual(args.lw_command, "status")

    def test_leaderboard_watch_settle_defaults(self):
        from binance_ai_trader.entrypoints.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "settle"])
        self.assertEqual(args.timeout_hours, 48.0)

    def test_leaderboard_watch_summary_subcommand(self):
        from binance_ai_trader.entrypoints.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["leaderboard-watch", "summary"])
        self.assertEqual(args.lw_command, "summary")

    def test_run_loop_enable_leaderboard_watch_flag(self):
        from binance_ai_trader.entrypoints.cli import build_parser
        from pathlib import Path

        parser = build_parser()
        args = parser.parse_args(["run-loop", "--enable-leaderboard-watch"])
        self.assertTrue(args.enable_leaderboard_watch)
        self.assertEqual(args.leaderboard_watch_database, Path("data/leaderboard_watch.db"))
        self.assertEqual(args.leaderboard_watch_hours, 24)


if __name__ == "__main__":
    unittest.main()
