# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-15T11:25:50+00:00
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
| 4 | `move_ge_min_move` | **26** | 497 | 95.0% |
| 5 | `volume_ge_min_quote_volume` | **22** | 4 | 15.4% |
| 6 | `gainers` | **18** | 4 | 18.2% |
| 7 | `losers` | **4** | 18 | 81.8% |
| 8 | `watchlist_active` | **13** | 9 | 40.9% |
| 9 | `review_candidates` | **13** | 0 | 0.0% |
| 10 | `rr_pass` | **13** | 0 | 0.0% |
| 11 | `stop_pass` | **5** | 8 | 61.5% |
| 12 | `final_opportunities` | **3** | 2 | 40.0% |

## Top Rejection Reasons

| Reason | Count |
| --- | ---: |
| `stop_too_wide` | 8 |
| `low_volume` | 2 |

## Top 10 Rejected Symbols

| Symbol | Reason | Detail |
| --- | --- | --- |
| `EVAAUSDT` | `stop_too_wide` | stop_pct=20.5% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=24.0% > 5% |
| `UAIUSDT` | `stop_too_wide` | stop_pct=10.0% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=26.8% > 5% |
| `TRADOORUSDT` | `stop_too_wide` | stop_pct=18.8% > 5% |
| `AIOUSDT` | `stop_too_wide` | stop_pct=9.8% > 5% |
| `MAGMAUSDT` | `stop_too_wide` | stop_pct=11.9% > 5% |
| `TSTUSDT` | `stop_too_wide` | stop_pct=9.1% > 5% |
| `AINUSDT` | `low_volume` | vol=4,576,357 < 5,000,000 |
| `METISUSDT` | `low_volume` | vol=4,081,292 < 5,000,000 |

## Final Opportunities

- `CLOUSDT`
- `GRASSUSDT`
- `VELVETUSDT`

> Research only. No live trading is performed.
