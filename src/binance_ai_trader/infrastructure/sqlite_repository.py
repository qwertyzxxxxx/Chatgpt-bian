from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.domain.models import (
    BacktestResult,
    BacktestSummary,
    EvaluationSummary,
    Kline,
    MarketRegime,
    RankedScore,
    SectorMember,
    SectorSnapshot,
    SignalEvaluation,
    StoredSignal,
    SymbolScore,
    TradeSignal,
    UniverseMember,
)
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import StrategyVersion


class MarketDataRepository:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def start_run(self, run_id: str, started_at: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO collection_runs (id, started_at, status) VALUES (?, ?, 'RUNNING')",
                (run_id, started_at),
            )

    def save_universe(self, run_id: str, members: Iterable[UniverseMember], observed_at: str) -> None:
        rows = [
            (
                run_id,
                member.symbol,
                member.contract.base_asset,
                member.contract.quote_asset,
                member.contract.contract_type,
                member.contract.status,
                str(member.ticker.quote_volume),
                str(member.ticker.price_change_percent),
                str(member.contract.tick_size),
                str(member.contract.step_size),
                observed_at,
            )
            for member in members
        ]
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO universe_snapshots (
                    run_id, symbol, base_asset, quote_asset, contract_type, contract_status,
                    volume_24h, change_24h, tick_size, step_size, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_klines(self, klines: Iterable[Kline]) -> int:
        rows = [
            (
                item.symbol,
                item.interval,
                item.open_time_ms,
                item.close_time_ms,
                str(item.open),
                str(item.high),
                str(item.low),
                str(item.close),
                str(item.volume),
                str(item.quote_volume),
                item.trade_count,
            )
            for item in klines
        ]
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO klines (
                    symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                    volume, quote_volume, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time_ms) DO UPDATE SET
                    close_time_ms=excluded.close_time_ms, open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close, volume=excluded.volume,
                    quote_volume=excluded.quote_volume, trade_count=excluded.trade_count
                """,
                rows,
            )
        return len(rows)

    def load_klines(self, symbol: str, interval: str, limit: int) -> tuple[Kline, ...]:
        rows = self._connection.execute(
            """
            SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                   volume, quote_volume, trade_count
            FROM (
                SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                       volume, quote_volume, trade_count
                FROM klines
                WHERE symbol=? AND interval=?
                ORDER BY open_time_ms DESC
                LIMIT ?
            )
            ORDER BY open_time_ms
            """,
            (symbol, interval, limit),
        ).fetchall()
        return tuple(
            Kline(
                symbol=row[0], interval=row[1], open_time_ms=row[2], close_time_ms=row[3],
                open=Decimal(row[4]), high=Decimal(row[5]), low=Decimal(row[6]), close=Decimal(row[7]),
                volume=Decimal(row[8]), quote_volume=Decimal(row[9]), trade_count=row[10],
            )
            for row in rows
        )

    def load_klines_at(
        self, symbol: str, interval: str, as_of_close_time_ms: int, limit: int
    ) -> tuple[Kline, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            """
            SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                   volume, quote_volume, trade_count
            FROM (
                SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                       volume, quote_volume, trade_count
                FROM klines
                WHERE symbol=? AND interval=? AND close_time_ms<=?
                ORDER BY open_time_ms DESC
                LIMIT ?
            )
            ORDER BY open_time_ms
            """,
            (symbol, interval, as_of_close_time_ms, limit),
        ).fetchall()
        return tuple(_row_to_kline(row) for row in rows)

    def load_klines_after(
        self, symbol: str, interval: str, after_close_time_ms: int, limit: int
    ) -> tuple[Kline, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            """
            SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                   volume, quote_volume, trade_count
            FROM klines
            WHERE symbol=? AND interval=? AND close_time_ms>?
            ORDER BY open_time_ms
            LIMIT ?
            """,
            (symbol, interval, after_close_time_ms, limit),
        ).fetchall()
        return tuple(_row_to_kline(row) for row in rows)

    def load_backtest_evaluation_times(
        self, start_ms: int | None = None, end_ms: int | None = None,
        required_future_bars: int = 96,
    ) -> tuple[int, ...]:
        if required_future_bars < 1:
            raise ValueError("required_future_bars must be positive")
        rows = self._connection.execute(
            """
            SELECT b.close_time_ms
            FROM klines AS b
            WHERE b.symbol='BTCUSDT' AND b.interval='15m'
              AND EXISTS (
                  SELECT 1 FROM klines AS e
                  WHERE e.symbol='ETHUSDT' AND e.interval='15m'
                    AND e.close_time_ms=b.close_time_ms
              )
              AND (SELECT COUNT(*) FROM klines AS f
                   WHERE f.symbol='BTCUSDT' AND f.interval='15m'
                     AND f.close_time_ms>b.close_time_ms) >= ?
              AND (? IS NULL OR b.close_time_ms>=?)
              AND (? IS NULL OR b.close_time_ms<=?)
            ORDER BY b.close_time_ms
            """,
            (required_future_bars, start_ms, start_ms, end_ms, end_ms),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def load_backtest_universe(self, as_of_ms: int) -> dict[str, Decimal]:
        rows = self._connection.execute(
            """
            SELECT u.run_id, u.symbol, u.tick_size, u.observed_at, r.started_at
            FROM universe_snapshots AS u
            JOIN collection_runs AS r ON r.id=u.run_id
            ORDER BY r.started_at DESC, u.run_id DESC, u.symbol
            """
        ).fetchall()
        eligible_runs: dict[str, tuple[int, dict[str, Decimal]]] = {}
        for run_id, symbol, tick_size, observed_at, started_at in rows:
            observed_ms = _iso_to_epoch_ms(observed_at)
            if observed_ms > as_of_ms:
                continue
            started_ms = _iso_to_epoch_ms(started_at)
            if started_ms > as_of_ms:
                continue
            _, symbols = eligible_runs.setdefault(run_id, (started_ms, {}))
            symbols[symbol] = Decimal(tick_size)
        if not eligible_runs:
            return {}
        return max(eligible_runs.values(), key=lambda item: item[0])[1]

    def start_backtest_run(
        self, run_id: str, started_at: str, start_ms: int | None, end_ms: int | None, step_bars: int
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO backtest_runs (
                    id, started_at, status, start_time_ms, end_time_ms, step_bars
                ) VALUES (?, ?, 'RUNNING', ?, ?, ?)
                """,
                (run_id, started_at, start_ms, end_ms, step_bars),
            )

    def save_backtest_results(
        self, run_id: str, results: Iterable[BacktestResult]
    ) -> None:
        rows = [
            (
                run_id, item.evaluation_time_ms, item.symbol, item.combined_regime,
                item.sector, item.sector_rank, item.score, str(item.entry),
                str(item.stop_loss), str(item.tp1), str(item.tp2), str(item.rr_tp1),
                str(item.rr_tp2), item.result, item.bars_to_result, str(item.realized_r),
            )
            for item in results
        ]
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO backtest_results (
                    backtest_run_id, evaluation_time_ms, symbol, combined_regime, sector,
                    sector_rank, score, entry, stop_loss, tp1, tp2, rr_tp1, rr_tp2,
                    result, bars_to_result, realized_r
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def finish_backtest_run(
        self, run_id: str, completed_at: str, status: str,
        summary: BacktestSummary | None, error_summary: str | None = None,
    ) -> None:
        summary_json = json.dumps(_backtest_summary_dict(summary), sort_keys=True) if summary else None
        with self._connection:
            self._connection.execute(
                """
                UPDATE backtest_runs
                SET completed_at=?, status=?, evaluation_points=?, total_signals=?,
                    summary_json=?, error_summary=?
                WHERE id=?
                """,
                (
                    completed_at, status, summary.evaluation_points if summary else 0,
                    summary.metrics.total_signals if summary else 0, summary_json,
                    error_summary, run_id,
                ),
            )

    def register_strategy_version(
        self, config: StrategyConfig, status: str, created_at: str,
        metrics: BacktestMetrics | None = None,
    ) -> None:
        if status not in {"baseline", "candidate", "approved", "rejected"}:
            raise ValueError("invalid strategy status")
        existing = self._connection.execute(
            "SELECT config_json, status FROM strategy_versions WHERE strategy_id=?",
            (config.strategy_id,),
        ).fetchone()
        config_json = json.dumps(config.as_dict(), sort_keys=True, separators=(",", ":"))
        metrics_json = (
            json.dumps(asdict(metrics), sort_keys=True, separators=(",", ":"))
            if metrics is not None else None
        )
        if existing is not None:
            if existing[0] != config_json:
                raise ValueError("registered strategy config is immutable")
            if existing[1] != status:
                raise ValueError("registered strategy status cannot be changed during registration")
            if metrics_json is not None:
                with self._connection:
                    self._connection.execute(
                        "UPDATE strategy_versions SET metrics_json=? WHERE strategy_id=?",
                        (metrics_json, config.strategy_id),
                    )
            return
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO strategy_versions (
                    strategy_id, name, description, config_json, status, created_at, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (config.strategy_id, config.name, config.description, config_json, status, created_at, metrics_json),
            )

    def update_strategy_metrics(self, strategy_id: str, metrics: BacktestMetrics) -> None:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE strategy_versions SET metrics_json=? WHERE strategy_id=?",
                (json.dumps(asdict(metrics), sort_keys=True, separators=(",", ":")), strategy_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"unknown strategy: {strategy_id}")

    def load_strategy_version(self, strategy_id: str) -> StrategyVersion | None:
        row = self._connection.execute(
            """
            SELECT strategy_id, name, description, config_json, status, created_at, metrics_json
            FROM strategy_versions WHERE strategy_id=?
            """,
            (strategy_id,),
        ).fetchone()
        return _row_to_strategy_version(row) if row is not None else None

    def list_strategy_versions(self) -> tuple[StrategyVersion, ...]:
        rows = self._connection.execute(
            """
            SELECT strategy_id, name, description, config_json, status, created_at, metrics_json
            FROM strategy_versions
            ORDER BY CASE status WHEN 'baseline' THEN 0 WHEN 'approved' THEN 1
                       WHEN 'candidate' THEN 2 ELSE 3 END, created_at, strategy_id
            """
        ).fetchall()
        return tuple(_row_to_strategy_version(row) for row in rows)

    def save_scores(self, run_id: str, scores: Iterable[SymbolScore], created_at: str) -> None:
        rows = [
            (
                run_id,
                rank,
                item.symbol,
                item.score,
                json.dumps(item.score_breakdown, sort_keys=True, separators=(",", ":")),
                item.algorithm_version,
                created_at,
            )
            for rank, item in enumerate(scores, start=1)
        ]
        with self._connection:
            self._connection.execute("DELETE FROM scores WHERE run_id=?", (run_id,))
            self._connection.executemany(
                """
                INSERT INTO scores (
                    run_id, rank, symbol, score, score_breakdown_json, algorithm_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_latest_scores(self, limit: int = 20) -> tuple[RankedScore, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            """
            SELECT s.run_id, s.rank, s.symbol, s.score, s.score_breakdown_json,
                   s.algorithm_version, u.tick_size
            FROM scores AS s
            JOIN collection_runs AS r ON r.id = s.run_id
            JOIN universe_snapshots AS u ON u.run_id = s.run_id AND u.symbol = s.symbol
            WHERE s.run_id = (
                SELECT s2.run_id
                FROM scores AS s2
                JOIN collection_runs AS r2 ON r2.id = s2.run_id
                GROUP BY s2.run_id, r2.started_at
                ORDER BY r2.started_at DESC, s2.run_id DESC
                LIMIT 1
            )
            ORDER BY s.rank, s.symbol
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(
            RankedScore(
                run_id=row[0],
                rank=row[1],
                score=SymbolScore(
                    symbol=row[2], score=row[3], score_breakdown=json.loads(row[4]), algorithm_version=row[5]
                ),
                tick_size=Decimal(row[6]),
            )
            for row in rows
        )

    def load_latest_sector_members(self) -> tuple[str | None, tuple[SectorMember, ...]]:
        rows = self._connection.execute(
            """
            SELECT s.run_id, s.symbol, s.score, u.change_24h, u.volume_24h
            FROM scores AS s
            JOIN collection_runs AS r ON r.id = s.run_id
            JOIN universe_snapshots AS u ON u.run_id = s.run_id AND u.symbol = s.symbol
            WHERE s.run_id = (
                SELECT s2.run_id
                FROM scores AS s2
                JOIN collection_runs AS r2 ON r2.id = s2.run_id
                GROUP BY s2.run_id, r2.started_at
                ORDER BY r2.started_at DESC, s2.run_id DESC
                LIMIT 1
            )
            ORDER BY s.rank, s.symbol
            """
        ).fetchall()
        if not rows:
            return None, ()
        return rows[0][0], tuple(
            SectorMember(
                symbol=row[1],
                score=row[2],
                change_24h=Decimal(row[3]),
                quote_volume_24h=Decimal(row[4]),
            )
            for row in rows
        )

    def save_sector_snapshots(
        self,
        run_id: str,
        snapshots: Iterable[SectorSnapshot],
        calculated_at: str,
    ) -> None:
        rows = [
            (
                run_id,
                item.sector,
                item.sector_rank,
                item.member_count,
                str(item.avg_score),
                str(item.median_score),
                str(item.top3_avg_score),
                str(item.positive_24h_ratio),
                str(item.quote_volume_24h),
                calculated_at,
            )
            for item in snapshots
        ]
        with self._connection:
            self._connection.execute("DELETE FROM sector_snapshots WHERE run_id=?", (run_id,))
            self._connection.executemany(
                """
                INSERT INTO sector_snapshots (
                    run_id, sector, sector_rank, member_count, avg_score, median_score,
                    top3_avg_score, positive_24h_ratio, quote_volume_24h, calculated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_sector_ranks(self, run_id: str) -> dict[str, int]:
        rows = self._connection.execute(
            """
            SELECT sector, sector_rank
            FROM sector_snapshots
            WHERE run_id=?
            ORDER BY sector_rank
            """,
            (run_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def load_latest_combined_regime(self) -> str:
        row = self._connection.execute(
            """
            SELECT combined_regime
            FROM market_regimes
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return row[0] if row is not None else "OBSERVE"

    def save_signals(self, run_id: str, signals: Iterable[TradeSignal], generated_at: str) -> None:
        rows = [
            (
                run_id,
                rank,
                item.symbol,
                item.direction,
                item.combined_regime,
                item.sector,
                item.sector_rank,
                item.score,
                str(item.entry),
                str(item.latest_close),
                str(item.stop_loss),
                str(item.stop_loss_pct),
                str(item.tp1),
                str(item.tp2),
                str(item.rr_tp1),
                str(item.rr_tp2),
                item.logic_summary,
                generated_at,
            )
            for rank, item in enumerate(signals, start=1)
        ]
        with self._connection:
            self._connection.execute("DELETE FROM signals WHERE run_id=?", (run_id,))
            self._connection.executemany(
                """
                INSERT INTO signals (
                    run_id, rank, symbol, direction, combined_regime, sector, sector_rank,
                    score, entry, latest_close, stop_loss, stop_loss_pct, tp1, tp2,
                    rr_tp1, rr_tp2, logic_summary, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_signals_for_evaluation(self) -> tuple[StoredSignal, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, symbol, direction, entry, stop_loss, tp1, tp2, generated_at
            FROM signals
            WHERE direction = 'LONG'
            ORDER BY generated_at, run_id, rank
            """
        ).fetchall()
        return tuple(
            StoredSignal(
                run_id=row[0],
                symbol=row[1],
                direction=row[2],
                entry=Decimal(row[3]),
                stop_loss=Decimal(row[4]),
                tp1=Decimal(row[5]),
                tp2=Decimal(row[6]),
                generated_at=row[7],
                generated_at_ms=_iso_to_epoch_ms(row[7]),
            )
            for row in rows
        )

    def load_future_klines(
        self,
        symbol: str,
        interval: str,
        after_close_time_ms: int,
        limit: int,
    ) -> tuple[Kline, ...]:
        rows = self._connection.execute(
            """
            SELECT symbol, interval, open_time_ms, close_time_ms, open, high, low, close,
                   volume, quote_volume, trade_count
            FROM klines
            WHERE symbol=? AND interval=? AND close_time_ms>?
            ORDER BY open_time_ms
            LIMIT ?
            """,
            (symbol, interval, after_close_time_ms, limit),
        ).fetchall()
        return tuple(
            Kline(
                symbol=row[0], interval=row[1], open_time_ms=row[2], close_time_ms=row[3],
                open=Decimal(row[4]), high=Decimal(row[5]), low=Decimal(row[6]), close=Decimal(row[7]),
                volume=Decimal(row[8]), quote_volume=Decimal(row[9]), trade_count=row[10],
            )
            for row in rows
        )

    def save_signal_evaluations(
        self,
        evaluations: Iterable[SignalEvaluation],
        evaluated_at: str,
    ) -> None:
        rows = [
            (
                item.signal_run_id,
                item.symbol,
                str(item.entry),
                str(item.stop_loss),
                str(item.tp1),
                str(item.tp2),
                item.result,
                str(item.max_favorable_pct),
                str(item.max_adverse_pct),
                item.bars_to_result,
                evaluated_at,
            )
            for item in evaluations
        ]
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO signal_evaluations (
                    signal_run_id, symbol, entry, stop_loss, tp1, tp2, result,
                    max_favorable_pct, max_adverse_pct, bars_to_result, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_run_id, symbol) DO UPDATE SET
                    entry=excluded.entry, stop_loss=excluded.stop_loss,
                    tp1=excluded.tp1, tp2=excluded.tp2, result=excluded.result,
                    max_favorable_pct=excluded.max_favorable_pct,
                    max_adverse_pct=excluded.max_adverse_pct,
                    bars_to_result=excluded.bars_to_result, evaluated_at=excluded.evaluated_at
                """,
                rows,
            )

    def load_evaluation_summary(self) -> EvaluationSummary:
        row = self._connection.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(result = 'WIN_TP2'), 0),
                COALESCE(SUM(result = 'TP1_HIT'), 0),
                COALESCE(SUM(result = 'LOSS'), 0),
                COALESCE(SUM(result = 'EXPIRED'), 0),
                COALESCE(AVG(CAST(max_favorable_pct AS REAL)), 0),
                COALESCE(AVG(CAST(max_adverse_pct AS REAL)), 0)
            FROM signal_evaluations
            """
        ).fetchone()
        total = int(row[0])
        win_tp2 = int(row[1])
        tp1_hit = int(row[2])
        loss = int(row[3])
        expired = int(row[4])
        return EvaluationSummary(
            total_signals=total,
            win_tp2_count=win_tp2,
            tp1_hit_count=tp1_hit,
            loss_count=loss,
            expired_count=expired,
            tp1_hit_rate=_rate(tp1_hit + win_tp2, total),
            tp2_win_rate=_rate(win_tp2, total),
            loss_rate=_rate(loss, total),
            average_max_favorable_pct=round(float(row[5]), 2),
            average_max_adverse_pct=round(float(row[6]), 2),
        )

    def save_market_regime(self, regime: MarketRegime, evaluated_at: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO market_regimes (
                    btc_regime, eth_regime, combined_regime, evaluated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    regime.btc_regime,
                    regime.eth_regime,
                    regime.combined_regime,
                    evaluated_at,
                ),
            )

    def finish_run(
        self,
        run_id: str,
        finished_at: str,
        status: str,
        universe_size: int,
        kline_count: int,
        error_summary: str | None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE collection_runs
                SET finished_at=?, status=?, universe_size=?, kline_count=?, error_summary=?
                WHERE id=?
                """,
                (finished_at, status, universe_size, kline_count, error_summary, run_id),
            )

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
                universe_size INTEGER NOT NULL DEFAULT 0,
                kline_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT
            );

            CREATE TABLE IF NOT EXISTS universe_snapshots (
                run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                base_asset TEXT NOT NULL,
                quote_asset TEXT NOT NULL,
                contract_type TEXT NOT NULL,
                contract_status TEXT NOT NULL,
                volume_24h TEXT NOT NULL,
                change_24h TEXT NOT NULL,
                tick_size TEXT NOT NULL,
                step_size TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY (run_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS klines (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL CHECK (interval IN ('15m', '1h', '4h')),
                open_time_ms INTEGER NOT NULL,
                close_time_ms INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                quote_volume TEXT NOT NULL,
                trade_count INTEGER NOT NULL,
                PRIMARY KEY (symbol, interval, open_time_ms)
            );

            CREATE TABLE IF NOT EXISTS scores (
                run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                rank INTEGER NOT NULL CHECK (rank > 0),
                symbol TEXT NOT NULL,
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                score_breakdown_json TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, symbol),
                UNIQUE (run_id, rank)
            );

            CREATE INDEX IF NOT EXISTS idx_scores_run_score ON scores(run_id, score DESC);

            CREATE TABLE IF NOT EXISTS signals (
                run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 3),
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction = 'LONG'),
                combined_regime TEXT NOT NULL DEFAULT 'OBSERVE' CHECK (
                    combined_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')
                ),
                sector TEXT NOT NULL DEFAULT 'OTHER' CHECK (sector IN (
                    'AI_AGENT', 'RWA', 'MEME', 'DEPIN', 'INFRA',
                    'LAYER1', 'LAYER2', 'DEFI', 'GAMEFI', 'OTHER'
                )),
                sector_rank INTEGER CHECK (sector_rank > 0),
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                entry TEXT NOT NULL,
                latest_close TEXT NOT NULL,
                stop_loss TEXT NOT NULL,
                stop_loss_pct TEXT NOT NULL,
                tp1 TEXT NOT NULL,
                tp2 TEXT NOT NULL,
                rr_tp1 TEXT NOT NULL,
                rr_tp2 TEXT NOT NULL,
                logic_summary TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, symbol),
                UNIQUE (run_id, rank)
            );


            CREATE TABLE IF NOT EXISTS signal_evaluations (
                signal_run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry TEXT NOT NULL,
                stop_loss TEXT NOT NULL,
                tp1 TEXT NOT NULL,
                tp2 TEXT NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('LOSS', 'TP1_HIT', 'WIN_TP2', 'EXPIRED')),
                max_favorable_pct TEXT NOT NULL,
                max_adverse_pct TEXT NOT NULL,
                bars_to_result INTEGER NOT NULL CHECK (bars_to_result BETWEEN 1 AND 96),
                evaluated_at TEXT NOT NULL,
                PRIMARY KEY (signal_run_id, symbol),
                FOREIGN KEY (signal_run_id, symbol)
                    REFERENCES signals(run_id, symbol) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS strategy_versions (
                strategy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                config_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('baseline', 'candidate', 'approved', 'rejected')),
                created_at TEXT NOT NULL,
                metrics_json TEXT
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
                start_time_ms INTEGER,
                end_time_ms INTEGER,
                step_bars INTEGER NOT NULL CHECK (step_bars > 0),
                evaluation_points INTEGER NOT NULL DEFAULT 0,
                total_signals INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT,
                error_summary TEXT
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
                backtest_run_id TEXT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
                evaluation_time_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                combined_regime TEXT NOT NULL CHECK (
                    combined_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')
                ),
                sector TEXT NOT NULL CHECK (sector IN (
                    'AI_AGENT', 'RWA', 'MEME', 'DEPIN', 'INFRA',
                    'LAYER1', 'LAYER2', 'DEFI', 'GAMEFI', 'OTHER'
                )),
                sector_rank INTEGER CHECK (sector_rank > 0),
                score REAL NOT NULL CHECK (score BETWEEN 0 AND 100),
                entry TEXT NOT NULL,
                stop_loss TEXT NOT NULL,
                tp1 TEXT NOT NULL,
                tp2 TEXT NOT NULL,
                rr_tp1 TEXT NOT NULL,
                rr_tp2 TEXT NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('LOSS', 'TP1_HIT', 'WIN_TP2', 'EXPIRED')),
                bars_to_result INTEGER NOT NULL CHECK (bars_to_result BETWEEN 1 AND 96),
                realized_r TEXT NOT NULL,
                PRIMARY KEY (backtest_run_id, evaluation_time_ms, symbol)
            );

            CREATE INDEX IF NOT EXISTS idx_backtest_results_run_time
                ON backtest_results(backtest_run_id, evaluation_time_ms);

            CREATE TABLE IF NOT EXISTS market_regimes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                btc_regime TEXT NOT NULL CHECK (btc_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')),
                eth_regime TEXT NOT NULL CHECK (eth_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')),
                combined_regime TEXT NOT NULL CHECK (
                    combined_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')
                ),
                evaluated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sector_snapshots (
                run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                sector TEXT NOT NULL CHECK (sector IN (
                    'AI_AGENT', 'RWA', 'MEME', 'DEPIN', 'INFRA',
                    'LAYER1', 'LAYER2', 'DEFI', 'GAMEFI', 'OTHER'
                )),
                sector_rank INTEGER NOT NULL CHECK (sector_rank > 0),
                member_count INTEGER NOT NULL CHECK (member_count > 0),
                avg_score TEXT NOT NULL,
                median_score TEXT NOT NULL,
                top3_avg_score TEXT NOT NULL,
                positive_24h_ratio TEXT NOT NULL,
                quote_volume_24h TEXT NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, sector),
                UNIQUE (run_id, sector_rank)
            );
            """
        )
        self._ensure_signals_combined_regime()
        self._ensure_signals_sector_context()

    def _ensure_signals_combined_regime(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(signals)")
        }
        if "combined_regime" not in columns:
            with self._connection:
                self._connection.execute(
                    """
                    ALTER TABLE signals ADD COLUMN combined_regime TEXT NOT NULL
                    DEFAULT 'OBSERVE' CHECK (
                        combined_regime IN ('BULL', 'BEAR', 'RANGE', 'OBSERVE')
                    )
                    """
                )

    def _ensure_signals_sector_context(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(signals)")
        }
        with self._connection:
            if "sector" not in columns:
                self._connection.execute(
                    """
                    ALTER TABLE signals ADD COLUMN sector TEXT NOT NULL
                    DEFAULT 'OTHER' CHECK (sector IN (
                        'AI_AGENT', 'RWA', 'MEME', 'DEPIN', 'INFRA',
                        'LAYER1', 'LAYER2', 'DEFI', 'GAMEFI', 'OTHER'
                    ))
                    """
                )
            if "sector_rank" not in columns:
                self._connection.execute(
                    """
                    ALTER TABLE signals ADD COLUMN sector_rank INTEGER
                    CHECK (sector_rank > 0)
                    """
                )


def _iso_to_epoch_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _rate(count: int, total: int) -> float:
    return round(count / total * 100, 2) if total else 0.0


def _row_to_kline(row: tuple[object, ...]) -> Kline:
    return Kline(
        symbol=str(row[0]), interval=str(row[1]), open_time_ms=int(row[2]),
        close_time_ms=int(row[3]), open=Decimal(str(row[4])), high=Decimal(str(row[5])),
        low=Decimal(str(row[6])), close=Decimal(str(row[7])), volume=Decimal(str(row[8])),
        quote_volume=Decimal(str(row[9])), trade_count=int(row[10]),
    )


def _backtest_summary_dict(summary: BacktestSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "completed_at": summary.completed_at,
        "evaluation_points": summary.evaluation_points,
        **asdict(summary.metrics),
        "by_combined_regime": {key: asdict(value) for key, value in summary.by_combined_regime.items()},
        "by_sector": {key: asdict(value) for key, value in summary.by_sector.items()},
        "by_score_bucket": {key: asdict(value) for key, value in summary.by_score_bucket.items()},
    }


def _row_to_strategy_version(row: tuple[object, ...]) -> StrategyVersion:
    metrics_raw = json.loads(str(row[6])) if row[6] is not None else None
    metrics = BacktestMetrics(**metrics_raw) if metrics_raw is not None else None
    return StrategyVersion(
        strategy_id=str(row[0]),
        name=str(row[1]),
        description=str(row[2]),
        config=StrategyConfig.from_dict(json.loads(str(row[3]))),
        status=str(row[4]),
        created_at=str(row[5]),
        metrics=metrics,
    )
