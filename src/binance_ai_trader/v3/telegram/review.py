"""
/review <strategy> <days>  —  策略複盤引擎

資料來源（按可靠性排序）：
  1. v3_paper_orders         ← 訂單結果、pnl_pct、direction
  2. v3_candidates           ← 入場時快照（volume_ratio / change_24h / atr / stop_pct）
  3. v3_order_events         ← 持倉期間最高/最低價 → 計算 MFE / MAE
  4. metadata_json           ← 策略自定義快照（rsd、wave 的 pullback_ratio 等）

若欄位確實缺失 → 顯示 DATA_MISSING，禁止用當前行情偽造歷史數據。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

_NA = "DATA_MISSING"

# ─── 虧損原因標籤 ──────────────────────────────────────────────────────────
_REASON_LABELS: dict[str, str] = {
    "LONG_TOO_HIGH":        "追漲做多（信號時24h已大漲，高位入場）",
    "SHORT_TOO_LOW":        "追跌做空（信號時24h已大跌，低位入場）",
    "NOT_FIRST_PULLBACK":   "非首次回踩（多次反覆測試同一位置）",
    "NO_STRUCTURE_SUPPORT": "缺乏結構支撐（止損位無明確極值保護）",
    "NO_VOLUME_RESTART":    "量能不足（入場時量比偏低，缺乏確認）",
    "FALSE_BREAKOUT":       "假突破（入場後30min內快速反轉）",
    "STOP_TOO_TIGHT":       "止損過緊（<1.5%，極易被噪音觸發）",
    "NO_AVAILABLE_SPACE":   "空間不足（入場RR<1.5，結構無足夠利潤空間）",
}


# ─── 資料模型 ──────────────────────────────────────────────────────────────

@dataclass
class ReviewRecord:
    order_id:      str
    signal_id:     str
    symbol:        str
    direction:     str        # LONG | SHORT
    entry:         float
    stop_loss:     float
    tp1:           float
    tp2:           float
    rr_entry:      float      # RR at signal time
    result:        str | None  # TP1 | TP2 | SL | TIMEOUT
    created_at:    str
    filled_at:     str | None
    closed_at:     str | None
    pnl_pct:       float | None
    rr_realized:   float | None
    metadata:      dict        # parsed metadata_json
    # from v3_candidates (None = DATA_MISSING)
    volume_ratio:  float | None
    change_24h:    float | None
    atr:           float | None
    stop_pct:      float | None  # % distance entry→SL
    confidence:    float | None
    market_regime: str | None
    cand_reason:   str | None
    # from v3_order_events aggregated (None = no candle events recorded)
    peak_high:     float | None   # highest candle_high during hold
    valley_low:    float | None   # lowest candle_low during hold
    event_count:   int

    @property
    def is_win(self) -> bool:
        return self.pnl_pct is not None and self.pnl_pct > 0

    @property
    def is_sl(self) -> bool:
        return self.result == "SL"

    @property
    def hold_minutes(self) -> float | None:
        if not self.filled_at or not self.closed_at:
            return None
        try:
            def _p(s: str) -> datetime:
                s = s.replace("Z", "+00:00")
                if "+" not in s[10:] and s[-6] != "+":
                    s += "+00:00"
                return datetime.fromisoformat(s)
            return max(0.0, (_p(self.closed_at) - _p(self.filled_at)).total_seconds() / 60)
        except Exception:
            return None

    @property
    def mfe_pct(self) -> float | None:
        """Max Favorable Excursion as % of entry (positive = good)."""
        if self.peak_high is None or self.valley_low is None or not self.entry:
            return None
        if self.direction == "LONG":
            return (self.peak_high - self.entry) / self.entry * 100
        else:
            return (self.entry - self.valley_low) / self.entry * 100

    @property
    def mae_pct(self) -> float | None:
        """Max Adverse Excursion as % of entry (negative = bad)."""
        if self.peak_high is None or self.valley_low is None or not self.entry:
            return None
        if self.direction == "LONG":
            return (self.valley_low - self.entry) / self.entry * 100
        else:
            return (self.entry - self.peak_high) / self.entry * 100


# ─── 資料查詢 ──────────────────────────────────────────────────────────────

def _fetch_records(strategy_id: str, cutoff: str | None) -> list[ReviewRecord]:
    from binance_ai_trader.v3.storage.pg import get_conn

    params: list = [strategy_id]
    date_clause = ""
    if cutoff:
        date_clause = "AND o.created_at >= %s"
        params.append(cutoff)

    sql = f"""
        WITH mm AS (
            SELECT
                order_id,
                MAX(CASE WHEN candle_high ~ '^[0-9.]+$' THEN candle_high::FLOAT END) AS peak_high,
                MIN(CASE WHEN candle_low  ~ '^[0-9.]+$' THEN candle_low::FLOAT  END) AS valley_low,
                COUNT(*) AS event_count
            FROM v3_order_events
            WHERE candle_high IS NOT NULL OR candle_low IS NOT NULL
            GROUP BY order_id
        )
        SELECT
            o.order_id, o.signal_id, o.symbol, o.direction,
            o.entry, o.stop_loss, o.tp1, o.tp2, o.rr, o.result,
            o.created_at, o.filled_at, o.closed_at,
            o.pnl_pct, o.rr_realized,
            o.metadata_json,
            c.volume_ratio, c.change_24h,
            c.atr, c.stop_pct,
            c.confidence, c.market_regime, c.reason AS cand_reason,
            mm.peak_high, mm.valley_low,
            COALESCE(mm.event_count, 0) AS event_count
        FROM v3_paper_orders o
        LEFT JOIN v3_candidates c ON c.signal_id = o.signal_id
        LEFT JOIN mm ON mm.order_id = o.order_id
        WHERE o.strategy_id = %s
          AND o.status = 'CLOSED'
          AND o.result IN ('TP1','TP2','SL','TIMEOUT')
          {date_clause}
        ORDER BY o.created_at DESC
    """

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    def _f(v) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    records = []
    for r in rows:
        (order_id, signal_id, symbol, direction,
         entry, stop_loss, tp1, tp2, rr, result,
         created_at, filled_at, closed_at,
         pnl_pct, rr_realized, metadata_json,
         volume_ratio, change_24h, atr, stop_pct,
         confidence, market_regime, cand_reason,
         peak_high, valley_low, event_count) = r
        try:
            meta = json.loads(metadata_json or "{}")
        except Exception:
            meta = {}
        records.append(ReviewRecord(
            order_id=order_id,
            signal_id=signal_id or "",
            symbol=symbol,
            direction=direction,
            entry=_f(entry) or 0.0,
            stop_loss=_f(stop_loss) or 0.0,
            tp1=_f(tp1) or 0.0,
            tp2=_f(tp2) or 0.0,
            rr_entry=_f(rr) or 0.0,
            result=result,
            created_at=created_at or "",
            filled_at=filled_at,
            closed_at=closed_at,
            pnl_pct=_f(pnl_pct),
            rr_realized=_f(rr_realized),
            metadata=meta,
            volume_ratio=_f(volume_ratio),
            change_24h=_f(change_24h),
            atr=_f(atr),
            stop_pct=_f(stop_pct),
            confidence=_f(confidence),
            market_regime=market_regime,
            cand_reason=cand_reason,
            peak_high=_f(peak_high),
            valley_low=_f(valley_low),
            event_count=int(event_count or 0),
        ))
    return records


def _fetch_funnel(strategy_id: str, cutoff: str | None) -> dict:
    from binance_ai_trader.v3.storage.pg import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            base_params: list = [strategy_id]
            dc = ""
            if cutoff:
                dc = "AND created_at >= %s"
                base_params.append(cutoff)

            # total candidates
            cur.execute(
                f"SELECT COUNT(*) FROM v3_candidates WHERE strategy_id=%s {dc}",
                base_params,
            )
            total_cands: int = cur.fetchone()[0]

            # candidates that became orders
            cur.execute(
                f"""SELECT COUNT(DISTINCT c.signal_id)
                    FROM v3_candidates c
                    JOIN v3_paper_orders o ON o.signal_id = c.signal_id
                    WHERE c.strategy_id = %s {dc}""",
                base_params,
            )
            ordered: int = cur.fetchone()[0]

            # top rejection reasons (candidates that did NOT become orders)
            rej_params = base_params + [strategy_id]
            cur.execute(
                f"""SELECT reason, COUNT(*) AS cnt
                    FROM v3_candidates c
                    WHERE c.strategy_id = %s {dc}
                      AND c.signal_id NOT IN (
                          SELECT signal_id FROM v3_paper_orders
                          WHERE strategy_id = %s
                      )
                      AND reason IS NOT NULL AND reason != ''
                    GROUP BY reason
                    ORDER BY cnt DESC
                    LIMIT 8""",
                rej_params,
            )
            rejections: list[tuple] = cur.fetchall()

            # order fill / close stats
            ord_params: list = [strategy_id]
            odc = ""
            if cutoff:
                odc = "AND created_at >= %s"
                ord_params.append(cutoff)
            cur.execute(
                f"""SELECT
                        COUNT(*) FILTER (WHERE status='CLOSED')                 AS closed,
                        COUNT(*) FILTER (WHERE filled_at IS NOT NULL)           AS filled,
                        COUNT(*) FILTER (WHERE result='EXPIRED_NOT_FILLED')     AS expired,
                        COUNT(*)                                                 AS total_orders
                    FROM v3_paper_orders
                    WHERE strategy_id = %s {odc}""",
                ord_params,
            )
            row = cur.fetchone() or (0, 0, 0, 0)
            closed_n, filled_n, expired_n, total_orders = row
    finally:
        conn.close()

    return {
        "total_cands":  total_cands,
        "ordered":      ordered,
        "rejections":   rejections,
        "closed":       closed_n,
        "filled":       filled_n,
        "expired":      expired_n,
        "total_orders": total_orders,
    }


# ─── 統計計算 ──────────────────────────────────────────────────────────────

def _stats(records: list[ReviewRecord]) -> dict:
    if not records:
        return {"n": 0}

    pnl    = [r.pnl_pct for r in records if r.pnl_pct is not None]
    wins   = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    tp_n   = sum(1 for r in records if r.result in ("TP1", "TP2"))
    sl_n   = sum(1 for r in records if r.result == "SL")
    tp_sl  = tp_n + sl_n

    # max consecutive SL (scan chronologically = reversed DESC list)
    max_consec = cur_consec = 0
    for r in reversed(records):
        if r.result == "SL":
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    hold_times = [r.hold_minutes for r in records if r.hold_minutes is not None]
    avg_hold   = sum(hold_times) / len(hold_times) if hold_times else None

    return dict(
        n            = len(records),
        pnl_n        = len(pnl),
        win_rate     = len(wins) / len(pnl) * 100  if pnl    else None,
        tp_sl_rate   = tp_n / tp_sl * 100           if tp_sl  else None,
        tp_n         = tp_n,
        sl_n         = sl_n,
        total_pnl    = sum(pnl),
        expectancy   = sum(pnl) / len(pnl)         if pnl    else None,
        avg_win      = sum(wins)   / len(wins)      if wins   else None,
        avg_loss     = sum(losses) / len(losses)    if losses else None,
        pf           = sum(wins) / abs(sum(losses)) if wins and losses else None,
        max_consec_sl= max_consec,
        avg_hold     = avg_hold,
    )


def _status_label(r20: dict, all_t: dict) -> str:
    rr = r20.get("win_rate")
    at = all_t.get("win_rate")
    if r20["n"] < 5:
        return "⚪ 數據不足（<5筆）"
    if rr is None:
        return "⚪ 數據不足"
    if rr < 40:
        return "🔴 危險（近期勝率<40%）"
    if at is not None and rr < at - 15:
        return "🔴 嚴重退化（近期勝率比全期低>15%）"
    if at is not None and rr < at - 8:
        return "⚠️ 退化（近期勝率比全期低>8%）"
    if rr < 50:
        return "🟡 偏弱（近期勝率<50%）"
    return "✅ 正常"


# ─── 虧損原因分類器（基於可用快照數據的啟發規則）──────────────────────────

def _classify_loss_reason(rec: ReviewRecord) -> str:
    # 1. 最可靠：硬性指標
    if rec.stop_pct is not None and rec.stop_pct < 1.5:
        return "STOP_TOO_TIGHT"
    if rec.rr_entry is not None and 0 < rec.rr_entry < 1.5:
        return "NO_AVAILABLE_SPACE"

    # 2. 方向 + 24h動量（數據可靠）
    if rec.direction == "LONG"  and rec.change_24h is not None and rec.change_24h > 13:
        return "LONG_TOO_HIGH"
    if rec.direction == "SHORT" and rec.change_24h is not None and rec.change_24h < -13:
        return "SHORT_TOO_LOW"

    # 3. 快速止損（< 30min）= 假突破
    hm = rec.hold_minutes
    if hm is not None and hm < 30:
        return "FALSE_BREAKOUT"

    # 4. 低量能入場
    if rec.volume_ratio is not None and rec.volume_ratio < 0.85:
        return "NO_VOLUME_RESTART"

    # 5. metadata 裡有明確標記
    meta = rec.metadata
    if meta.get("is_first_pullback") is False or (meta.get("pullback_count") or 1) > 1:
        return "NOT_FIRST_PULLBACK"

    return "NO_STRUCTURE_SUPPORT"


# ─── 因子對比表 ────────────────────────────────────────────────────────────

def _factor_rows(
    win_recs: list[ReviewRecord], loss_recs: list[ReviewRecord]
) -> list[tuple[str, str, str, str]]:
    """Returns [(name, win_str, loss_str, coverage_str)]."""

    def _avg(recs: list, getter) -> float | None:
        vals = [v for r in recs if (v := getter(r)) is not None]
        return sum(vals) / len(vals) if vals else None

    def _cov(recs: list, getter) -> int:
        return sum(1 for r in recs if getter(r) is not None)

    def _fmt(v: float | None, dec: int = 2) -> str:
        return f"{v:.{dec}f}" if v is not None else _NA

    specs: list[tuple[str, any, int]] = [
        ("M15量比 (volume_ratio)",     lambda r: r.volume_ratio,  2),
        ("H1量比",                      lambda r: r.metadata.get("h1_vol_ratio"),  2),
        ("24h漲跌幅 (%)",               lambda r: r.change_24h,   2),
        ("回踩深度 stop_pct (%)",       lambda r: r.stop_pct,     2),
        ("ATR (絕對值)",                lambda r: r.atr,          4),
        ("入場RR",                      lambda r: r.rr_entry,     2),
        ("MFE 最高盈利 (%)",            lambda r: r.mfe_pct,      2),
        ("MAE 最大回撤 (%)",            lambda r: r.mae_pct,      2),
        ("持倉時長 (min)",              lambda r: r.hold_minutes, 0),
        ("首次回踩比例",                lambda r: r.metadata.get("pullback_count"), 0),
    ]

    rows = []
    all_recs = win_recs + loss_recs
    for name, getter, dec in specs:
        w_v = _avg(win_recs,  getter)
        l_v = _avg(loss_recs, getter)
        w_c = _cov(win_recs,  getter)
        l_c = _cov(loss_recs, getter)
        if w_c + l_c == 0:
            rows.append((name, _NA, _NA, "無數據"))
        else:
            cov = f"勝{w_c}/{len(win_recs)} 敗{l_c}/{len(loss_recs)}"
            rows.append((name, _fmt(w_v, dec), _fmt(l_v, dec), cov))
    return rows


# ─── 主格式化函數 ──────────────────────────────────────────────────────────

def format_review(strategy_id: str, days: int) -> str:  # noqa: C901
    """Build the full /review reply string."""

    cutoff: str | None = None
    if days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")

    records = _fetch_records(strategy_id, cutoff)
    funnel  = _fetch_funnel(strategy_id, cutoff)

    period = f"最近 {days} 天" if days > 0 else "全周期（不限天數）"
    lines = [
        f"📊 策略複盤  {strategy_id}",
        f"   周期: {period}   已結算訂單: {len(records)} 筆",
        "━" * 24,
    ]

    if not records:
        lines.append(
            "⚠️ 此策略在指定週期內無已結算訂單（TP1/TP2/SL/TIMEOUT）\n"
            "• 策略可能剛啟用，或訂單尚在持倉中\n"
            "• 可嘗試增大 <days>，例如 /review " + strategy_id + " 365"
        )
        # 仍然顯示漏斗
        _append_funnel(lines, funnel)
        return "\n".join(lines)

    all_s  = _stats(records)
    last50 = _stats(records[:50])
    last20 = _stats(records[:20])

    # ── 一、策略健康 ──────────────────────────────────────────────────────
    lines.append("\n━━ 一、策略健康 ━━━━━━━━━━━━━━━━━━━━")

    def _pf(v):  return f"{v:.2f}" if v is not None else "—"
    def _pp(v):  return f"{v:+.2f}%" if v is not None else "—"
    def _pr(v):  return f"{v:.1f}%" if v is not None else "—"
    def _ph(v):  return f"{v:.0f}min" if v is not None else "—"

    hdr = f"  {'':12}  {'訂單':>4}  {'淨勝率':>6}  {'PF':>5}  {'期望值':>7}  {'均盈':>7}  {'均虧':>7}  {'連虧':>4}  {'均持倉':>7}"
    sep = "  " + "─" * (len(hdr) - 2)
    lines.append(hdr)
    lines.append(sep)

    for label, s in [("最近20筆", last20), ("最近50筆", last50), ("全周期  ", all_s)]:
        n = s["n"]
        if n == 0:
            lines.append(f"  {label}  （無數據）")
            continue
        lines.append(
            f"  {label}  "
            f"{n:>4}  "
            f"{_pr(s.get('win_rate')):>6}  "
            f"{_pf(s.get('pf')):>5}  "
            f"{_pp(s.get('expectancy')):>7}  "
            f"{_pp(s.get('avg_win')):>7}  "
            f"{_pp(s.get('avg_loss')):>7}  "
            f"{s.get('max_consec_sl',0):>4}  "
            f"{_ph(s.get('avg_hold')):>7}"
        )

    status = _status_label(last20, all_s)
    lines.append(
        f"\n  狀態: {status}"
        f"  （近20筆勝率 {_pr(last20.get('win_rate'))} | 全期 {_pr(all_s.get('win_rate'))}）"
    )
    lines.append(
        f"  全期: TP={all_s['tp_n']} SL={all_s['sl_n']} "
        f"TIMEOUT={sum(1 for r in records if r.result=='TIMEOUT')}  "
        f"累計pnl: {_pp(all_s.get('total_pnl'))}"
    )

    # ── 二、方向拆分 ──────────────────────────────────────────────────────
    lines.append("\n━━ 二、方向拆分 ━━━━━━━━━━━━━━━━━━━━")
    longs  = [r for r in records if r.direction == "LONG"]
    shorts = [r for r in records if r.direction == "SHORT"]
    ls, ss = _stats(longs), _stats(shorts)
    for label, s, recs in [("LONG ", ls, longs), ("SHORT", ss, shorts)]:
        if s["n"] == 0:
            lines.append(f"  {label}: 無訂單")
        else:
            lines.append(
                f"  {label} ({s['n']}筆)  "
                f"勝率:{_pr(s.get('win_rate'))}  "
                f"PF:{_pf(s.get('pf'))}  "
                f"期望:{_pp(s.get('expectancy'))}  "
                f"均盈:{_pp(s.get('avg_win'))}  "
                f"均虧:{_pp(s.get('avg_loss'))}  "
                f"最大連虧:{s.get('max_consec_sl',0)}"
            )

    # ── 三、勝負因子對比 ──────────────────────────────────────────────────
    lines.append("\n━━ 三、勝負訂單因子對比 ━━━━━━━━━━━━━━")
    win_recs  = [r for r in records if r.is_win]
    sl_recs   = [r for r in records if r.result == "SL"]
    tout_recs = [r for r in records if r.result == "TIMEOUT"]
    lines.append(
        f"  勝單:{len(win_recs)}筆  "
        f"SL:{len(sl_recs)}筆  "
        f"TIMEOUT:{len(tout_recs)}筆"
    )

    # candidate snapshot coverage
    snap_n = sum(1 for r in records if r.volume_ratio is not None)
    event_n = sum(1 for r in records if r.event_count > 0)
    lines.append(
        f"  快照覆蓋率: 候選快照 {snap_n}/{len(records)} 筆 | "
        f"K線事件 {event_n}/{len(records)} 筆"
    )
    if snap_n == 0:
        lines.append(
            "  ⚠ 無候選快照（舊訂單或 v3_candidates 數據缺失）"
            "→ 量比/ATR欄位顯示 DATA_MISSING"
        )

    lines.append(f"\n  {'因子':<24}  {'勝單均值':>12}  {'敗單均值':>12}  覆蓋")
    lines.append("  " + "─" * 65)
    for name, w_s, l_s, cov in _factor_rows(win_recs, sl_recs):
        lines.append(f"  {name:<24}  {w_s:>12}  {l_s:>12}  {cov}")

    # ── 四、虧損原因 Top5 ─────────────────────────────────────────────────
    lines.append("\n━━ 四、虧損原因 Top5 ━━━━━━━━━━━━━━━━━")
    if not sl_recs:
        lines.append("  無 SL 訂單")
    else:
        reason_counter = Counter(_classify_loss_reason(r) for r in sl_recs)
        total_sl = len(sl_recs)
        for i, (reason, cnt) in enumerate(reason_counter.most_common(5), 1):
            pct = cnt / total_sl * 100
            bar_len = min(10, max(1, int(pct / 5)))
            bar = "█" * bar_len + "░" * (10 - bar_len)
            label = _REASON_LABELS.get(reason, reason)
            lines.append(f"  {i}. [{bar}] {cnt:3}筆 ({pct:4.1f}%)  {reason}")
            lines.append(f"     └ {label}")

        # 說明分類器的依據
        cand_snap = sum(1 for r in sl_recs if r.volume_ratio is not None)
        evt_snap  = sum(1 for r in sl_recs if r.event_count > 0)
        lines.append(
            f"\n  分類依據: stop_pct/RR/change_24h/hold_minutes/volume_ratio/meta_json"
        )
        if cand_snap < total_sl:
            lines.append(
                f"  ⚠ {total_sl - cand_snap}/{total_sl} 筆缺少候選快照"
                f" → 部分原因為估算，非精確分類"
            )

    # ── 五、最近10筆訂單 ───────────────────────────────────────────────────
    lines.append("\n━━ 五、最近10筆訂單 ━━━━━━━━━━━━━━━━━━")
    for i, r in enumerate(records[:10], 1):
        icon = "✅" if r.is_win else ("❌" if r.result == "SL" else "⏱" if r.result == "TIMEOUT" else "🔵")
        pnl_s  = f"{r.pnl_pct:+.2f}%" if r.pnl_pct is not None else "—"
        hold_s = f"{r.hold_minutes:.0f}m" if r.hold_minutes is not None else "—"
        dt     = (r.closed_at or r.created_at or "")[:10]
        row = (
            f"  {i:02d} {icon} {r.symbol:<12} {r.direction:<5} "
            f"{str(r.result):<8} {pnl_s:>7}  {hold_s:>6}  {dt}"
        )
        if r.result == "SL":
            row += f"  → {_classify_loss_reason(r)}"
        lines.append(row)

    # ── 六、篩選漏斗 ──────────────────────────────────────────────────────
    _append_funnel(lines, funnel)

    lines.append("")
    lines.append(f"用法: /review {strategy_id} 30  /review {strategy_id} 90")
    return "\n".join(lines)


def _append_funnel(lines: list[str], f: dict) -> None:
    lines.append("\n━━ 六、篩選漏斗 ━━━━━━━━━━━━━━━━━━━━━")
    total_c = f["total_cands"]
    ordered = f["ordered"]
    filled  = f["filled"]
    closed  = f["closed"]
    expired = f["expired"]

    if total_c == 0:
        lines.append("  v3_candidates 此策略此週期無記錄")
        lines.append("  （舊策略遷移前的訂單無候選快照，屬正常現象）")
        return

    def _rt(a: int, b: int) -> str:
        return f"{a/b*100:.1f}%" if b else "—"

    lines.append(f"  候選信號       {total_c:>6} 個  （v3_candidates 原始計數）")
    lines.append(f"  → 進訂單      {ordered:>6} 個  ({_rt(ordered, total_c)})  去重+風控+RR過濾後")
    lines.append(f"  → 成交入場    {filled:>6} 個  ({_rt(filled, ordered)})  等到回踩觸發填單")
    lines.append(f"  → 已結算      {closed:>6} 個  ({_rt(closed, filled)})  TP/SL/TIMEOUT")
    if expired:
        lines.append(f"  → 未觸發過期  {expired:>6} 個  掛單到期未成交（EXPIRED_NOT_FILLED）")

    rejections = f.get("rejections", [])
    if rejections:
        lines.append("  主要拒絕原因（未進訂單的候選信號）:")
        for reason, cnt in rejections[:5]:
            lines.append(f"    {reason:<35} {cnt:>4} 個")
    else:
        lines.append("  拒絕原因: v3_candidates.reason 欄位無詳細記錄")
