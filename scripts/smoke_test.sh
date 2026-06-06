#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m binance_ai_trader --help
PYTHONPATH=src python -m binance_ai_trader scan --help
PYTHONPATH=src python -m binance_ai_trader backtest --help
