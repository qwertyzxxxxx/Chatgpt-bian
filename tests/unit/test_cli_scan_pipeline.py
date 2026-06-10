from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from binance_ai_trader.entrypoints.cli import _scan


class ScanPipelineTest(unittest.TestCase):
    def test_scan_analyzes_sectors_before_signal_generation(self) -> None:
        calls: list[str] = []
        repository = SimpleNamespace(
            close=lambda: calls.append("close"),
            load_snapshot_for_run=lambda run_id: SimpleNamespace(snapshot_id=f"snapshot-{run_id}"),
        )

        class Collector:
            def __init__(self, **_: object) -> None:
                pass

            def collect(self):
                calls.append("collect")
                return SimpleNamespace(run_id="run-1", universe=(), failed_requests=())

        class RegimeAnalyzer:
            def __init__(self, _: object) -> None:
                pass

            def analyze(self, *_: object):
                calls.append("regime")
                return SimpleNamespace(combined_regime="BULL")

        class Scorer:
            def __init__(self, _: object) -> None:
                pass

            def score_run(self, **_: object):
                calls.append("score")
                return SimpleNamespace(ranked_scores=(), skipped_symbols=())

        class SectorAnalyzer:
            def __init__(self, _: object, __: object) -> None:
                pass

            def analyze_latest(self, *_: object, **__: object):
                calls.append("sectors")
                return ()


        class CapitalAnalyzer:
            def __init__(self, _: object, __: object) -> None:
                pass

            def analyze_latest(self, *_: object, **__: object):
                calls.append("capital")
                return ()

        class SpaceAnalyzer:
            def __init__(self, _: object, __: object) -> None:
                pass

            def analyze_latest(self, *_: object, **__: object):
                calls.append("space")
                return ()

        class Generator:
            def __init__(self, _: object, **__: object) -> None:
                pass

            def generate_latest(self, *_: object):
                calls.append("signals")
                return SimpleNamespace(signals=())

        args = Namespace(
            database=Path("unused.db"),
            config=Path("config/universe.json"),
            sectors_config=Path("config/sectors.json"),
            base_url="https://example.invalid",
            timeout=1.0,
            max_retries=0,
            kline_limit=200,
            max_workers=1,
        )
        with (
            patch("binance_ai_trader.entrypoints.cli.MarketDataRepository", return_value=repository),
            patch("binance_ai_trader.entrypoints.cli.BinancePublicClient"),
            patch("binance_ai_trader.entrypoints.cli.MarketDataCollector", Collector),
            patch("binance_ai_trader.entrypoints.cli.MarketRegimeAnalyzer", RegimeAnalyzer),
            patch("binance_ai_trader.entrypoints.cli.MarketScorer", Scorer),
            patch("binance_ai_trader.entrypoints.cli.SectorStrengthAnalyzer", SectorAnalyzer),
            patch("binance_ai_trader.entrypoints.cli.CapitalFlowAnalyzer", CapitalAnalyzer),
            patch("binance_ai_trader.entrypoints.cli.SpaceAnalyzer", SpaceAnalyzer),
            patch("binance_ai_trader.entrypoints.cli.SignalGenerator", Generator),
        ):
            exit_code = _scan(args)

        self.assertEqual(0, exit_code)
        self.assertEqual(["collect", "regime", "score", "sectors", "capital", "space", "signals", "close"], calls)


if __name__ == "__main__":
    unittest.main()
