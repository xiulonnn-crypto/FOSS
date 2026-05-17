from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


EXIT_SIGNAL_SCHEMA = "exit_signal_v1"
EXIT_ACTIONS = frozenset(
    {
        "HOLD",
        "HOLD_TO_EXPIRY",
        "TAKE_PROFIT",
        "ACCELERATE_TAKE_PROFIT",
        "TIME_EXIT",
        "DEFEND",
        "EXPIRED",
        "UNKNOWN",
    }
)
EXIT_SEVERITIES = frozenset({"info", "warn", "danger"})

_REQUIRED_POSITION_FIELDS = ("expiration", "strike", "open_premium")
_REQUIRED_MARK_FIELDS = ("spot", "current_mid", "pnl_pct", "margin_buffer")


def build_exit_signal(
    position: Dict[str, Any],
    mark: Dict[str, Any],
    settings: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a deterministic, explainable exit action for one OPEN short put."""

    position = position or {}
    mark = mark or {}
    settings = settings or {}
    exits = settings.get("exits") or {}
    now_dt = _coerce_datetime(now)
    today = now_dt.date()

    reasons: List[Dict[str, Any]] = []

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
        reasons.append(
            {
                "code": code,
                "dimension": dimension,
                "severity": severity,
                "message": message,
                "current": current,
                "threshold": threshold,
                "passed": bool(passed),
            }
        )

    expiration = _parse_date(position.get("expiration"))
    strike = _to_float(position.get("strike"))
    open_premium = _to_float(position.get("open_premium"))
    open_at = _parse_datetime(position.get("open_at"))
    holding_days = _holding_days(position, open_at, now_dt)

    current_mid = _mark_float(mark, "current_mid", "option_mid", "mid")
    spot = _mark_float(mark, "spot", "current_spot")
    delta = _mark_float(mark, "delta", "current_delta")
    pnl_pct = _mark_float(mark, "pnl_pct")
    margin_buffer = _mark_float(mark, "margin_buffer")
    if margin_buffer is None and spot is not None and strike is not None and spot > 0:
        margin_buffer = (spot - strike) / spot
    if (
        pnl_pct is None
        and open_premium is not None
        and open_premium > 0
        and current_mid is not None
    ):
        pnl_pct = 1.0 - (current_mid / open_premium)

    dte = _dte(expiration, today)
    metrics = {
        "spot": spot,
        "strike": strike,
        "open_premium": open_premium,
        "current_mid": current_mid,
        "unrealized_pnl_usd": _mark_float(mark, "unrealized_pnl_usd"),
        "delta": delta,
        "margin_buffer": _round_or_none(margin_buffer),
        "pnl_pct": _round_or_none(pnl_pct),
        "dte": dte,
        "holding_days": holding_days,
        "_radar_snapshot_id": _to_int(mark.get("radar_snapshot_id")),
        "_mark_basis": mark.get("mark_basis"),
    }

    position_missing = _missing_position_fields(
        position,
        expiration,
        strike,
        open_premium,
    )
    if position_missing:
        add(
            "position_missing_fields",
            "data_quality",
            "warn",
            "持仓缺少出场判断所需字段",
            current=position_missing,
            threshold=list(_REQUIRED_POSITION_FIELDS),
            passed=False,
        )
        return _signal(
            position,
            now_dt,
            action="UNKNOWN",
            severity="warn",
            suggested_close_reason=None,
            legacy_signals=[],
            metrics=metrics,
            reasons=reasons,
        )

    if dte is not None and dte < 0:
        add(
            "position_expired",
            "time",
            "info",
            "合约已过期，需进入结算流程",
            current=dte,
            threshold=0,
            passed=True,
        )
        return _signal(
            position,
            now_dt,
            action="EXPIRED",
            severity="info",
            suggested_close_reason=None,
            legacy_signals=[],
            metrics=metrics,
            reasons=reasons,
        )

    mark_error = _mark_error(mark)
    mark_missing = _missing_mark_fields(mark, spot, current_mid)
    if mark_error is not None or mark_missing:
        if mark_error is not None:
            add(
                "mark_unavailable",
                "data_quality",
                "warn",
                "行情或持仓标记失败，不能形成可靠平仓动作",
                current=mark_error,
                threshold="valid mark",
                passed=False,
            )
        if mark_missing:
            add(
                "mark_missing_fields",
                "data_quality",
                "warn",
                "行情标记缺少出场判断所需字段",
                current=mark_missing,
                threshold=list(_REQUIRED_MARK_FIELDS),
                passed=False,
            )
        return _signal(
            position,
            now_dt,
            action="UNKNOWN",
            severity="warn",
            suggested_close_reason=None,
            legacy_signals=[],
            metrics=metrics,
            reasons=reasons,
        )

    add(
        "mark_complete",
        "data_quality",
        "info",
        "行情标记字段完整",
        current={
            k: metrics[k]
            for k in ("spot", "current_mid", "pnl_pct", "margin_buffer")
        },
        threshold=list(_REQUIRED_MARK_FIELDS),
        passed=True,
    )

    legacy_signals = _legacy_signals(
        position=position,
        spot=spot,
        current_mid=current_mid,
        delta=delta,
        pnl_pct=pnl_pct,
        dte=dte,
        settings=settings,
    )

    loss_pnl = _to_float(exits.get("loss_pnl_pct_danger"), -0.50)
    delta_thresh = _to_float(exits.get("delta_breach_abs"), 0.40) or 0.40
    defend_codes: List[str] = []
    if spot is not None and strike is not None and spot <= strike:
        defend_codes.append("spot_below_strike")
        add(
            "spot_below_strike",
            "risk",
            "danger",
            "标的价格已跌至或跌破行权价，需优先防守",
            current=spot,
            threshold=strike,
            passed=False,
        )
    if margin_buffer is not None and margin_buffer < 0:
        defend_codes.append("margin_buffer_negative")
        add(
            "margin_buffer_negative",
            "risk",
            "danger",
            "安全垫为负，持仓已进入防守区",
            current=_round_or_none(margin_buffer),
            threshold=0,
            passed=False,
        )
    if delta is not None and abs(delta) >= delta_thresh:
        defend_codes.append("delta_breach")
        add(
            "delta_breach",
            "risk",
            "danger",
            "Delta 已突破防守阈值",
            current=abs(delta),
            threshold=delta_thresh,
            passed=False,
        )
    if loss_pnl is not None and pnl_pct is not None and pnl_pct <= loss_pnl:
        defend_codes.append("loss_breach")
        add(
            "loss_breach",
            "profit",
            "danger",
            "浮亏扩大并触及止损防守阈值",
            current=_round_or_none(pnl_pct),
            threshold=loss_pnl,
            passed=False,
        )
    if defend_codes:
        return _signal(
            position,
            now_dt,
            action="DEFEND",
            severity="danger",
            suggested_close_reason=_defend_close_reason(defend_codes),
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )

    time_danger = _to_int(exits.get("time_danger_dte"), 7) or 7
    time_warning = _to_int(exits.get("time_warning_dte"), 14) or 14
    expiry_hold_max_mid = _to_float(exits.get("expiry_hold_max_mid"), 0.05)
    expiry_hold_min_margin = _to_float(exits.get("expiry_hold_min_margin_buffer"), 0.05)
    if (
        dte is not None
        and dte <= time_danger
        and current_mid is not None
        and expiry_hold_max_mid is not None
        and current_mid <= expiry_hold_max_mid
        and margin_buffer is not None
        and expiry_hold_min_margin is not None
        and margin_buffer >= expiry_hold_min_margin
    ):
        add(
            "expiry_hold_candidate",
            "time",
            "info",
            "临近到期且剩余价值很低，安全垫仍满足持有到期条件",
            current={
                "dte": dte,
                "current_mid": current_mid,
                "margin_buffer": _round_or_none(margin_buffer),
            },
            threshold={
                "dte_max": time_danger,
                "max_mid": expiry_hold_max_mid,
                "min_margin_buffer": expiry_hold_min_margin,
            },
            passed=True,
        )
        return _signal(
            position,
            now_dt,
            action="HOLD_TO_EXPIRY",
            severity="info",
            suggested_close_reason=None,
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )

    if dte is not None and dte <= time_danger:
        add(
            "time_7d",
            "time",
            "danger",
            "剩余天数已进入 7 天防守窗口",
            current=dte,
            threshold=time_danger,
            passed=False,
        )
        return _signal(
            position,
            now_dt,
            action="TIME_EXIT",
            severity="danger",
            suggested_close_reason="time_7d",
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )
    if dte is not None and dte <= time_warning:
        add(
            "time_14d",
            "time",
            "warn",
            "剩余天数已进入 14 天管理窗口",
            current=dte,
            threshold=time_warning,
            passed=False,
        )
        return _signal(
            position,
            now_dt,
            action="TIME_EXIT",
            severity="warn",
            suggested_close_reason="time_14d",
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )

    fast_profit_days = _to_int(exits.get("fast_profit_days"), 7)
    fast_profit_pct = _to_float(exits.get("fast_profit_pct"))
    if fast_profit_pct is None:
        fast_profit_pct = _to_float(exits.get("take_profit_pct"), 0.50)
    if (
        holding_days is not None
        and fast_profit_days is not None
        and holding_days <= fast_profit_days
        and fast_profit_pct is not None
        and pnl_pct is not None
        and pnl_pct >= fast_profit_pct
    ):
        add(
            "take_profit_fast",
            "profit",
            "warn",
            "开仓后短时间内已达到快速止盈阈值",
            current={
                "holding_days": holding_days,
                "pnl_pct": _round_or_none(pnl_pct),
            },
            threshold={
                "max_holding_days": fast_profit_days,
                "pnl_pct": fast_profit_pct,
            },
            passed=True,
        )
        return _signal(
            position,
            now_dt,
            action="ACCELERATE_TAKE_PROFIT",
            severity="warn",
            suggested_close_reason="take_profit_fast",
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )

    take_profit_strong = _to_float(exits.get("take_profit_strong_pct"), 0.75) or 0.75
    take_profit = _to_float(exits.get("take_profit_pct"), 0.50) or 0.50
    if pnl_pct is not None and pnl_pct >= take_profit_strong:
        add(
            "take_profit_75",
            "profit",
            "warn",
            "已捕获约 75% 或更多最大盈利",
            current=_round_or_none(pnl_pct),
            threshold=take_profit_strong,
            passed=True,
        )
        return _signal(
            position,
            now_dt,
            action="TAKE_PROFIT",
            severity="warn",
            suggested_close_reason="take_profit_75",
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )
    if pnl_pct is not None and pnl_pct >= take_profit:
        add(
            "take_profit_50",
            "profit",
            "warn",
            "已捕获约 50% 或更多最大盈利",
            current=_round_or_none(pnl_pct),
            threshold=take_profit,
            passed=True,
        )
        return _signal(
            position,
            now_dt,
            action="TAKE_PROFIT",
            severity="warn",
            suggested_close_reason="take_profit_50",
            legacy_signals=legacy_signals,
            metrics=metrics,
            reasons=reasons,
        )

    add(
        "hold_conditions",
        "risk",
        "info",
        "未触发防守、止盈或时间退出规则",
        current={
            "dte": dte,
            "pnl_pct": _round_or_none(pnl_pct),
            "margin_buffer": _round_or_none(margin_buffer),
            "delta": delta,
        },
        threshold={
            "time_warning_dte": time_warning,
            "take_profit_pct": take_profit,
            "delta_breach_abs": delta_thresh,
            "loss_pnl_pct_danger": loss_pnl,
        },
        passed=True,
    )
    return _signal(
        position,
        now_dt,
        action="HOLD",
        severity="info",
        suggested_close_reason=None,
        legacy_signals=legacy_signals,
        metrics=metrics,
        reasons=reasons,
    )


def _signal(
    position: Dict[str, Any],
    now_dt: datetime,
    *,
    action: str,
    severity: str,
    suggested_close_reason: Optional[str],
    legacy_signals: Sequence[str],
    metrics: Dict[str, Any],
    reasons: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if action not in EXIT_ACTIONS:
        action = "UNKNOWN"
    if severity not in EXIT_SEVERITIES:
        severity = "warn"
    return {
        "schema": EXIT_SIGNAL_SCHEMA,
        "action": action,
        "severity": severity,
        "urgency_score": _urgency_score(action, severity, suggested_close_reason, metrics),
        "suggested_close_reason": suggested_close_reason,
        "summary": _summary(action, suggested_close_reason, reasons),
        "legacy_signals": list(dict.fromkeys(legacy_signals)),
        "generated_at": now_dt.isoformat(),
        "position_id": _to_int(position.get("id")),
        "symbol": position.get("symbol"),
        "expiration": position.get("expiration"),
        "strike": _to_float(position.get("strike")),
        "source": {
            "radar_snapshot_id": metrics.get("_radar_snapshot_id"),
            "mark_basis": metrics.get("_mark_basis"),
            "latest_entry_signal_id": _to_int(
                position.get("latest_entry_signal_id")
                or position.get("entry_signal_id")
            ),
        },
        "metrics": {
            key: value for key, value in metrics.items() if not key.startswith("_")
        },
        "reasons": reasons,
    }


def _summary(
    action: str,
    suggested_close_reason: Optional[str],
    reasons: Sequence[Dict[str, Any]],
) -> str:
    triggered = [r for r in reasons if r.get("passed") is True]
    blockers = [r for r in reasons if r.get("severity") == "danger"]
    primary = blockers[-1] if blockers else (triggered[-1] if triggered else (reasons[-1] if reasons else {}))
    message = str(primary.get("message") or "").strip()
    if action == "TAKE_PROFIT":
        return message or "达到止盈阈值，建议评估买回锁定收益。"
    if action == "ACCELERATE_TAKE_PROFIT":
        return message or "短时间已捕获较多权利金，建议加速止盈。"
    if action == "TIME_EXIT":
        return message or "临近到期，时间与尾部风险上升，建议评估退出。"
    if action == "DEFEND":
        return message or "持仓风险触发防守条件，建议优先处理。"
    if action == "HOLD_TO_EXPIRY":
        return message or "剩余价值很低且风险仍可控，可考虑等待到期。"
    if action == "EXPIRED":
        return message or "合约已到期，需进入结算流程。"
    if action == "UNKNOWN":
        return message or "行情或标记数据不足，暂不能形成可靠建议。"
    if suggested_close_reason:
        return message or f"触发 {suggested_close_reason}。"
    return message or "未触发动作规则，继续持有并观察。"


def _urgency_score(
    action: str,
    severity: str,
    suggested_close_reason: Optional[str],
    metrics: Dict[str, Any],
) -> int:
    base = {
        "UNKNOWN": 0,
        "HOLD": 12,
        "HOLD_TO_EXPIRY": 34,
        "TAKE_PROFIT": 62,
        "ACCELERATE_TAKE_PROFIT": 72,
        "TIME_EXIT": 68,
        "DEFEND": 88,
        "EXPIRED": 76,
    }.get(action, 0)
    if severity == "danger":
        base += 10
    elif severity == "warn":
        base += 4
    pnl_pct = _to_float(metrics.get("pnl_pct"))
    dte = _to_int(metrics.get("dte"))
    margin_buffer = _to_float(metrics.get("margin_buffer"))
    if action in {"TAKE_PROFIT", "ACCELERATE_TAKE_PROFIT"} and pnl_pct is not None:
        base += min(8, max(0, int((pnl_pct - 0.5) * 20)))
    if action == "TIME_EXIT" and dte is not None:
        base += max(0, min(8, 8 - dte))
    if action == "DEFEND" and margin_buffer is not None and margin_buffer < 0:
        base += 6
    if suggested_close_reason == "loss_breach":
        base += 6
    return max(0, min(100, int(round(base))))


def _legacy_signals(
    *,
    position: Dict[str, Any],
    spot: Optional[float],
    current_mid: Optional[float],
    delta: Optional[float],
    pnl_pct: Optional[float],
    dte: Optional[int],
    settings: Dict[str, Any],
) -> List[str]:
    exits = settings.get("exits") or {}
    signals: List[str] = []
    tp_pct = _to_float(exits.get("take_profit_pct"), 0.50) or 0.50
    tp_strong = _to_float(exits.get("take_profit_strong_pct"), 0.75) or 0.75
    time_warn = _to_int(exits.get("time_warning_dte"), 14) or 14
    time_danger = _to_int(exits.get("time_danger_dte"), 7) or 7
    dist_pct = _to_float(exits.get("danger_distance_pct"), 0.03) or 0.03
    delta_thresh = _to_float(exits.get("delta_breach_abs"), 0.40) or 0.40

    if pnl_pct is None:
        open_premium = _to_float(position.get("open_premium"))
        if open_premium is not None and open_premium > 0 and current_mid is not None:
            pnl_pct = 1.0 - (current_mid / open_premium)
    if pnl_pct is not None:
        if pnl_pct >= tp_strong:
            signals.append("take_profit_75")
        elif pnl_pct >= tp_pct:
            signals.append("take_profit_50")

    if dte is not None:
        if dte <= time_danger:
            signals.append("time_7d")
        elif dte <= time_warn:
            signals.append("time_14d")

    strike = _to_float(position.get("strike"))
    if strike is not None and strike > 0 and spot is not None and spot > 0:
        dist = (spot - strike) / strike
        if dist <= dist_pct:
            signals.append("danger_3pct")

    if delta is not None and abs(delta) >= delta_thresh:
        signals.append("delta_breach")

    return signals


def _defend_close_reason(defend_codes: Sequence[str]) -> str:
    if "loss_breach" in defend_codes:
        return "loss_breach"
    if "spot_below_strike" in defend_codes or "margin_buffer_negative" in defend_codes:
        return "danger_3pct"
    if "delta_breach" in defend_codes:
        return "delta_breach"
    return "loss_breach"


def _missing_position_fields(
    position: Dict[str, Any],
    expiration: Optional[date],
    strike: Optional[float],
    open_premium: Optional[float],
) -> List[str]:
    missing: List[str] = []
    if expiration is None:
        missing.append("expiration")
    if strike is None or strike <= 0:
        missing.append("strike")
    if open_premium is None or open_premium < 0:
        missing.append("open_premium")
    return missing


def _missing_mark_fields(
    mark: Dict[str, Any],
    spot: Optional[float],
    current_mid: Optional[float],
) -> List[str]:
    missing: List[str] = []
    if spot is None or spot <= 0:
        missing.append("spot")
    if current_mid is None or current_mid < 0:
        missing.append("current_mid")
    if _mark_float(mark, "pnl_pct") is None:
        missing.append("pnl_pct")
    if _mark_float(mark, "margin_buffer") is None:
        missing.append("margin_buffer")
    return missing


def _mark_error(mark: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    errors = {
        key: str(mark.get(key))
        for key in ("quote_error", "chain_error", "mark_error", "error")
        if mark.get(key)
    }
    return errors or None


def _mark_float(mark: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in mark:
            value = _to_float(mark.get(key))
            if value is not None:
                return value
    return None


def _holding_days(position: Dict[str, Any], open_at: Optional[datetime], now_dt: datetime) -> Optional[int]:
    explicit = _to_int(position.get("holding_days"))
    if explicit is not None:
        return explicit
    if open_at is None:
        return None
    return max(0, (now_dt.date() - open_at.date()).days)


def _dte(expiration: Optional[date], today: date) -> Optional[int]:
    if expiration is None:
        return None
    return (expiration - today).days


def _coerce_datetime(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    raise TypeError("now must be a datetime or None")


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


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return default
    return result


def _round_or_none(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)
