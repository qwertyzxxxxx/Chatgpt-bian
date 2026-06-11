from contextlib import redirect_stdout
from io import StringIO
import json
import os
import unittest
from unittest.mock import patch

from binance_ai_trader.entrypoints.cli import main


class TelegramCliIntegrationTest(unittest.TestCase):
    def test_telegram_test_without_environment_returns_skipped(self) -> None:
        output = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            exit_code = main(["telegram-test"])
        self.assertEqual(0, exit_code)
        self.assertEqual("SKIPPED", json.loads(output.getvalue())["status"])

    def test_runner_alias_is_registered(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            main(["runner", "--help"])
        self.assertEqual(0, raised.exception.code)
        self.assertIn("--history-days", output.getvalue())


if __name__ == "__main__":
    unittest.main()
