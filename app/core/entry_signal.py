from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


ENTRY_SIGNAL_SCHEMA = "entry_signal_v1"
ENTRY_SIGNAL_STATUSES = frozenset({"OPENABLE", "WAIT", "REJECT", "EXPIRED", "UNKNOWN"})

_BLOCKED_POOL_STATUSES = {"BLOCKED"}
_EXPIRED_POOL_STATUSES = {"EXPIRED"}
_WARN_POOL_STATUSES = {"STALE"}


def build_entry_signal(
    pool_row: Dict[str, Any],
    *,
    candidate_row: Optional[Dict[str, Any]] = None,
    watch_row: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Build a deterministic, explainable entry decision card for one CSP put."""

    settings = settings or {}
    filters = settings.get("filters") or {}
    row = {**(candidate_row or {}), **(pool_row or {})}
    now_dt = now or datetime.now(timezone.utc)
    today_d = today or date.today()

    reasons: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    positives: List[Dict[str, Any]] = []

    def add(
        code: str,
        dimension: str,
        severity: str,
        message: str,
        *,
        current: Any = None,
        threshold: Any = None,
        passed: bool = False,
    ) -> None:
        reason = {
            "code": code,
            "dimension": dimension,
            "severity": severity,
            "message": message,
            "current": current,
            "threshold": threshold,
            "passed": bool(passed),
        }
        reasons.append(reason)
        if severity == "blocker":
            blockers.append(reason)
        elif severity == "warn":
            warnings.append(reason)
        elif severity == "positive":
            positives.append(reason)

    expiration = _parse_date(row.get("expiration"))
    pool_status = str(row.get("status") or "").upper()
    quality_grade = _quality_grade(row.get("quality_grade"))
    quality_score = _to_float(row.get("quality_score"))
    quality_flags = _string_list(row.get("quality_flags"))

    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("ask"))
    mid = _to_float(row.get("mid"))
    spot = _to_float(row.get("spot"))
    strike = _to_float(row.get("strike"))
    delta = _to_float(row.get("delta"))
    dte = _to_int(row.get("dte"))
    annualized_roi = _to_float(row.get("annualized_roi"))
    spread_pct = _to_float(row.get("spread_pct"))
    margin_buffer = _to_float(row.get("margin_buffer"))
    score = _to_float(row.get("score"))
    open_interest = _to_int(row.get("open_interest"))
    iv_rank = _to_float(row.get("iv_rank"))

    if expiration is not None and expiration < today_d:
        add("contract_expired", "risk", "blocker", "合约已过期", current=expiration.isoformat(), passed=False)
    elif pool_status in _EXPIRED_POOL_STATUSES:
        add("pool_expired", "risk", "blocker", "合约池状态已过期", current=pool_status, passed=False)

    if pool_status in _BLOCKED_POOL_STATUSES:
        add("pool_blocked", "data_quality", "blocker", "合约池已被系统标记为不可行动", current=pool_status, passed=False)
    elif pool_status in _WARN_POOL_STATUSES:
        add("pool_stale", "data_quality", "warn", "该合约近期未在扫描中出现，建议等待刷新", current=pool_status, passed=False)

    if quality_grade == "C":
        add("quality_c", "data_quality", "blocker", "免费行情质量不足，不能形成可靠开仓判断", current=quality_grade, passed=False)
    elif quality_grade == "unknown":
        add("quality_unknown", "data_quality", "warn", "该合约缺少完整质量评级，需人工核对报价", current=quality_grade, passed=False)
    elif quality_grade == "B":
        add("quality_b", "data_quality", "warn", "数据可观察但存在降级标记，入场前需复核", current=quality_grade, passed=True)
    else:
        add("quality_a", "data_quality", "positive", "数据质量满足决策要求", current=quality_grade, passed=True)

    if not (bid is not None and ask is not None and mid is not None and bid > 0 and ask > 0 and mid > 0 and ask >= bid):
        add("invalid_bid_ask", "liquidity", "blocker", "缺少有效双边报价", current={"bid": bid, "ask": ask, "mid": mid}, passed=False)

    max_spread = _to_float(filters.get("spread_pct_max"), 0.15) or 0.15
    if spread_pct is None:
        add("spread_missing", "liquidity", "warn", "缺少价差数据", passed=False)
    elif spread_pct > max_spread * 2:
        add("spread_too_wide", "liquidity", "blocker", "价差显著过宽，不适合登记开仓", current=spread_pct, threshold=max_spread, passed=False)
    elif spread_pct > max_spread:
        add("spread_wide_wait", "liquidity", "warn", "价差偏宽，建议等待更好报价", current=spread_pct, threshold=max_spread, passed=False)
    else:
        add("spread_pass", "liquidity", "positive", "价差处于可接受范围", current=spread_pct, threshold=max_spread, passed=True)

    min_oi = _to_int(filters.get("min_open_interest"), 10) or 10
    if open_interest is None:
        add("oi_missing", "liquidity", "warn", "缺少未平仓量数据", passed=False)
    elif open_interest < min_oi:
        add("oi_low", "liquidity", "warn", "未平仓量偏低，成交可能不顺畅", current=open_interest, threshold=min_oi, passed=False)
    else:
        add("oi_pass", "liquidity", "positive", "未平仓量满足最低要求", current=open_interest, threshold=min_oi, passed=True)

    min_dte = _to_int(filters.get("dte_min"), 21) or 21
    max_dte = _to_int(filters.get("dte_max"), 60) or 60
    if dte is None:
        add("dte_missing", "risk", "blocker", "缺少 DTE，无法评估到期风险", passed=False)
    elif dte < min_dte or dte > max_dte:
        add("dte_out_of_range", "risk", "blocker", "DTE 不在当前策略窗口", current=dte, threshold={"min": min_dte, "max": max_dte}, passed=False)
    else:
        add("dte_pass", "risk", "positive", "DTE 位于策略窗口内", current=dte, threshold={"min": min_dte, "max": max_dte}, passed=True)

    min_margin = _to_float(filters.get("margin_buffer_min"), 0.08) or 0.08
    if margin_buffer is None:
        add("margin_buffer_missing", "risk", "warn", "缺少安全垫数据", passed=False)
    elif margin_buffer < min_margin:
        add("margin_buffer_low", "risk", "blocker", "安全垫低于最低要求", current=margin_buffer, threshold=min_margin, passed=False)
    else:
        add("margin_buffer_pass", "risk", "positive", "安全垫满足最低要求", current=margin_buffer, threshold=min_margin, passed=True)

    delta_min = _to_float(filters.get("delta_min"), 0.1) or 0.1
    delta_max = _to_float(filters.get("delta_max"), 0.2) or 0.2
    abs_delta = abs(delta) if delta is not None else None
    if abs_delta is None:
        add("delta_missing", "risk", "blocker", "缺少 Delta，无法评估被指派风险", passed=False)
    elif abs_delta < delta_min or abs_delta > delta_max:
        add("delta_out_of_range", "risk", "warn", "Delta 不在偏好的卖 Put 区间", current=abs_delta, threshold={"min": delta_min, "max": delta_max}, passed=False)
    else:
        add("delta_pass", "risk", "positive", "Delta 位于偏好的卖 Put 区间", current=abs_delta, threshold={"min": delta_min, "max": delta_max}, passed=True)

    min_roi = _to_float(filters.get("annualized_roi_min"), 0.12) or 0.12
    if annualized_roi is None:
        add("roi_missing", "return", "warn", "缺少年化收益率", passed=False)
    elif annualized_roi < min_roi:
        add("roi_below_target", "return", "warn", "收益尚未达到当前开仓目标", current=annualized_roi, threshold=min_roi, passed=False)
    else:
        add("roi_pass", "return", "positive", "年化收益率达到最低要求", current=annualized_roi, threshold=min_roi, passed=True)

    min_iv_rank = _to_float(filters.get("iv_rank_min"), 0)
    if min_iv_rank is not None:
        if iv_rank is None:
            add("iv_rank_missing", "volatility", "warn", "缺少 IV Rank，波动率维度只能参考代理数据", passed=False)
        elif iv_rank < min_iv_rank:
            add("iv_rank_low", "volatility", "warn", "IV Rank 低于当前设置", current=iv_rank, threshold=min_iv_rank, passed=False)
        else:
            add("iv_rank_pass", "volatility", "positive", "IV Rank 满足当前设置", current=iv_rank, threshold=min_iv_rank, passed=True)

    _add_watch_target_reasons(add, row, watch_row)
    _add_timing_reasons(add, row)

    decision_score = _decision_score(
        score=score,
        quality_score=quality_score,
        positives=len(positives),
        warnings=len(warnings),
        blockers=len(blockers),
    )
    status = _status_for(blockers, warnings, decision_score, expiration, today_d)
    summary = _summary_for(status, positives, warnings, blockers)

    signal = {
        "schema": ENTRY_SIGNAL_SCHEMA,
        "status": status,
        "decision_score": decision_score,
        "summary": summary,
        "generated_at": now_dt.isoformat(),
        "symbol": row.get("symbol"),
        "expiration": row.get("expiration"),
        "strike": strike,
        "right": row.get("right") or "P",
        "source": {
            "option_pool_id": _to_int(row.get("option_pool_id") if row.get("option_pool_id") is not None else row.get("id")),
            "latest_candidate_id": _to_int(row.get("latest_candidate_id") if row.get("latest_candidate_id") is not None else row.get("candidate_id")),
            "scan_run_id": _to_int(row.get("last_scan_run_id") if row.get("last_scan_run_id") is not None else row.get("scan_run_id")),
        },
        "metrics": _metrics(row),
        "reasons": reasons,
        "blockers": blockers,
    }
    return signal


def _add_watch_target_reasons(add: Any, row: Dict[str, Any], watch_row: Optional[Dict[str, Any]]) -> None:
    if not watch_row:
        return
    checks = (
        ("target_premium", "mid", "return", "target_premium", "权利金达到观察目标", "权利金尚未达到观察目标"),
        ("target_score", "score", "return", "target_score", "策略评分达到观察目标", "策略评分尚未达到观察目标"),
        ("target_margin_buffer", "margin_buffer", "risk", "target_margin_buffer", "安全垫达到观察目标", "安全垫尚未达到观察目标"),
    )
    for target_key, actual_key, dimension, code, ok_message, wait_message in checks:
        target = _to_float(watch_row.get(target_key))
        if target is None:
            continue
        actual = _to_float(row.get(actual_key))
        if actual is not None and actual >= target:
            add(f"{code}_pass", dimension, "positive", ok_message, current=actual, threshold=target, passed=True)
        else:
            add(f"{code}_not_met", dimension, "warn", wait_message, current=actual, threshold=target, passed=False)


def _add_timing_reasons(add: Any, row: Dict[str, Any]) -> None:
    rsi_6 = _to_float(row.get("rsi_6"))
    bb_distance = _to_float(row.get("bb_distance_pct"))
    if rsi_6 is not None:
        if rsi_6 >= 75:
            add("timing_overbought", "timing", "warn", "短期 RSI 偏热，追价风险较高", current=rsi_6, threshold=75, passed=False)
        elif rsi_6 <= 35:
            add("timing_pullback", "timing", "positive", "短期 RSI 显示回落，卖 Put 安全垫更值得关注", current=rsi_6, threshold=35, passed=True)
    if bb_distance is not None:
        if bb_distance < 0:
            add("timing_below_lower_band", "timing", "warn", "价格跌破布林下轨，需防范趋势性下跌", current=bb_distance, threshold=0, passed=False)
        elif bb_distance <= 8:
            add("timing_near_lower_band", "timing", "positive", "价格接近布林下轨，入场位置相对克制", current=bb_distance, threshold=8, passed=True)


def _status_for(
    blockers: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    decision_score: int,
    expiration: Optional[date],
    today: date,
) -> str:
    if expiration is not None and expiration < today:
        return "EXPIRED"
    if blockers:
        return "REJECT"
    wait_codes = {r.get("code") for r in warnings}
    if wait_codes & {"roi_below_target", "spread_wide_wait", "target_premium_not_met", "target_score_not_met", "target_margin_buffer_not_met", "pool_stale"}:
        return "WAIT"
    if decision_score >= 60:
        return "OPENABLE"
    if warnings:
        return "WAIT"
    return "UNKNOWN"


def _decision_score(
    *,
    score: Optional[float],
    quality_score: Optional[float],
    positives: int,
    warnings: int,
    blockers: int,
) -> int:
    base = 45.0
    if score is not None:
        base = max(0.0, min(1.0, score)) * 65.0
    if quality_score is not None:
        base += max(0.0, min(100.0, quality_score)) * 0.2
    base += min(15.0, positives * 1.5)
    base -= min(20.0, warnings * 3.0)
    base -= min(35.0, blockers * 10.0)
    return int(round(max(0.0, min(100.0, base))))


def _summary_for(
    status: str,
    positives: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    blockers: List[Dict[str, Any]],
) -> str:
    if status == "EXPIRED":
        return "合约已过期，不能作为开仓对象。"
    if blockers:
        return f"存在硬性阻断：{blockers[0]['message']}。"
    if status == "OPENABLE":
        if warnings:
            return f"核心条件满足，可考虑开仓；但{warnings[0]['message']}。"
        if positives:
            return f"核心条件满足：{positives[0]['message']}，可进入人工确认。"
        return "核心条件满足，可进入人工确认。"
    if warnings:
        return f"建议等待：{warnings[0]['message']}。"
    return "数据不足，暂时无法形成稳定开仓判断。"


def _metrics(row: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mid = _to_float(row.get("mid"))
    strike = _to_float(row.get("strike"))
    spot = _to_float(row.get("spot"))
    return {
        "return": {
            "premium": mid,
            "annualized_roi": _to_float(row.get("annualized_roi")),
            "max_profit": mid * 100 if mid is not None else None,
            "capital_usage": strike * 100 if strike is not None else None,
            "score": _to_float(row.get("score")),
        },
        "risk": {
            "spot": spot,
            "strike": strike,
            "distance_to_strike_pct": _to_float(row.get("margin_buffer")),
            "margin_buffer": _to_float(row.get("margin_buffer")),
            "delta": _to_float(row.get("delta")),
            "dte": _to_int(row.get("dte")),
            "breakeven": _to_float(row.get("breakeven")),
        },
        "liquidity": {
            "bid": _to_float(row.get("bid")),
            "ask": _to_float(row.get("ask")),
            "mid": mid,
            "spread_pct": _to_float(row.get("spread_pct")),
            "open_interest": _to_int(row.get("open_interest")),
            "volume": _to_int(row.get("volume")),
        },
        "volatility": {
            "iv": _to_float(row.get("iv")),
            "iv_rank": _to_float(row.get("iv_rank")),
            "iv_rank_source": row.get("iv_rank_source"),
        },
        "timing": {
            "rsi_6": _to_float(row.get("rsi_6")),
            "rsi_12": _to_float(row.get("rsi_12")),
            "rsi_24": _to_float(row.get("rsi_24")),
            "bb_distance_pct": _to_float(row.get("bb_distance_pct")),
        },
        "data_quality": {
            "quality_grade": _quality_grade(row.get("quality_grade")),
            "quality_score": _to_int(row.get("quality_score")),
            "quality_flags": _string_list(row.get("quality_flags")),
            "quote_age_seconds": _to_int(row.get("quote_age_seconds")),
            "greeks_source": row.get("greeks_source"),
            "iv_rank_source": row.get("iv_rank_source"),
        },
    }


def _quality_grade(value: Any) -> str:
    text = str(value or "unknown").strip().upper()
    if text in {"A", "B", "C"}:
        return text
    return "unknown"


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    if isinstance(value, tuple):
        return [str(v) for v in value if str(v)]
    return [str(value)]
