# P1-2 Historical Data Collector and Database Bootstrap

## Goal

P1-2 adds a safe, resumable way to create `data/market_data.db` from Binance USD-M public
market-data endpoints so the repository can accumulate enough point-in-time history for real
backtest and walk-forward validation.

## Public endpoint boundary

The collector uses only:

- `/fapi/v1/exchangeInfo`;
- `/fapi/v1/ticker/24hr`;
- `/fapi/v1/klines`;
- `/futures/data/openInterestHist`;
- `/fapi/v1/fundingRate`;
- `/futures/data/globalLongShortAccountRatio`.

Official endpoint references:

- [Kline/Candlestick Data](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [Open Interest Statistics](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics)
- [Funding Rate History](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)
- [Global Long/Short Ratio](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Long-Short-Ratio)

It does not send an API key and does not call account, balance, position, leverage, order, or
execution endpoints.

## Collected symbols

The bootstrap symbol set is the union of:

1. currently eligible contracts produced by the existing `UniverseConfig` filters;
2. valid, currently trading USD-M perpetual symbols in `config/sectors.json`;
3. BTCUSDT and ETHUSDT.

Stablecoin bases, leveraged-token suffixes, denied symbols, non-USDT contracts, inactive
contracts, and non-perpetual contracts remain excluded by existing universe policy.

## Kline history

For every selected symbol, the collector paginates closed:

- 15-minute klines;
- 1-hour klines;
- 4-hour klines.

Each request is capped at 1,500 rows and advances by the exact interval. Open candles are
excluded. SQLite's existing `(symbol, interval, open_time_ms)` key makes reruns idempotent and
allows an interrupted final page to be fetched again safely.

## Historical universe snapshots

After kline ingestion, the collector derives one universe snapshot per complete UTC day:

- trailing 24-hour quote volume is summed from 96 closed 15-minute candles;
- historical 24-hour price change is calculated from the first open and final close;
- the configured minimum quote-volume rule is reapplied;
- BTCUSDT and ETHUSDT remain present when complete daily candles exist;
- current public contract filters and tick/step sizes provide contract metadata.

Each daily snapshot has its own deterministic collection run and immutable analysis snapshot.
Completed days are skipped on resume.

### Limitation: contract survivorship

Binance's current exchange-info endpoint does not reconstruct delisted historical contracts or
historical tick-size changes. Daily membership is therefore reconstructed from historical
volume for contracts that are currently discoverable/configured. This limitation must be
considered when interpreting old walk-forward windows.

## Capital Flow history

The collector stores immutable point-in-time observations for:

- hourly Open Interest history;
- Funding Rate history;
- hourly global Long/Short Ratio history;
- hourly rolling 24-hour quote volume derived from closed 15-minute klines.

Observations reference the immutable ingestion snapshot. Binance's public futures-data Open
Interest and Long/Short Ratio endpoints have limited historical retention; the collector
requests the latest 30-day public window for those metrics when a longer kline bootstrap is
requested. Funding and kline pagination cover the requested range when Binance makes it
available. Missing older observations remain visible through the existing Capital Flow data
quality and fallback contract rather than being synthesized.

## Resume and idempotency

- Klines use existing SQLite upserts.
- Capital observations use immutable `INSERT OR IGNORE` keys by symbol, metric, and timestamp.
- Daily universe runs use deterministic IDs and are skipped once complete.
- A repeated command creates a new immutable ingestion snapshot for newly available Capital
  observations while preserving prior lineage.
- Public pages are intentionally reread for the requested range so a page interrupted before
  commit is repaired without assuming the local series is gap-free.

## CLI

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db
```

Important options:

- `--end-ms`: deterministic historical cutoff for testing or controlled imports;
- `--request-pause`: delay between public requests;
- `--timeout` and `--max-retries`: public HTTP resilience;
- `--config` and `--sectors-config`: existing universe and configured symbol policies.

The JSON result reports run ID, symbol count, requested range, fetched kline rows, Capital Flow
observations, daily universe snapshots, failures, and database path. Isolated series failures
produce exit code 2 and a `PARTIAL` collection run; already committed symbols remain usable and
the command can be rerun.

## Validation

Automated tests prove:

1. historical kline requests carry public `startTime`, `endTime`, and limit parameters;
2. only closed candles are persisted;
3. BTCUSDT/ETHUSDT multi-timeframe history is stored;
4. daily historical universe snapshots are persisted;
5. all four Capital Flow metrics are present;
6. reruns do not duplicate klines, universe rows, or Capital observations;
7. ingestion snapshots are finalized and lineage remains immutable;
8. the existing full unit and integration suite remains compatible.

## Walk-forward readiness

This command creates the required database structure and historical records, but walk-forward
readiness still depends on actual endpoint coverage. Use at least 180 requested days, then
check that the database has:

- at least 1,392 eligible aligned BTC/ETH 15-minute timestamps;
- 720 prior 4-hour bars for complete Space analysis at the first evaluation point;
- 96 later 15-minute bars after the final test point;
- historical universe rows before every evaluated window;
- sufficient Capital observations, with limited-retention gaps explicitly marked.

The collector does not automatically run or approve a strategy.

## Safety confirmation

P1-2 changes data ingestion only. It does not change scoring, Regime, Sector, Capital or Space
formulas, Signal Ranking, LONG/SHORT construction, Entry, Stop Loss, TP1, TP2, RR, evaluation,
paper accounting, or walk-forward selection. It adds no API key, account access, order
execution, live trading, Telegram, or Web functionality.
