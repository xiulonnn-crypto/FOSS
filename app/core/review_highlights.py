"""Rule-based highlights and lowlights for a single closed position review."""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.close_reason_norm import canonical_close_reason_code
from app.core.review_analytics import position_holding_days, _safe_float


def _capture_ratio(pos: Dict[str, Any]) -> float | None:
    pnl = _safe_float(pos.get("realized_pnl"))
    premium = _safe_float(pos.get("open_premium"))
    contracts = int(_safe_float(pos.get("contracts")) or 1)
    if pnl is None or premium is None or premium <= 0 or contracts <= 0:
        return None
    max_profit = premium * 100 * contracts
    if max_profit <= 0:
        return None
    return pnl / max_profit


def build_position_highlights(
    position: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, List[Dict[str, str]]]:
    """Conservative, transparent rules — at most 3 highlights and 3 lowlights."""
    snap = snapshot or {}
    highlights: List[Dict[str, str]] = []
    lowlights: List[Dict[str, str]] = []

    reason_raw = position.get("close_reason")
    reason = canonical_close_reason_code(reason_raw)

    if reason in ("take_profit", "take_profit_fast"):
        highlights.append({"text": "止盈成功，目标达成"})
    if reason == "expired":
        highlights.append({"text": "到期 OTM 自然收益"})

    capture = _capture_ratio(position)
    if capture is not None and capture > 0.7:
        highlights.append({"text": "收益捕获率高于 70%"})

    if reason == "defend":
        lowlights.append({"text": "触发防守信号"})
    if reason == "assigned":
        lowlights.append({"text": "期权被行权"})

    delta = _safe_float(snap.get("delta"))
    if delta is not None and abs(delta) > 0.20:
        lowlights.append({"text": f"入场 Delta 偏高（{abs(delta):.2f}）"})

    grade = str(snap.get("quality_grade") or "").upper()
    if grade in ("B", "C") or not snap.get("quality_grade"):
        label = grade if grade in ("B", "C") else "未评级"
        lowlights.append({"text": f"入场时数据质量为 {label} 级"})

    holding = position_holding_days(position)
    if holding is not None and holding < 7 and reason not in ("take_profit", "take_profit_fast"):
        lowlights.append({"text": f"持仓天数极短（{holding:.0f}天），Theta 收取有限"})

    # Deduplicate while preserving order
    def _dedupe(items: List[Dict[str, str]], limit: int) -> List[Dict[str, str]]:
        seen: set[str] = set()
        out: List[Dict[str, str]] = []
        for item in items:
            t = item["text"]
            if t in seen:
                continue
            seen.add(t)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    return {
        "highlights": _dedupe(highlights, 3),
        "lowlights": _dedupe(lowlights, 3),
    }
