from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from binance_ai_trader.application.collect_history import HistoricalCollectionResult
from binance_ai_trader.entrypoints.cli import main


class CollectHistoryCliTest(unittest.TestCase):
    def test_outputs_bootstrap_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.db"
            result = HistoricalCollectionResult(
                run_id="history-1",
                symbols=("BTCUSDT", "ETHUSDT"),
                start_ms=1,
                end_ms=2,
                fetched_klines=100,
                capital_observations=20,
                universe_snapshots=2,
                failures=(),
            )
            output = StringIO()
            with (
                patch("binance_ai_trader.entrypoints.cli.BinancePublicClient"),
                patch("binance_ai_trader.entrypoints.cli.HistoricalDataCollector") as collector,
                redirect_stdout(output),
            ):
                collector.return_value.collect.return_value = result
                exit_code = main([
                    "collect-history", "--days", "180",
                    "--database", str(database), "--request-pause", "0",
                ])

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(2, payload["symbol_count"])
        self.assertEqual(100, payload["fetched_klines"])
        self.assertEqual("history-1", payload["run_id"])
        collector.return_value.collect.assert_called_once_with(180, None)


if __name__ == "__main__":
    unittest.main()
