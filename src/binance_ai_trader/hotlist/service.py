from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.hotlist.models import HotlistCandidate, HotlistEntryPlan

log = logging.getLogger(__name__)


class PublicMarketData(Protocol):
    def exchange_info(self) -> tuple[Contract, ...]: ...
    def tickers_24h(self) -> tuple[Ticker24h, ...]: ...
    def klines(self, symbol: str, interval: str, limit: int = 200) -> tuple[Kline, ...]: ...


@dataclass(frozen=True, slots=True)
class HotlistWatcherPolicy:
    limit: int = 5
    min_move_pct: Decimal = Decimal("15")
    min_quote_volume: Decimal = Decimal("5000000")
    expiry_minutes: int = 60

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        if self.min_move_pct < 0 or self.min_quote_volume < 0:
            raise ValueError("hotlist thresholds cannot be negative")
        if self.expiry_minutes < 1:
            raise ValueError("expiry_minutes must be positive")


class HotlistWatcher:
    """Public-data-only, research-only momentum watcher."""

    def __init__(
        self,
        client: PublicMarketData,
        universe_config: UniverseConfig,
        policy: HotlistWatcherPolicy = HotlistWatcherPolicy(),
    ) -> None:
        self._client = client
        self._universe_config = universe_config
        self._policy = policy

    def candidates(self) -> tuple[HotlistCandidate, ...]:
        contracts = {
            item.symbol: item
            for item in self._client.exchange_info()
            if item.quote_asset == "USDT"
            and item.margin_asset == "USDT"
            and item.contract_type == "PERPETUAL"
            and item.status == "TRADING"
            and item.base_asset not in self._universe_config.stablecoin_base_assets
            and item.symbol not in self._universe_config.denied_symbols
            and not item.base_asset.endswith(self._universe_config.leveraged_token_suffixes)
        }
        all_tickers = [t for t in self._client.tickers_24h() if t.symbol in contracts]
        eligible = [
            ticker
            for ticker in all_tickers
            if abs(ticker.price_change_percent) >= self._policy.min_move_pct
            and ticker.quote_volume >= self._policy.min_quote_volume
        ]
        top_all = sorted(all_tickers, key=lambda t: -abs(t.price_change_percent))[:15]
        log.info(
            "[V3/Hotlist] universe=%d fetched=%d eligible=%d(move>=%.0f%%+vol>=%.0fM) top15=%s",
            len(contracts),
            len(all_tickers),
            len(eligible),
            self._policy.min_move_pct,
            self._policy.min_quote_volume / 1_000_000,
            [(t.symbol, f"{float(t.price_change_percent):+.1f}%", f"{float(t.quote_volume)/1e6:.1f}M") for t in top_all],
        )
        gainers = sorted(
            (item for item in eligible if item.price_change_percent > 0),
            key=lambda item: (-item.price_change_percent, -item.quote_volume, item.symbol),
        )
        losers = sorted(
            (item for item in eligible if item.price_change_percent < 0),
            key=lambda item: (item.price_change_percent, -item.quote_volume, item.symbol),
        )
        volume_movers = sorted(
            eligible, key=lambda item: (-item.quote_volume, -abs(item.price_change_percent), item.symbol)
        )
        combined = {item.symbol: item for item in (*gainers, *losers, *volume_movers)}
        ranked = sorted(
            combined.values(),
            key=lambda item: (-abs(item.price_change_percent), -item.quote_volume, item.symbol),
        )
        return tuple(
            HotlistCandidate(
                symbol=item.symbol,
                direction="LONG" if item.price_change_percent > 0 else "SHORT",
                change_24h_pct=item.price_change_percent,
                quote_volume=item.quote_volume,
            )
            for item in ranked
        )

    def watch(self, now: datetime | None = None) -> tuple[HotlistEntryPlan, ...]:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        plans = []
        for candidate in self.candidates():
            fifteen = self._client.klines(candidate.symbol, "15m", limit=60)
            hourly = self._client.klines(candidate.symbol, "1h", limit=60)
            if len(fifteen) < 21 or len(hourly) < 20:
                continue
            plans.append(self._plan(candidate, fifteen, hourly, generated_at))
            if len(plans) >= self._policy.limit:
                break
        return tuple(plans)

    def candidates_pool(self, limit: int = 15) -> tuple[HotlistCandidate, ...]:
        """Top-N eligible candidates ranked by |24h move| — a ranking POOL only.

        Callers that need quality-based selection (e.g. V3 strategy) should
        compute full plans for the whole pool via `plan_all()` and re-rank by
        their own criteria, instead of truncating on |24h move| alone.
        """
        return self.candidates()[:limit]

    def plan_all(
        self, candidates: tuple[HotlistCandidate, ...], now: datetime | None = None
    ) -> tuple[HotlistEntryPlan, ...]:
        """Compute entry/SL/TP plans for ALL given candidates — no early truncation."""
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        plans = []
        for candidate in candidates:
            fifteen = self._client.klines(candidate.symbol, "15m", limit=60)
            hourly = self._client.klines(candidate.symbol, "1h", limit=60)
            if len(fifteen) < 21 or len(hourly) < 20:
                continue
            plans.append(self._plan(candidate, fifteen, hourly, generated_at))
        return tuple(plans)

    def plan_candidate(
        self,
        candidate: HotlistCandidate,
        now: datetime | None = None,
        fetch_4h: bool = False,
    ) -> HotlistEntryPlan | None:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        fifteen = self._client.klines(candidate.symbol, "15m", limit=60)
        hourly = self._client.klines(candidate.symbol, "1h", limit=60)
        if len(fifteen) < 21 or len(hourly) < 20:
            return None
        fourh = self._client.klines(candidate.symbol, "4h", limit=60) if fetch_4h else None
        return self._plan(candidate, fifteen, hourly, generated_at, fourh=fourh)

    def _plan(
        self,
        candidate: HotlistCandidate,
        fifteen: tuple[Kline, ...],
        hourly: tuple[Kline, ...],
        generated_at: datetime,
        *,
        fourh: tuple[Kline, ...] | None = None,
    ) -> HotlistEntryPlan:
        current = fifteen[-1].close
        ema20 = _ema(tuple(item.close for item in fifteen), 20)
        atr14 = _atr(fifteen, 14)
        recent = fifteen[-21:-1]
        swing_high = max(item.high for item in recent)
        swing_low = min(item.low for item in recent)
        average_volume = sum((item.quote_volume for item in recent), Decimal("0")) / Decimal(len(recent))
        volume_ratio = (
            fifteen[-1].quote_volume / average_volume if average_volume > 0 else Decimal("0")
        )
        hourly_ema = _ema(tuple(item.close for item in hourly), 20)
        buffer = atr14 * Decimal("0.25")
        chg = candidate.change_24h_pct
        chg_str = f"+{chg:.2f}%" if chg > 0 else f"{chg:.2f}%"
        above_ema = current >= hourly_ema
        trend_str = "上方" if above_ema else "下方"
        sentiment = _sentiment(candidate.direction, chg, volume_ratio, above_ema)
        trend_aligned = above_ema if candidate.direction == "LONG" else not above_ema

        trend_4h_aligned = True
        trend_aligned_triple_4h = True
        if fourh and len(fourh) >= 50:
            closes_4h = tuple(k.close for k in fourh)
            ema50_4h = _ema(closes_4h, 50)
            above_4h_ema = current >= ema50_4h
            trend_4h_aligned = above_4h_ema if candidate.direction == "LONG" else not above_4h_ema
            ema10_4h = _ema(closes_4h, 10)
            ema20_4h = _ema(closes_4h, 20)
            if candidate.direction == "LONG":
                trend_aligned_triple_4h = ema10_4h > ema20_4h > ema50_4h
            else:
                trend_aligned_triple_4h = ema10_4h < ema20_4h < ema50_4h

        trend_aligned_triple_1h = True
        if len(hourly) >= 50:
            closes_1h = tuple(k.close for k in hourly)
            ema10_1h = _ema(closes_1h, 10)
            ema20_1h = _ema(closes_1h, 20)
            ema50_1h = _ema(closes_1h, 50)
            if candidate.direction == "LONG":
                trend_aligned_triple_1h = ema10_1h > ema20_1h > ema50_1h
            else:
                trend_aligned_triple_1h = ema10_1h < ema20_1h < ema50_1h

        if candidate.direction == "LONG":
            entry = min(ema20, current - buffer)
            stop = min(swing_low, entry - atr14)
            risk = entry - stop
            tp1 = entry + risk
            tp2 = entry + risk * Decimal("2")
            long_desc = "动量续涨做多" if chg > 0 else "低位反弹做多"
            reason = (
                f"24h涨跌{chg_str}，{long_desc}；"
                f"等15m EMA20回踩入场；"
                f"量比{_fmt(volume_ratio)}x；"
                f"价格在1h EMA20{trend_str}。"
            )
        else:
            entry = max(ema20, current + buffer)
            stop = max(swing_high, entry + atr14)
            risk = stop - entry
            tp1 = entry - risk
            tp2 = entry - risk * Decimal("2")
            short_desc = "高位超买做空" if chg > 0 else "趋势延续做空"
            reason = (
                f"24h涨跌{chg_str}，{short_desc}；"
                f"等15m EMA20反弹入场；"
                f"量比{_fmt(volume_ratio)}x；"
                f"价格在1h EMA20{trend_str}。"
            )
        return HotlistEntryPlan(
            symbol=candidate.symbol,
            direction=candidate.direction,
            current_price=_price(current),
            change_24h_pct=candidate.change_24h_pct,
            quote_volume=candidate.quote_volume,
            volume_ratio_15m=_ratio(volume_ratio),
            ema20_15m=_price(ema20),
            atr14=_price(atr14),
            swing_high=_price(swing_high),
            swing_low=_price(swing_low),
            suggested_limit_entry=_price(entry),
            stop_loss=_price(stop),
            tp1=_price(tp1),
            tp2=_price(tp2),
            rr=Decimal("2.00"),
            expires_at=(generated_at + timedelta(minutes=self._policy.expiry_minutes)).isoformat(
                timespec="seconds"
            ),
            reason=reason,
            sentiment=sentiment,
            trend_aligned=trend_aligned,
            trend_4h_aligned=trend_4h_aligned,
            trend_aligned_triple_1h=trend_aligned_triple_1h,
            trend_aligned_triple_4h=trend_aligned_triple_4h,
        )


