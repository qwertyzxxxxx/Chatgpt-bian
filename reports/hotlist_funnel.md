# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-15T16:47:21+00:00
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
| 4 | `move_ge_min_move` | **37** | 486 | 92.9% |
| 5 | `volume_ge_min_quote_volume` | **31** | 6 | 16.2% |
| 6 | `gainers` | **24** | 7 | 22.6% |
| 7 | `losers` | **7** | 24 | 77.4% |
| 8 | `watchlist_active` | **13** | 18 | 58.1% |
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
| `EVAAUSDT` | `stop_too_wide` | stop_pct=27.4% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=16.8% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=15.6% > 5% |
| `TRADOORUSDT` | `stop_too_wide` | stop_pct=12.7% > 5% |
| `CLOUSDT` | `stop_too_wide` | stop_pct=5.1% > 5% |
| `SIRENUSDT` | `stop_too_wide` | stop_pct=5.9% > 5% |
| `MAGMAUSDT` | `stop_too_wide` | stop_pct=6.5% > 5% |
| `GRASSUSDT` | `stop_too_wide` | stop_pct=5.7% > 5% |
| `1000000MOGUSDT` | `low_volume` | vol=1,184,922 < 5,000,000 |
| `CLANKERUSDT` | `low_volume` | vol=1,720,753 < 5,000,000 |

## Final Opportunities

- `UAIUSDT`
- `TSTUSDT`
- `VELVETUSDT`

> Research only. No live trading is performed.
