from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

from app.core.exit_signal import build_exit_signal
from app.core.position_mark import mark_short_put_position
from app.core.radar_snapshot import append_radar_snapshot_from_mark
from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)

SERVER_NOTIFY_URL = "http://127.0.0.1:7000/api/internal/notify"

# 与 app/core/strategy.evaluate_exit_signals 返回的 signal id 一致（用户可见文案）
_RADAR_SIGNAL_TITLE_ZH = {
    "take_profit_50": "止盈约 50% 最大盈利",
    "take_profit_75": "止盈约 75% 最大盈利",
    "time_14d": "剩余天数提醒（≤14 天）",
    "time_7d": "剩余天数预警（≤7 天）",
    "danger_3pct": "股价贴近行权价（≤3% 缓冲）",
    "delta_breach": "Delta 突破阈值",
    "take_profit_fast": "快速止盈",
    "loss_breach": "浮亏扩大需防守",
    "HOLD_TO_EXPIRY": "可考虑持有到期",
    "DEFEND": "持仓防守建议",
    "TIME_EXIT": "时间退出建议",
    "TAKE_PROFIT": "止盈建议",
    "ACCELERATE_TAKE_PROFIT": "加速止盈建议",
    "EXPIRED": "持仓已过期",
}


def _radar_event_title(symbol: str, sig: str) -> str:
    label = _RADAR_SIGNAL_TITLE_ZH.get(sig)
    if not label:
        label = sig.replace("_", " ")
    return f"{symbol} · {label}"


def _event_signal_key(signal: dict) -> str:
    return str(signal.get("suggested_close_reason") or signal.get("action") or "UNKNOWN")


def _event_level(signal: dict) -> str:
    action = signal.get("action")
    reason = signal.get("suggested_close_reason")
    severity = signal.get("severity")
    if action == "HOLD_TO_EXPIRY":
        return "info"
    if action in {"DEFEND", "EXPIRED"} or reason == "time_7d" or severity == "danger":
        return "danger"
    return "warn"


def _is_actionable(signal: dict) -> bool:
    return signal.get("action") in {
        "TAKE_PROFIT",
        "ACCELERATE_TAKE_PROFIT",
        "TIME_EXIT",
        "DEFEND",
        "HOLD_TO_EXPIRY",
        "EXPIRED",
    }


def _notify_server(event_id: int) -> None:
    """Best-effort HTTP POST to server; failure is non-fatal."""
    try:
        data = json.dumps({"id": event_id}).encode()
        req = urllib.request.Request(
            SERVER_NOTIFY_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            pass
    except Exception as exc:
        log.debug("radar: notify server failed (non-fatal): %s", exc)


def run_radar(
    repo: Repo,
    provider: MarketDataProvider,
    risk_free_rate: float = 0.045,
) -> None:
    """Evaluate all OPEN positions, write radar snapshots, emit exit signals."""
    settings = repo.get_settings()
    positions = repo.list_positions(state="OPEN")

    for pos in positions:
        symbol = pos["symbol"]
        position_id = pos["id"]
        strike = float(pos.get("strike", 0) or 0)

        mark = mark_short_put_position(pos, provider, risk_free_rate)
        if mark.get("quote_error"):
            log.warning("radar: quote(%s) failed: %s", symbol, mark["quote_error"])

        exit_signal = build_exit_signal(pos, mark, settings)
        signals = exit_signal.get("legacy_signals") or []

        taken_at = datetime.now(timezone.utc).isoformat()
        radar_snapshot_id = append_radar_snapshot_from_mark(
            repo,
            position_id,
            taken_at,
            mark,
            signals=signals,
        )
        if radar_snapshot_id is not None:
            exit_signal.setdefault("source", {})["radar_snapshot_id"] = radar_snapshot_id

        event_seen = repo.exit_signal_event_exists(
            position_id,
            str(exit_signal.get("action") or "UNKNOWN"),
            exit_signal.get("suggested_close_reason"),
            str(exit_signal.get("severity") or "info"),
        )
        exit_signal_id = repo.insert_exit_signal(exit_signal, radar_snapshot_id=radar_snapshot_id)
        exit_signal["id"] = exit_signal_id
        exit_signal["exit_signal_id"] = exit_signal_id

        if _is_actionable(exit_signal) and not event_seen:
            sig = _event_signal_key(exit_signal)
            metrics = exit_signal.get("metrics") or {}
            eid = repo.insert_event(
                level=_event_level(exit_signal),
                category="radar",
                title=_radar_event_title(symbol, sig),
                payload={
                    "position_id": position_id,
                    "exit_signal_id": exit_signal_id,
                    "signal_type": sig,
                    "exit_action": exit_signal.get("action"),
                    "suggested_close_reason": exit_signal.get("suggested_close_reason"),
                    "severity": exit_signal.get("severity"),
                    "urgency_score": exit_signal.get("urgency_score"),
                    "summary": exit_signal.get("summary"),
                    "pnl_pct": metrics.get("pnl_pct"),
                    "spot": metrics.get("spot"),
                    "strike": strike,
                },
            )
            _notify_server(eid)

    log.info("radar: processed %d OPEN positions", len(positions))
