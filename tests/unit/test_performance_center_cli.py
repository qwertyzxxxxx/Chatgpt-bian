import os
import subprocess
import sys
import unittest


_REPO_DIR = os.path.join(os.path.dirname(__file__), "..", "..")


def _run(*args, env_override=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(_REPO_DIR, "src")
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        [sys.executable, "-m", "binance_ai_trader"] + list(args),
        capture_output=True, text=True, cwd=_REPO_DIR, env=env,
        timeout=30,
    )
    return result


class TestPerformanceCenterCLI(unittest.TestCase):
    def test_help_registered(self):
        r = _run("performance-center", "--help")
        self.assertIn("performance-center", r.stdout + r.stderr)

    def test_settle_help(self):
        r = _run("performance-center", "settle", "--help")
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("settle", out)

    def test_summary_help(self):
        r = _run("performance-center", "summary", "--help")
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("send-telegram", out)

    def test_leaderboard_help(self):
        r = _run("performance-center", "leaderboard", "--help")
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("leaderboard", out)

    def test_performance_center_in_whitelist(self):
        cli_path = os.path.join(_REPO_DIR, "src", "binance_ai_trader", "entrypoints", "cli.py")
        with open(cli_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("performance-center", src)

    def test_summary_runs_without_crash(self):
        r = _run("performance-center", "summary")
        self.assertEqual(r.returncode, 0)

    def test_leaderboard_runs_without_crash(self):
        r = _run("performance-center", "leaderboard")
        self.assertEqual(r.returncode, 0)

    def test_settle_runs_without_crash(self):
        r = _run("performance-center", "settle")
        self.assertEqual(r.returncode, 0)

    def test_strategy_diagnostic_help(self):
        r = _run("performance-center", "strategy-diagnostic", "--help")
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("strategy-diagnostic", out)

    def test_strategy_diagnostic_runs_without_crash(self):
        r = _run("performance-center", "strategy-diagnostic")
        self.assertEqual(r.returncode, 0)

    def test_strategy_diagnostic_json_output(self):
        import json
        r = _run("performance-center", "strategy-diagnostic")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("diagnostic", data)
        self.assertIn("days", data)
        entries = data["diagnostic"]
        self.assertIsInstance(entries, list)
        self.assertGreater(len(entries), 0)
        for entry in entries:
            self.assertIn("strategy_id", entry)
            self.assertIn("bottleneck", entry)
            self.assertIn("bottleneck_description", entry)


if __name__ == "__main__":
    unittest.main()
