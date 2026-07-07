"""
Telegram notifier — Chinese output.  V1.2
All user-facing text is in Simplified Chinese; stock symbols remain in English.
"""

import requests
import logging
from market_hunter.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from market_hunter.scoring.scorer import volume_rating, volume_label_pullback, volume_label_breakout

logger = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

SECTOR_ZH: dict[str, str] = {
    "Information Technology": "信息技术",
    "Technology": "信息技术",
    "Financials": "金融",
    "Finance": "金融",
    "Industrials": "工业",
    "Health Care": "医疗健康",
    "Healthcare": "医疗健康",
    "Consumer Discretionary": "可选消费",
    "Consumer Staples": "日常消费",
    "Real Estate": "房地产",
    "Communication Services": "通信服务",
    "Communications": "通信服务",
    "Energy": "能源",
    "Utilities": "公用事业",
    "Materials": "材料",
}

STRATEGY_ZH: dict[str, str] = {
    "ma60_reclaim": "MA60回踩反转",
    "strong_trend": "主升浪回踩",
    "new_high":     "52周新高突破",
}

STRATEGY_EMOJI: dict[str, str] = {
    "ma60_reclaim": "📐",
    "strong_trend": "📈",
    "new_high":     "🚀",
}

STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "可关注买点":       ("✅", "可关注买点"),
    "观察":            ("⚠️", "观察"),
    "观察，等待回踩":   ("⚠️", "观察，等待回踩"),
    "观察，等待进入买入区": ("⚠️", "观察，等待进入买入区"),
    "风险过高，等待回踩": ("❗", "风险过高，等待更低回踩"),
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _zh_sector(sector: str) -> str:
    return SECTOR_ZH.get(sector, sector or "其他")


def _zh_strategy(key: str) -> str:
    return STRATEGY_ZH.get(key, key)


def _zh_strategy_with_emoji(key: str) -> str:
    emoji = STRATEGY_EMOJI.get(key, "")
    return f"{emoji} {STRATEGY_ZH.get(key, key)}"


def _price_str(val) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):,.2f}美元"


def _ma_str(val) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):,.2f}"


def _pct_str(val, plus: bool = True) -> str:
    if val is None:
        return "N/A"
    sign = "+" if (plus and float(val) >= 0) else ""
    return f"{sign}{float(val):.1f}%"


def _dollar_vol_zh(val) -> str:
    if val is None:
        return "N/A"
    v = float(val)
    yi = v / 1e8
    if yi >= 1:
        return f"{yi:.1f}亿美元"
    wan = v / 1e4
    if wan >= 1:
        return f"{wan:.0f}万美元"
    return f"{v:,.0f}美元"


def _entry_zone_str(ep: dict) -> str:
    lo = ep.get("entry_zone_low")
    hi = ep.get("entry_zone_high")
    if lo is None or hi is None:
        return "N/A"
    return f"{lo:,.2f} – {hi:,.2f}"


def _target2_str(ep: dict) -> str:
    t2   = ep.get("target2")
    note = ep.get("target2_note")
    if t2 is not None:
        s = f"{float(t2):,.2f}"
        if note:
            s += f"（{note}）"
        return s
    return note or "N/A"


def _rr_str(ep: dict) -> str:
    rr = ep.get("rr_ratio")
    if rr is None:
        return "N/A"
    return f"1:{float(rr):.1f}"


# ---------------------------------------------------------------------------
# V1.2 — Star rating
# ---------------------------------------------------------------------------

def _star_rating(ep: dict, total_score: float, vol_ratio: float | None) -> str:
    """
    ★★★★★  Score>=90, RR>=2.5, Risk<=5, Vol>=1.5
    ★★★★   Score>=85, RR>=2,   Risk<=6
    ★★★    Actionable (can buy)
    ★★     Risk high
    ★      Observation / ignore
    """
    rr   = float(ep.get("rr_ratio") or 0)
    risk = float(ep.get("risk_pct") or 999)
    vr   = float(vol_ratio or 0)
    status = ep.get("action_status", "")

    if status == "可关注买点":
        if total_score >= 90 and rr >= 2.5 and risk <= 5 and vr >= 1.5:
            return "★★★★★"
        if total_score >= 85 and rr >= 2.0 and risk <= 6:
            return "★★★★"
        return "★★★"
    if "风险" in status:
        return "★★"
    return "★"


# ---------------------------------------------------------------------------
# V1.2 — Distance to entry zone
# ---------------------------------------------------------------------------

