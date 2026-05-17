"""Rule-based setting suggestions from review condition slices."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _bucket_index(slices: Dict[str, List[Dict[str, Any]]], dimension: str) -> Dict[str, Dict[str, Any]]:
    buckets = slices.get(dimension) or []
    return {b["bucket"]: b for b in buckets if b.get("bucket")}


def _bucket_worse(candidate: Optional[Dict[str, Any]], baseline: Optional[Dict[str, Any]], min_sample: int) -> bool:
    if not candidate or not baseline:
        return False
    if (candidate.get("count") or 0) < min_sample or (baseline.get("count") or 0) < min_sample:
        return False
    if candidate.get("low_sample") or baseline.get("low_sample"):
        return False
    cand_win = candidate.get("win_rate")
    base_win = baseline.get("win_rate")
    if cand_win is not None and base_win is not None and cand_win + 0.10 < base_win:
        return True
    cand_roe = candidate.get("avg_roe")
    base_roe = baseline.get("avg_roe")
    if cand_roe is not None and base_roe is not None and cand_roe + 0.002 < base_roe:
        return True
    cand_mae = candidate.get("avg_maee")
    base_mae = baseline.get("avg_maee")
    if cand_mae is not None and base_mae is not None and cand_mae + 0.03 < base_mae:
        return True
    return False


def _nested_get(settings: Dict[str, Any], key: str) -> Any:
    parts = key.split(".")
    cur: Any = settings
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def build_suggestions(
    slices: Dict[str, List[Dict[str, Any]]],
    performance_review: Dict[str, Any],
    settings: Dict[str, Any],
    min_sample: int,
) -> List[Dict[str, Any]]:
    """Produce actionable suggestions with apply metadata."""
    suggestions: List[Dict[str, Any]] = []
    filters = settings.get("filters") or {}

    delta = _bucket_index(slices, "delta_abs")
    if _bucket_worse(delta.get("gt_0_20"), delta.get("target_0_10_0_20") or delta.get("le_0_10"), min_sample):
        current = float(filters.get("delta_max", 0.2))
        proposed = min(current, 0.20)
        if proposed >= current - 1e-9:
            proposed = max(0.10, round(current - 0.05, 4))
        if proposed < current - 1e-9:
            suggestions.append({
                "id": "delta-max-tighten",
                "severity": "warn",
                "title": "高 Delta 入场表现偏弱",
                "rationale": "历史高 |Delta| 合约的胜率、回报或浮亏弱于更低 Delta 桶，可考虑收紧 Delta 上限。",
                "detail": "历史高 |Delta| 合约的胜率、回报或浮亏弱于更低 Delta 桶，可考虑收紧 Delta 上限。",
                "setting_key": "filters.delta_max",
                "changes": [{
                    "key": "filters.delta_max",
                    "current": current,
                    "proposed": proposed,
                    "kind": "number",
                }],
                "sample_size": int(delta.get("gt_0_20", {}).get("count") or 0),
                "confidence": "medium" if (delta.get("gt_0_20", {}).get("count") or 0) >= min_sample * 2 else "low",
                "dimension": "delta_abs",
                "bucket": "gt_0_20",
            })

    margin = _bucket_index(slices, "margin_buffer")
    if _bucket_worse(margin.get("lt_0_08"), margin.get("gte_0_15") or margin.get("d0_08_0_15"), min_sample):
        current = float(filters.get("margin_buffer_min", 0.08))
        proposed = max(current, 0.10)
        if proposed > current:
            suggestions.append({
                "id": "margin-buffer-raise",
                "severity": "warn",
                "title": "安全垫过低的交易拖累结果",
                "rationale": "低安全垫桶的收益或回撤表现更弱，可考虑提高最低安全垫要求。",
                "detail": "低安全垫桶的收益或回撤表现更弱，可考虑提高最低安全垫要求。",
                "setting_key": "filters.margin_buffer_min",
                "changes": [{
                    "key": "filters.margin_buffer_min",
                    "current": current,
                    "proposed": proposed,
                    "kind": "number",
                }],
                "sample_size": int(margin.get("lt_0_08", {}).get("count") or 0),
                "confidence": "medium",
                "dimension": "margin_buffer",
                "bucket": "lt_0_08",
            })

    signal = _bucket_index(slices, "entry_signal_status")
    if _bucket_worse(signal.get("WAIT") or signal.get("REJECT"), signal.get("OPENABLE"), min_sample):
        current = bool((settings.get("entry_signal") or {}).get("openable_only", False))
        if not current:
            suggestions.append({
                "id": "openable-only-enable",
                "severity": "info",
                "title": "等待类信号不宜直接开仓",
                "rationale": "WAIT/REJECT 样本弱于可开仓样本时，可只把「可开仓」合约送入筛选结果。",
                "detail": "WAIT/REJECT 样本弱于可开仓样本时，可只把「可开仓」合约送入筛选结果。",
                "setting_key": "entry_signal.openable_only",
                "changes": [{
                    "key": "entry_signal.openable_only",
                    "current": current,
                    "proposed": True,
                    "kind": "boolean",
                }],
                "sample_size": int((signal.get("WAIT") or {}).get("count") or 0)
                + int((signal.get("REJECT") or {}).get("count") or 0),
                "confidence": "low",
                "dimension": "entry_signal_status",
                "bucket": "WAIT",
            })

    quality = _bucket_index(slices, "quality_grade")
    if _bucket_worse(quality.get("B") or quality.get("UNKNOWN"), quality.get("A"), min_sample):
        suggestions.append({
            "id": "quality-grade-caution",
            "severity": "info",
            "title": "非 A 级数据入场需更谨慎",
            "rationale": "B/未评级样本弱于 A 级时，优先放入观察池，等待数据质量改善再登记开仓。",
            "detail": "B/未评级样本弱于 A 级时，优先放入观察池，等待数据质量改善再登记开仓。",
            "setting_key": None,
            "changes": [],
            "sample_size": int((quality.get("B") or {}).get("count") or 0),
            "confidence": "low",
            "dimension": "quality_grade",
            "bucket": "B",
        })

    return suggestions[:4]


def apply_suggestion_changes(
    settings: Dict[str, Any],
    suggestions: List[Dict[str, Any]],
    suggestion_ids: List[str],
) -> Dict[str, Any]:
    """Deep-merge proposed changes for selected suggestion ids. Returns partial patch."""
    id_set = set(suggestion_ids)
    patch: Dict[str, Any] = {}
    for sug in suggestions:
        if sug.get("id") not in id_set:
            continue
        for change in sug.get("changes") or []:
            key = change.get("key")
            if not key:
                continue
            parts = key.split(".")
            target = patch
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = change.get("proposed")
    return patch
