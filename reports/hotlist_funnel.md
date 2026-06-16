# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-16T13:27:17+00:00
- **Research only:** True

## Parameters

| Parameter | Value |
| --- | --- |
| `min_move_pct` | 15 |
| `min_quote_volume` | 5000000 |
| `min_rr` | 2 |
| `max_stop_pct` | 5 |

## Funnel

| # | Step | Count | Dropped | Drop-off |
| ---: | --- | ---: | ---: | ---: |
| 1 | `universe_total` | **792** | 0 | 0.0% |
| 2 | `usdt_perpetual` | **527** | 265 | 33.5% |
| 3 | `after_exclusions` | **523** | 4 | 0.8% |
| 4 | `move_ge_min_move` | **20** | 503 | 96.2% |
| 5 | `volume_ge_min_quote_volume` | **19** | 1 | 5.0% |
| 6 | `gainers` | **12** | 7 | 36.8% |
| 7 | `losers` | **7** | 12 | 63.2% |
| 8 | `watchlist_active` | **13** | 6 | 31.6% |
| 9 | `review_candidates` | **13** | 0 | 0.0% |
| 10 | `rr_pass` | **13** | 0 | 0.0% |
| 11 | `stop_pass` | **7** | 6 | 46.2% |
| 12 | `final_opportunities` | **3** | 4 | 57.1% |

## Top Rejection Reasons

| Reason | Count |
| --- | ---: |
| `stop_too_wide` | 6 |
| `low_move` | 3 |
| `low_volume` | 1 |

## Top 10 Rejected Symbols

| Symbol | Reason | Detail |
| --- | --- | --- |
| `EVAAUSDT` | `stop_too_wide` | stop_pct=5.4% > 5% |
| `VELVETUSDT` | `stop_too_wide` | stop_pct=16.7% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=17.3% > 5% |
| `COAIUSDT` | `stop_too_wide` | stop_pct=7.6% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=14.0% > 5% |
| `SIRENUSDT` | `stop_too_wide` | stop_pct=18.7% > 5% |
| `QUSDT` | `low_volume` | vol=4,863,965 < 5,000,000 |
| `0GUSDT` | `low_move` | change=-5.9% < 15% |
| `1000000BOBUSDT` | `low_move` | change=-3.8% < 15% |
| `1000000MOGUSDT` | `low_move` | change=-1.2% < 15% |

## Final Opportunities

### `CLOUSDT`

| Field | Value |
| --- | --- |
| Direction | LONG |
| Entry | 0.14776071 |
| Stop Loss | 0.1438 |
| TP1 | 0.15172143 |
| TP2 | 0.15568214 |
| RR | 2.00 |

### `UAIUSDT`

| Field | Value |
| --- | --- |
| Direction | LONG |
| Entry | 0.30335357 |
| Stop Loss | 0.29316786 |
| TP1 | 0.31353929 |
| TP2 | 0.323725 |
| RR | 2.00 |

### `TSTUSDT`

| Field | Value |
| --- | --- |
| Direction | SHORT |
| Entry | 0.01170032 |
| Stop Loss | 0.01212 |
| TP1 | 0.01128065 |
| TP2 | 0.01086097 |
| RR | 2.00 |


> Research only. No live trading is performed.