def _distance_to_entry(close_price: float | None, ep: dict) -> str:
    """
    Already in zone   → 已在买入区间
    Above zone        → 高于买入区 +X.X%
    Below zone        → 低于买入区 -X.X%
    """
    if close_price is None:
        return "N/A"
    lo = ep.get("entry_zone_low")
    hi = ep.get("entry_zone_high")
    if lo is None or hi is None:
        return "N/A"
    lo, hi = float(lo), float(hi)
    cp = float(close_price)
    if lo <= cp <= hi:
        return "已在买入区间"
    if cp > hi:
        return f"高于买入区 +{(cp / hi - 1) * 100:.1f}%"
    return f"低于买入区 {(cp / lo - 1) * 100:.1f}%"


# ---------------------------------------------------------------------------
# V1.2 — One-line trading suggestion
# ---------------------------------------------------------------------------

def _trading_suggestion(ep: dict, close_price: float | None) -> str:
    status = ep.get("action_status", "")
    lo = ep.get("entry_zone_low")
    hi = ep.get("entry_zone_high")

    if status == "可关注买点":
        if lo and hi and close_price and float(lo) <= float(close_price) <= float(hi):
            return "建议：今日可挂单"
        return "建议：等待突破"
    if "等待回踩" in status:
        return "建议：等待回踩"
    if "等待进入" in status:
        return "建议：等待进入买入区"
    if "风险" in status:
        return "建议：等待更低回踩"
    return "建议：观察等待"


# ---------------------------------------------------------------------------
# V1.2 — Status block
# ---------------------------------------------------------------------------

def _status_block(ep: dict) -> list[str]:
    """Return lines for the 状态/原因/建议 block."""
    if not ep:
        return []
    status = ep.get("action_status") or "观察"
    reason = ep.get("action_reason") or ""
    emoji, label = STATUS_DISPLAY.get(status, ("⚠️", status))
    lines = [f"状态：{emoji} {label}"]
    if reason:
        lines.append(f"原因：{reason}")
    return lines


# ---------------------------------------------------------------------------
# Reason lines
# ---------------------------------------------------------------------------

def _build_reason_text(strategy_name: str, details: dict, diag: dict) -> str:
    lines = _reason_lines(strategy_name, details, diag)
    return "\n".join(lines)


def _reason_lines(strategy_name: str, details: dict, diag: dict) -> list[str]:
    vol_ratio = diag.get("volume_ratio")
    rs_20d    = diag.get("rs_vs_spy_20d")

    lines: list[str] = []

    if strategy_name == "ma60_reclaim":
        days_ago   = details.get("cross_days_ago", "?")
        days_below = details.get("days_below_before_cross", "?")
        pct_ma60   = details.get("pct_from_ma60")
        pct_str    = f"{pct_ma60:+.1f}%" if pct_ma60 is not None else ""
        lines.append(f"✓ {days_below}天下降趋势后，{days_ago}天前突破MA60")
        if pct_str:
            lines.append(f"✓ 回踩至MA60附近（{pct_str}）")
        vol_lbl = volume_label_pullback(vol_ratio)
        if vol_lbl:
            lines.append(f"✓ {vol_lbl}")
        if rs_20d is not None and rs_20d > 0:
            lines.append(f"✓ 20日相对SPY强势（{rs_20d:+.1f}%）")

    elif strategy_name == "strong_trend":
        near_ma20 = details.get("near_ma20", True)
        pct_ma20  = details.get("pct_from_ma20")
        pct_ma50  = details.get("pct_from_ma50")
        dist_52   = details.get("dist_52w_pct")
        lines.append("✓ MA20 > MA50 > MA200 趋势对齐")
        if near_ma20 and pct_ma20 is not None:
            lines.append(f"✓ 回踩至MA20附近（{pct_ma20:+.1f}%）")
        elif pct_ma50 is not None:
            lines.append(f"✓ 回踩至MA50附近（{pct_ma50:+.1f}%）")
        vol_lbl = volume_label_pullback(vol_ratio)
        if vol_lbl:
            lines.append(f"✓ {vol_lbl}")
        if dist_52 is not None:
            lines.append(f"✓ 距52周高点 {dist_52:.1f}%")
        if rs_20d is not None and rs_20d > 0:
            lines.append(f"✓ 强于SPY（20日：{rs_20d:+.1f}%）")

    elif strategy_name == "new_high":
        pct_above = details.get("pct_above_52w_high")
        vol_r     = details.get("vol_ratio") or vol_ratio
        vol_lbl   = volume_label_breakout(vol_r)
        if pct_above is not None:
            lines.append(f"✓ 突破52周高点 +{pct_above:.1f}%")
        if vol_lbl:
            lines.append(f"✓ {vol_lbl}（量比：{vol_r:.1f}倍）" if vol_r else f"✓ {vol_lbl}")
        lines.append("✓ MA20 和 MA50 趋势向上")
        if rs_20d is not None and rs_20d > 0:
            lines.append(f"✓ 强于SPY（20日：{rs_20d:+.1f}%）")

    return lines


