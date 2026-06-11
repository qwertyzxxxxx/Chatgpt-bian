# Collected Database Readiness Audit

**Audit date:** 2026-06-10 UTC
**Database:** `data/market_data.db`
**Requested range:** 180 days, from 2025-12-12 00:30:25.820 UTC through 2026-06-10 00:30:25.820 UTC

## Final verdict

# NOT_READY_FOR_WALK_FORWARD

The SQLite database and schema were created, but this execution environment's HTTP proxy
rejected the first Binance public request. The collector therefore stored no market data and
there are zero eligible walk-forward timestamps.

## Phase 1 — Collection execution

The requested command was executed with standard ASCII option prefixes:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db
```

The command reached the public-only client and attempted:

```text
GET https://fapi.binance.com/fapi/v1/exchangeInfo
```

It failed before symbol discovery with:

```text
BinancePublicApiError: Binance request failed for /fapi/v1/exchangeInfo:
<urlopen error Tunnel connection failed: 403 Forbidden>
```

This was an environment/proxy rejection, not an authenticated Binance API error. The process
has `HTTP_PROXY` and `HTTPS_PROXY` configured, and the proxy refused the HTTPS CONNECT tunnel.
No API key, signed request, account endpoint, position endpoint, balance endpoint, or order
endpoint was used.

The failed attempt was persisted in `collection_runs`:

| Field | Value |
| --- | --- |
| Run ID | `history-ingest-1765499425820-1781051425820` |
| Status | `FAILED` |
| Data quality | `MISSING` |
| Universe size | 0 |
| Kline count | 0 |
| Error | Public `exchangeInfo` request blocked by proxy tunnel HTTP 403 |

Because failure occurred during exchange metadata discovery, no per-symbol requests were
started. There are no partially collected symbols and no committed market rows to resume from;
a rerun in an environment that can reach Binance will safely populate the existing database.

## Phase 2 — Database audit

The database file exists and is 241,664 bytes, consisting of the migrated schema, indexes,
triggers, immutable analysis snapshot for the failed attempt, and its failed collection-run
audit record.

| Requested measurement | Observed value |
| --- | ---: |
| Total symbols collected | 0 |
| Earliest kline timestamp | Not available |
| Latest kline timestamp | Not available |
| Total 15m rows | 0 |
| Total 1h rows | 0 |
| Total 4h rows | 0 |
| Universe snapshot rows | 0 |
| Universe snapshot runs | 0 |
| Capital observation rows | 0 |
| Signal rows | 0 |
| Evaluation rows | 0 |
| Collection runs | 1 failed run |

### Capital observation coverage

| Metric | Rows |
| --- | ---: |
| Open Interest | 0 |
| Funding Rate | 0 |
| Long/Short Ratio | 0 |
| Rolling 24h quote volume | 0 |

### Missing symbols

All mandatory/configured symbols are missing because symbol collection never began:

- `AAVEUSDT`
- `ADAUSDT`
- `ARBUSDT`
- `AVAXUSDT`
- `AXSUSDT`
- `BNBUSDT`
- `BTCUSDT`
- `CRVUSDT`
- `DOGEUSDT`
- `ETHUSDT`
- `FETUSDT`
- `FILUSDT`
- `GALAUSDT`
- `HNTUSDT`
- `IOTAUSDT`
- `LINKUSDT`
- `ONDOUSDT`
- `OPUSDT`
- `PENDLEUSDT`
- `PEPEUSDT`
- `POLYXUSDT`
- `PYTHUSDT`
- `RENDERUSDT`
- `SANDUSDT`
- `SHIBUSDT`
- `SOLUSDT`
- `STRKUSDT`
- `TAOUSDT`
- `UNIUSDT`
- `WUSDT`

The dynamic universe beyond these 30 configured/mandatory symbols could not be determined
because `exchangeInfo` and the 24-hour ticker endpoint were unreachable.

### Partial symbols

None. The run failed before per-symbol collection, so a symbol cannot be classified as
partially collected. The only partial/failure condition is the run-level public endpoint
failure.

### Data quality summary

| Quality status | Collection runs | Interpretation |
| --- | ---: | --- |
| `COMPLETE` | 0 | No complete collection |
| `PARTIAL` | 0 | No symbol-level collection started |
| `STALE` | 0 | No observations exist to age |
| `FALLBACK` | 0 | No derived result was produced |
| `MISSING` | 1 | The failed bootstrap run has no usable source data |

## Phase 3 — Walk-forward readiness

### Eligibility

The repository query found:

| Readiness measurement | Value |
| --- | ---: |
| Eligible aligned BTC/ETH timestamps | 0 |
| Minimum timestamps for one default fold | 1,392 |
| Possible folds/windows | 0 |
| Remaining eligible timestamp gap | 1,392 |

The default fold requires 720 training points, a 96-point embargo, 240 validation points,
another 96-point embargo, and 240 test points. Each eligible point also needs aligned
BTCUSDT/ETHUSDT closed 15m candles and 96 later BTCUSDT 15m candles for outcome evaluation.
None of these prerequisites are present.

### Remaining gaps

1. Public network access to `https://fapi.binance.com` must be available from the execution
   environment; the current proxy blocks it with tunnel HTTP 403.
2. BTCUSDT and ETHUSDT need aligned closed 15m, 1h, and 4h histories.
3. The configured and dynamically discovered universe needs closed 15m, 1h, and 4h histories.
4. Daily historical universe snapshots need to be generated from complete 15m days.
5. Open Interest, Funding Rate, Long/Short Ratio, and rolling 24h quote-volume observations
   need to be persisted.
6. At least 720 prior 4h candles are needed at the first evaluation point for complete Space
   analysis.
7. At least 1,392 eligible timestamps plus 96 later 15m candles are needed for the first fold.
8. Binance's limited historical retention for Open Interest and global Long/Short Ratio will
   remain a documented Capital Flow coverage limitation even after network access is restored.

### Recommended next action

Run the same idempotent command from Replit Reserved VM or another environment where Binance
USD-M public endpoints are reachable:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db
```

After it completes, rerun the database audit and only then run:

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database data/market_data.db \
  --report reports/walk_forward_validation.md
```

Do not interpret the current zero counts as strategy performance. They indicate that source
data could not be downloaded.

## Safety confirmation

No strategy logic, scoring, signal generation, ranking, Entry, Stop Loss, TP1, TP2, RR,
Telegram behavior, exchange-account integration, or live-trading capability was modified.
Only the ignored SQLite database was created at runtime and this readiness report was added to
version control.
