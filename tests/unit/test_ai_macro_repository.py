from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.ai_macro.models import AIMacroTrade
from binance_ai_trader.ai_macro.repository import AIMacroRepository


def _trade(
    trade_id: str = "abc",
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    status: str = "OPEN",
    pnl_pct: Decimal | None = None,
    closed_at: str | None = None,
) -> AIMacroTrade:
    return AIMacroTrade(
        trade_id=trade_id,
        created_at="2026-01-01T00:00:00+00:00",
        symbol=symbol,
        direction=direction,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        score=83,
        market_state="BULL",
        risk_grade="A",
        reason="test reason",
        status=status,
        pnl_pct=pnl_pct,
        closed_at=closed_at,
    )


class TestAIMacroRepository(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test.db"
        self._repo = AIMacroRepository(self._db_path)

    def tearDown(self) -> None:
        self._repo.close()
        self._tmpdir.cleanup()

    def test_create_table_on_init(self) -> None:
        self.assertEqual(self._repo.open_count(), 0)

    def test_save_and_retrieve_open_trade(self) -> None:
        self._repo.save_trade(_trade("t1"))
        open_trades = self._repo.open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0].trade_id, "t1")
        self.assertEqual(open_trades[0].status, "OPEN")

    def test_open_count(self) -> None:
        self._repo.save_trade(_trade("t1"))
        self._repo.save_trade(_trade("t2", status="TP1", pnl_pct=Decimal("5"),
                                    closed_at="2026-01-02T00:00:00+00:00"))
        self.assertEqual(self._repo.open_count(), 1)

    def test_save_idempotent(self) -> None:
        trade = _trade("t1")
        self._repo.save_trade(trade)
        self._repo.save_trade(trade)
        self.assertEqual(self._repo.open_count(), 1)

    def test_all_trades_returns_closed_and_open(self) -> None:
        self._repo.save_trade(_trade("t1", status="OPEN"))
        self._repo.save_trade(_trade("t2", status="TP2", pnl_pct=Decimal("10"),
                                    closed_at="2026-01-02T00:00:00+00:00"))
        self.assertEqual(len(self._repo.all_trades()), 2)

    def test_update_trade_changes_status(self) -> None:
        self._repo.save_trade(_trade("t1"))
        self._repo.update_trade("t1", "TP1", Decimal("5.00"), "2026-01-02T00:00:00+00:00")
        self.assertEqual(len(self._repo.open_trades()), 0)
        all_trades = self._repo.all_trades()
        self.assertEqual(all_trades[0].status, "TP1")
        self.assertEqual(all_trades[0].pnl_pct, Decimal("5.00"))
        self.assertEqual(all_trades[0].closed_at, "2026-01-02T00:00:00+00:00")

    def test_multiple_open_trades(self) -> None:
        for i in range(5):
            self._repo.save_trade(_trade(f"t{i}", symbol=f"SYM{i}USDT"))
        self.assertEqual(self._repo.open_count(), 5)

    def test_pnl_roundtrip(self) -> None:
        trade = _trade("t1", status="STOP", pnl_pct=Decimal("-4.75"),
                       closed_at="2026-01-02T00:00:00+00:00")
        self._repo.save_trade(trade)
        all_trades = self._repo.all_trades()
        self.assertEqual(all_trades[0].pnl_pct, Decimal("-4.75"))

    def test_decimal_entry_roundtrip(self) -> None:
        trade = AIMacroTrade(
            trade_id="t1",
            created_at="2026-01-01T00:00:00+00:00",
            symbol="ETHUSDT",
            direction="SHORT",
            entry=Decimal("3012.5"),
            stop_loss=Decimal("3150.0"),
            tp1=Decimal("2875.0"),
            tp2=Decimal("2737.5"),
            score=81,
            market_state="BEAR",
            risk_grade="C",
            reason="test",
            status="OPEN",
            pnl_pct=None,
            closed_at=None,
        )
        self._repo.save_trade(trade)
        retrieved = self._repo.all_trades()[0]
        self.assertEqual(retrieved.entry, Decimal("3012.5"))
        self.assertEqual(retrieved.stop_loss, Decimal("3150.0"))

    def test_null_pnl_roundtrip(self) -> None:
        self._repo.save_trade(_trade("t1"))
        retrieved = self._repo.all_trades()[0]
        self.assertIsNone(retrieved.pnl_pct)
        self.assertIsNone(retrieved.closed_at)

    def test_update_stop_loss_pnl(self) -> None:
        self._repo.save_trade(_trade("t1"))
        self._repo.update_trade("t1", "STOP", Decimal("-4.76"), "2026-01-02T06:00:00+00:00")
        retrieved = self._repo.all_trades()[0]
        self.assertEqual(retrieved.status, "STOP")
        self.assertEqual(retrieved.pnl_pct, Decimal("-4.76"))


if __name__ == "__main__":
    unittest.main()