# ---------------------------------------------------------------------------
# Stock card (V1.2)
# ---------------------------------------------------------------------------

def _format_stock_card(sig: dict, strategy_name: str) -> str:
    diag    = sig.get("diagnostics") or {}
    details = sig.get("strategy_details") or {}
    ep      = sig.get("entry_plan") or {}

    vol_ratio = diag.get("volume_ratio")
    vol_label, _ = volume_rating(vol_ratio)

    symbol      = sig["symbol"]
    sector      = _zh_sector(sig.get("sector") or "")
    strategy_zh = _zh_strategy_with_emoji(strategy_name)
    score       = sig.get("total_score", 0)
    close_price = sig.get("close_price")

    t_score   = sig.get("trend_score", 0)
    rs_score  = sig.get("relative_strength_score", 0)
    v_score   = sig.get("volume_score", 0)
    pb_score  = sig.get("pullback_risk_score", 0)
    sec_score = sig.get("sector_score", 0)

    stars      = _star_rating(ep, score, vol_ratio)
    dist_entry = _distance_to_entry(close_price, ep)
    suggestion = _trading_suggestion(ep, close_price)
    status_lns = _status_block(ep)
    reason_lns = _reason_lines(strategy_name, details, diag)

    rr_val   = ep.get("rr_ratio")
    risk_val = ep.get("risk_pct")
    risk_note = f"  ❗超过8%上限" if risk_val is not None and float(risk_val) > 8.0 else ""

    parts = [
        f"<b>{symbol}</b>  {stars}",
        f"行业：{sector}",
        f"策略：{strategy_zh}",
    ]

    # Status block
    if status_lns:
        parts.append("")
        parts.extend(status_lns)

    # Distance + suggestion
    parts += [
        "",
        f"距买入区：{dist_entry}",
        suggestion,
    ]

    # Scoring
    parts += [
        "",
        f"评分：<b>{score:.0f}分</b>",
        f"（趋势{t_score:.0f} 强弱{rs_score:.0f} 量能{v_score:.0f} 回踩{pb_score:.0f} 行业{sec_score:.0f}）",
    ]

    # Price + volume
    parts += [
        "",
        f"当前价格：{_price_str(close_price)}",
        f"{vol_label}  量比：{f'{vol_ratio:.2f}倍' if vol_ratio is not None else 'N/A'}",
        f"成交额：{_dollar_vol_zh(diag.get('dollar_volume'))}",
    ]

    # Moving averages
    parts += [
        "",
        f"MA20：{_ma_str(diag.get('ma20'))}  MA50：{_ma_str(diag.get('ma50'))}",
        f"MA60：{_ma_str(diag.get('ma60'))}  MA200：{_ma_str(diag.get('ma200'))}",
        f"52周高点距离：{_pct_str(diag.get('distance_52w_high'))}",
        f"相对SPY：20日{_pct_str(diag.get('rs_vs_spy_20d'))}  60日{_pct_str(diag.get('rs_vs_spy_60d'))}",
    ]

    # Entry plan
    if ep:
        parts += [
            "",
            f"买入区间：{_entry_zone_str(ep)}",
            f"触发价：{_price_str(ep.get('trigger_price'))}",
            f"止损：{_price_str(ep.get('stop_loss'))}",
            f"目标1：{_price_str(ep.get('target1'))}  目标2：{_target2_str(ep)}",
            f"风险：{f'{float(risk_val):.1f}%' if risk_val is not None else 'N/A'}{risk_note}  "
            f"风险回报比：{_rr_str(ep)}",
        ]

    # Reason
    if reason_lns:
        parts += ["", "信号依据："] + reason_lns

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Summary (V1.2)
# ---------------------------------------------------------------------------

