# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-15T17:25:52+00:00
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
| 4 | `move_ge_min_move` | **32** | 491 | 93.9% |
| 5 | `volume_ge_min_quote_volume` | **29** | 3 | 9.4% |
| 6 | `gainers` | **22** | 7 | 24.1% |
| 7 | `losers` | **7** | 22 | 75.9% |
| 8 | `watchlist_active` | **13** | 16 | 55.2% |
| 9 | `review_candidates` | **13** | 0 | 0.0% |
| 10 | `rr_pass` | **13** | 0 | 0.0% |
| 11 | `stop_pass` | **6** | 7 | 53.8% |
| 12 | `final_opportunities` | **3** | 3 | 50.0% |

## Top Rejection Reasons

| Reason | Count |
| --- | ---: |
| `stop_too_wide` | 7 |
| `low_volume` | 3 |

## Top 10 Rejected Symbols

| Symbol | Reason | Detail |
| --- | --- | --- |
| `EVAAUSDT` | `stop_too_wide` | stop_pct=28.8% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=10.3% > 5% |
| `UAIUSDT` | `stop_too_wide` | stop_pct=6.9% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=15.5% > 5% |
| `CLOUSDT` | `stop_too_wide` | stop_pct=5.5% > 5% |
| `GRASSUSDT` | `stop_too_wide` | stop_pct=5.9% > 5% |
| `TSTUSDT` | `stop_too_wide` | stop_pct=5.0% > 5% |
| `BROCCOLIF3BUSDT` | `low_volume` | vol=1,312,368 < 5,000,000 |
| `PUMPBTCUSDT` | `low_volume` | vol=1,917,860 < 5,000,000 |
| `VELODROMEUSDT` | `low_volume` | vol=2,139,968 < 5,000,000 |

## Final Opportunities

### `MAGMAUSDT`

| Field | Value |
| --- | --- |
| Direction | LONG |
| Entry | 0.38089 |
| Stop Loss | 0.36263 |
| TP1 | 0.39915 |
| TP2 | 0.41741 |
| RR | 2.00 |

### `VELVETUSDT`

| Field | Value |
| --- | --- |
| Direction | SHORT |
| Entry | 0.3276127 |
| Stop Loss | 0.343 |
| TP1 | 0.31222539 |
| TP2 | 0.29683809 |
| RR | 2.00 |

### `COAIUSDT`

| Field | Value |
| --- | --- |
| Direction | SHORT |
| Entry | 0.33522321 |
| Stop Loss | 0.34051607 |
| TP1 | 0.32993036 |
| TP2 | 0.3246375 |
| RR | 2.00 |


> Research only. No live trading is performed.