def _sentiment(direction: str, chg: Decimal, volume_ratio: Decimal, above_ema: bool) -> str:
    if volume_ratio < Decimal("0.5"):
        return "🧊 低波整理"
    if direction == "LONG":
        if chg < Decimal("-10"):
            return "🔄 超跌反弹"
        if volume_ratio >= Decimal("1.5") and chg > 0:
            return "🚀 加速突破"
        if chg > 0 and above_ema:
            return "🔥 主升浪延续"
        if chg > 0 and not above_ema:
            return "⚡ 二波回踩"
        return "🔄 超跌反弹"
    else:
        if chg >= Decimal("10"):
            return "💀 高位派发"
        return "📉 趋势崩塌"


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("not enough values for EMA")
    multiplier = Decimal("2") / Decimal(period + 1)
    value = sum(values[:period], Decimal("0")) / Decimal(period)
    for current in values[period:]:
        value = (current - value) * multiplier + value
    return value


def _atr(klines: tuple[Kline, ...], period: int) -> Decimal:
    if len(klines) < period + 1:
        raise ValueError("not enough klines for ATR")
    ranges = []
    for previous, current in zip(klines[-period - 1:-1], klines[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges, Decimal("0")) / Decimal(period)


def _price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize()


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return str(_ratio(value))