def _format_summary(scan_date: str, results: dict) -> str:
    summary = results.get("summary", {})
    scanned  = summary.get("scanned_count", 0)
    valid    = summary.get("valid_price_count", scanned)
    rejected = summary.get("rejected_bad_price_count", 0)
    duration = results.get("duration_seconds", 0)

    dr = results.get("diagnostics_report", {})
    fs = results.get("filter_stats", {})

    ma60_cnt   = dr.get("ma60_count", 0)
    strong_cnt = dr.get("strong_trend_count", 0)
    nh_cnt     = dr.get("new_high_count", 0)
    avg_score  = dr.get("avg_score", 0)
    top_sectors = dr.get("top_sectors", [])

    total_triggered   = ma60_cnt + strong_cnt + nh_cnt
    total_actionable  = fs.get("total_actionable", 0)
    total_observation = fs.get("total_observation", 0)
    total_high_risk   = fs.get("total_high_risk", 0)
    cooldown_count    = fs.get("cooldown_count", 0)
    holding_count     = fs.get("holding_count", 0)
    telegram_count    = fs.get("telegram_count", 0)

    lines = [
        "📊 <b>美股每日扫描报告</b>",
        f"📅 {scan_date}",
        "",
        f"扫描 {scanned} 只股票  ✅ 有效 {valid}  ❌ 剔除 {rejected}  ⏱ {duration:.0f}秒",
        "",
        "─── 信号汇总 ───",
        f"策略触发：{total_triggered} 个  "
        f"（MA60 {ma60_cnt} · 主升浪 {strong_cnt} · 新高 {nh_cnt}）",
        "",
        f"✅ <b>真正可交易：{telegram_count} 个</b>",
        f"⚠️  观察信号：{total_observation} 个",
        f"❗ 风险过高：{total_high_risk} 个",
        f"⏳ 冷却期过滤：{cooldown_count} 个",
        f"📁 持仓过滤：{holding_count} 个",
        "",
        f"信号平均评分：{avg_score:.1f}分",
    ]

    if top_sectors:
        lines += ["", "热门行业 Top5："]
        medals = ["①", "②", "③", "④", "⑤"]
        for i, (sector, cnt) in enumerate(top_sectors):
            m = medals[i] if i < len(medals) else f"{i+1}."
            lines.append(f"  {m} {_zh_sector(sector)} {cnt}只")

    # Compact top10 by score
    top20 = results.get("top20", [])
    if top20:
        lines += ["", "🏆 <b>综合评分 Top10</b>"]
        for i, s in enumerate(top20[:10], 1):
            strats    = s.get("strategies") or []
            strat_tag = "  ".join(_zh_strategy(st) for st in strats)
            lines.append(
                f"{i:2}. <b>{s['symbol']:<5}</b> "
                f"{s['total_score']:4.0f}分  "
                f"{_price_str(s.get('close_price')):>12}  "
                f"{_zh_sector(s.get('sector') or '')[:8]}"
            )
            if strat_tag:
                lines.append(f"     └ {strat_tag}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Multi-message send
# ---------------------------------------------------------------------------

def _chunked_send(messages: list[str]) -> bool:
    ok = True
    for text in messages:
        if not text.strip():
            continue
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[:TELEGRAM_MAX_CHARS - 25] + "\n<i>...（内容过长已截断）</i>"
        ok &= send_message(text)
    return ok


def send_scan_report(scan_date: str, results: dict) -> bool:
    """
    Send scan report to Telegram.
    Strategy sections only contain 可关注买点 signals (Telegram-eligible).
    If no actionable signals for a strategy, that section is omitted.
    """
    messages: list[str] = []
    messages.append(_format_summary(scan_date, results))

    strategy_map = [
        ("telegram_ma60",     "ma60_reclaim",  STRATEGY_ZH["ma60_reclaim"]),
        ("telegram_strong",   "strong_trend",  STRATEGY_ZH["strong_trend"]),
        ("telegram_new_high", "new_high",       STRATEGY_ZH["new_high"]),
    ]

    for result_key, strat_key, label_zh in strategy_map:
        picks = results.get(result_key, [])
        if not picks:
            continue

        emoji = STRATEGY_EMOJI.get(strat_key, "")
        header = f"{emoji} <b>{label_zh} — 可交易 {len(picks)} 个</b>"

        section_parts = [header, "━" * 20]
        for sig in picks:
            section_parts.append(_format_stock_card(sig, strat_key))
            section_parts.append("━" * 20)

        section_text = "\n".join(section_parts)

        if len(section_text) > TELEGRAM_MAX_CHARS:
            messages.append(header)
            for sig in picks:
                messages.append(_format_stock_card(sig, strat_key))
        else:
            messages.append(section_text)

    ok = _chunked_send(messages)
    if ok:
        logger.info(f"Telegram scan report sent ({len(messages)} messages)")
    return ok


# ---------------------------------------------------------------------------
# Low-level
# ---------------------------------------------------------------------------

def send_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":                TELEGRAM_CHAT_ID,
        "text":                   text,
        "parse_mode":             "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


def send_error(error_msg: str) -> bool:
    text = f"<b>❌ Market Hunter 错误</b>\n\n<code>{error_msg}</code>"
    return send_message(text)
