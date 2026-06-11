# Walk-Forward Validation

## Purpose

P1-1 adds evaluation infrastructure for rolling train/validation/test analysis. It does
not alter scoring, signal generation, ranking, entry, stop loss, targets, RR, or any
strategy parameter.

## Leakage controls

1. All candidate strategies receive the same chronologically ordered evaluation points.
2. Each rolling window is divided into disjoint training, validation, and test slices.
3. Purge/embargo points at least equal to the longest evaluation horizon separate the
   partitions, preventing outcome bars from crossing a boundary.
4. Candidate ranking receives only training backtest summaries.
5. After training selection is complete, only the selected strategy is evaluated on the
   validation and test slices.
6. Validation and test metrics never feed candidate selection or parameter mutation.
7. The existing point-in-time `BacktestEngine` remains responsible for closed-candle and
   no-future-data enforcement inside each partition.

## Current repository result

The repository does not version a production historical SQLite database, so this checked-in
report does not claim strategy performance. Run the command below against an audited database
to replace this section with real rolling-window metrics:

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database data/market_data.db \
  --train-points 720 \
  --validation-points 240 \
  --test-points 240 \
  --step-points 240 \
  --embargo-points 96 \
  --report reports/walk_forward_validation.md
```

| Partition | Win rate | Profit factor | Max drawdown (R) | Number of trades |
| --- | ---: | ---: | ---: | ---: |
| Train | Not run | Not run | Not run | Not run |
| Validation | Not run | Not run | Not run | Not run |
| Test | Not run | Not run | Not run | Not run |

Win rate is defined as the percentage of trades that reach at least TP1, matching the
existing `tp1_hit_rate` metric.

## Rolling-window policy

Default point counts are:

- Training: 720 evaluation points
- Validation: 240 evaluation points
- Test: 240 evaluation points
- Roll step: 240 evaluation points
- Purge/embargo after training and validation: 96 evaluation points

The embargo must be at least the largest candidate evaluation horizon, preventing a late
training trade from using outcome candles in validation and a late validation trade from
using outcome candles in test. These counts are configurable evaluation parameters, not strategy rules. The command fails
rather than producing a misleading report when there is insufficient history for one full
window.

## Overfitting risk indicators

Each generated report highlights:

- positive training expectancy that becomes non-positive in validation;
- positive training expectancy that becomes non-positive in test;
- test expectancy below half of training expectancy;
- training profit factor of at least 1.5 that falls below 1.0 in test;
- test drawdown more than 50% above training drawdown;
- partitions with no trades.

These warnings are diagnostics, not automatic approval or rejection rules. Walk-forward
validation reduces selection bias but cannot remove limited sample size, regime change,
data-quality, liquidity, slippage, fee, or model-risk concerns.

## Validation coverage

Automated tests verify chronological disjoint partitions, rolling windows, training-only
candidate selection, out-of-sample degradation warnings, insufficient-history rejection,
and generation of all required metric columns.
