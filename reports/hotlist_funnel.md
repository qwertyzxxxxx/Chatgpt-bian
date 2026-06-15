# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-15T16:28:44+00:00
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
| 4 | `move_ge_min_move` | **39** | 484 | 92.5% |
| 5 | `volume_ge_min_quote_volume` | **33** | 6 | 15.4% |
| 6 | `gainers` | **28** | 5 | 15.2% |
| 7 | `losers` | **5** | 28 | 84.8% |
| 8 | `watchlist_active` | **13** | 20 | 60.6% |
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
| `EVAAUSDT` | `stop_too_wide` | stop_pct=25.7% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=15.1% > 5% |
| `UAIUSDT` | `stop_too_wide` | stop_pct=6.4% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=25.1% > 5% |
| `TRADOORUSDT` | `stop_too_wide` | stop_pct=12.6% > 5% |
| `SIRENUSDT` | `stop_too_wide` | stop_pct=11.8% > 5% |
| `MAGMAUSDT` | `stop_too_wide` | stop_pct=6.7% > 5% |
| `GRASSUSDT` | `stop_too_wide` | stop_pct=6.3% > 5% |
| `BROCCOLIF3BUSDT` | `low_volume` | vol=1,287,129 < 5,000,000 |
| `GRIFFAINUSDT` | `low_volume` | vol=2,500,512 < 5,000,000 |

## Final Opportunities

- `CLOUSDT`
- `TSTUSDT`
- `VELVETUSDT`

> Research only. No live trading is performed.
