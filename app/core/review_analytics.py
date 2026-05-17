"""Review analytics: condition slices, performance review, score–PnL correlation."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.close_reason_norm import (
    canonical_close_reason_code,
    close_reason_bucket,
    pool_source_from_snapshot,
)
from app.core.pnl_excursion import compute_annualized_return
from app.core.time_et import parse_instant_utc

SliceBucketFn = Callable[[Dict[str, Any], Dict[str, Any]], Tuple[str, str, int]]


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _ratio_value(value: Any) -> Optional[float]:
    out = _safe_float(value)
    if out is None:
        return None
    return out / 100.0 if abs(out) > 1.5 else out


def position_roe(pos: Dict[str, Any]) -> Optional[float]:
    pnl = _safe_float(pos.get("realized_pnl"))
    strike = _safe_float(pos.get("strike"))
    contracts = int(_safe_float(pos.get("contracts")) or 1)
    if pnl is None or strike is None or strike <= 0 or contracts <= 0:
        return None
    margin = strike * 100 * contracts
    return pnl / margin if margin > 0 else None


def position_holding_days(pos: Dict[str, Any]) -> Optional[float]:
    open_dt = parse_instant_utc(pos.get("open_at"))
    close_dt = parse_instant_utc(pos.get("close_at"))
    if not open_dt or not close_dt:
        return None
    return max(0.0, (close_dt - open_dt).total_seconds()) / 86400.0


def parse_review_filters(
    args: Any,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    review_cfg = settings.get("review") or {}
    min_sample = args.get("min_sample")
    try:
        min_sample_int = int(min_sample) if min_sample is not None else int(review_cfg.get("min_sample_size", 5))
    except (TypeError, ValueError):
        min_sample_int = 5
    min_sample_int = max(1, min_sample_int)

    since_raw = (args.get("since") or "").strip()
    until_raw = (args.get("until") or "").strip()
    since_d = date.fromisoformat(since_raw) if since_raw else None
    until_d = date.fromisoformat(until_raw) if until_raw else None

    symbols_raw = (args.get("symbols") or "").strip()
    symbols = {s.strip().upper() for s in symbols_raw.split(",") if s.strip()} if symbols_raw else None

    pool = (args.get("pool") or "all").strip().lower()
    if pool not in ("all", "main", "watch", "manual"):
        pool = "all"

    include_deleted = str(args.get("include_deleted", "")).lower() in ("1", "true", "yes")

    return {
        "since": since_d,
        "until": until_d,
        "symbols": symbols,
        "pool": pool,
        "include_deleted": include_deleted,
        "min_sample": min_sample_int,
        "score_buckets": list(review_cfg.get("score_correlation_buckets") or [60, 80]),
    }


def filter_closed_positions(
    positions: List[Dict[str, Any]],
    snapshots: Dict[int, Dict[str, Any]],
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    since_d = filters.get("since")
    until_d = filters.get("until")
    symbols = filters.get("symbols")
    pool = filters.get("pool", "all")

    out: List[Dict[str, Any]] = []
    for pos in positions:
        if symbols and str(pos.get("symbol") or "").upper() not in symbols:
            continue
        close_raw = pos.get("close_at")
        close_dt = parse_instant_utc(close_raw)
        if since_d or until_d:
            if not close_dt:
                continue
            close_day = close_dt.date()
            if since_d and close_day < since_d:
                continue
            if until_d and close_day > until_d:
                continue
        if pool != "all":
            pid = pos.get("id")
            snap = snapshots.get(int(pid)) if pid is not None else {}
            src = pool_source_from_snapshot(snap or {})
            if src != pool:
                continue
        out.append(pos)
    return out


def _entry_signal_status(snapshot: Dict[str, Any]) -> str:
    raw = snapshot.get("entry_signal_status")
    if raw is None and isinstance(snapshot.get("entry_signal"), dict):
        raw = snapshot["entry_signal"].get("status")
    status = str(raw or "UNKNOWN").upper()
    return status if status in {"OPENABLE", "WAIT", "REJECT", "EXPIRED"} else "UNKNOWN"


def _bucket_quality(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    grade = str(snapshot.get("quality_grade") or "unknown").upper()
    labels = {"A": "A 可决策", "B": "B 可观察", "C": "C 数据不足", "UNKNOWN": "未评级"}
    order = {"A": 0, "B": 1, "C": 2, "UNKNOWN": 3}
    if grade not in labels:
        grade = "UNKNOWN"
    return grade, labels[grade], order[grade]


def _bucket_entry_signal(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    status = _entry_signal_status(snapshot)
    labels = {
        "OPENABLE": "可开仓",
        "WAIT": "等待",
        "REJECT": "拒绝",
        "EXPIRED": "已过期",
        "UNKNOWN": "未知",
    }
    order = {"OPENABLE": 0, "WAIT": 1, "REJECT": 2, "EXPIRED": 3, "UNKNOWN": 4}
    return status, labels[status], order[status]


def _bucket_delta(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    delta = _safe_float(snapshot.get("delta"))
    if delta is None:
        return "unknown", "未知 Delta", 3
    abs_delta = abs(delta)
    if abs_delta <= 0.10:
        return "le_0_10", "|Delta| ≤ 0.10", 0
    if abs_delta <= 0.20:
        return "target_0_10_0_20", "0.10 < |Delta| ≤ 0.20", 1
    return "gt_0_20", "|Delta| > 0.20", 2


def _bucket_dte(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    dte = _safe_float(snapshot.get("dte"))
    if dte is None:
        return "unknown", "未知 DTE", 3
    if dte <= 21:
        return "le_21", "≤ 21 天", 0
    if dte <= 45:
        return "d22_45", "22-45 天", 1
    return "gt_45", "> 45 天", 2


def _bucket_iv_rank(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    iv_rank = _safe_float(snapshot.get("iv_rank"))
    if iv_rank is None:
        return "unknown", "未知 IV Rank", 3
    rank_pct = iv_rank * 100 if 0 <= iv_rank <= 1 else iv_rank
    if rank_pct < 30:
        return "lt_30", "< 30", 0
    if rank_pct <= 70:
        return "d30_70", "30-70", 1
    return "gt_70", "> 70", 2


def _bucket_margin_buffer(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    buffer = _ratio_value(snapshot.get("margin_buffer"))
    if buffer is None:
        return "unknown", "未知安全垫", 3
    if buffer < 0.08:
        return "lt_0_08", "< 8%", 0
    if buffer < 0.15:
        return "d0_08_0_15", "8%-15%", 1
    return "gte_0_15", "≥ 15%", 2


def _bucket_rsi(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    rsi = _safe_float(snapshot.get("rsi_12"))
    if rsi is None:
        rsi = _safe_float(snapshot.get("rsi_6"))
    if rsi is None:
        return "unknown", "未知 RSI", 3
    if rsi < 30:
        return "oversold", "超跌 (<30)", 0
    if rsi > 70:
        return "overbought", "过热 (>70)", 1
    return "neutral", "中性 (30-70)", 2


def _bucket_close_reason(_snapshot: Dict[str, Any], pos: Dict[str, Any]) -> Tuple[str, str, int]:
    return close_reason_bucket(pos.get("close_reason"))


def _bucket_pool_source(snapshot: Dict[str, Any], _pos: Dict[str, Any]) -> Tuple[str, str, int]:
    src = pool_source_from_snapshot(snapshot)
    labels = {"main": "合约池", "watch": "观察池", "manual": "手动录入", "unknown": "未知"}
    order = {"main": 0, "watch": 1, "manual": 2, "unknown": 3}
    key = src if src in labels else "unknown"
    return key, labels[key], order[key]


_SLICE_DIMENSIONS: Tuple[Tuple[str, str, SliceBucketFn], ...] = (
    ("quality_grade", "数据质量", _bucket_quality),
    ("entry_signal_status", "开仓信号", _bucket_entry_signal),
    ("delta_abs", "Delta", _bucket_delta),
    ("dte", "DTE", _bucket_dte),
    ("iv_rank", "IV Rank", _bucket_iv_rank),
    ("margin_buffer", "安全垫", _bucket_margin_buffer),
    ("rsi", "RSI", _bucket_rsi),
    ("close_reason", "平仓原因", _bucket_close_reason),
    ("pool_source", "来源池", _bucket_pool_source),
)


def _new_bucket_stats(bucket: str, label: str, order: int) -> Dict[str, Any]:
    return {
        "bucket": bucket,
        "label": label,
        "sort_order": order,
        "count": 0,
        "_wins": 0,
        "_roe_sum": 0.0,
        "_roe_count": 0,
        "_holding_sum": 0.0,
        "_holding_count": 0,
        "_mae_sum": 0.0,
        "_mae_count": 0,
        "_mfe_sum": 0.0,
        "_mfe_count": 0,
        "_score_sum": 0.0,
        "_score_count": 0,
    }


def _finalize_bucket(stats: Dict[str, Any], min_sample: int) -> Dict[str, Any]:
    count = int(stats["count"] or 0)
    roe_count = int(stats["_roe_count"] or 0)
    holding_count = int(stats["_holding_count"] or 0)
    mae_count = int(stats["_mae_count"] or 0)
    mfe_count = int(stats["_mfe_count"] or 0)
    return {
        "bucket": stats["bucket"],
        "label": stats["label"],
        "count": count,
        "low_sample": count < min_sample,
        "win_rate": stats["_wins"] / count if count else None,
        "avg_roe": stats["_roe_sum"] / roe_count if roe_count else None,
        "avg_holding_days": stats["_holding_sum"] / holding_count if holding_count else None,
        "avg_maee": stats["_mae_sum"] / mae_count if mae_count else None,
        "avg_mfe": stats["_mfe_sum"] / mfe_count if mfe_count else None,
        "avg_score": stats["_score_sum"] / int(stats["_score_count"] or 0)
        if stats["_score_count"]
        else None,
    }


def build_position_dimension_summary(
    position: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Nine-dimension bucket labels for a single closed position."""
    snap = snapshot or {}
    out: List[Dict[str, Any]] = []
    for dim_key, dim_label, bucket_fn in _SLICE_DIMENSIONS:
        bucket, bucket_label, _order = bucket_fn(snap, position)
        out.append({
            "dimension": dim_key,
            "label": dim_label,
            "bucket": bucket,
            "bucket_label": bucket_label,
        })
    return out


def build_condition_slices(
    records: List[Dict[str, Any]],
    min_sample: int,
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Return (slices dict, condition_slices list)."""
    slices_dict: Dict[str, List[Dict[str, Any]]] = {}
    condition_list: List[Dict[str, Any]] = []

    for dim_key, dim_label, bucket_fn in _SLICE_DIMENSIONS:
        buckets: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            bucket, bucket_label, order = bucket_fn(rec["snapshot"], rec["position"])
            stats = buckets.setdefault(bucket, _new_bucket_stats(bucket, bucket_label, order))
            stats["count"] += 1
            if rec["win"]:
                stats["_wins"] += 1
            if rec["roe"] is not None:
                stats["_roe_sum"] += rec["roe"]
                stats["_roe_count"] += 1
            if rec["holding_days"] is not None:
                stats["_holding_sum"] += rec["holding_days"]
                stats["_holding_count"] += 1
            if rec["mae"] is not None:
                stats["_mae_sum"] += rec["mae"]
                stats["_mae_count"] += 1
            if rec["mfe"] is not None:
                stats["_mfe_sum"] += rec["mfe"]
                stats["_mfe_count"] += 1
            score = _safe_float(rec["snapshot"].get("score"))
            if score is not None:
                stats["_score_sum"] += score
                stats["_score_count"] += 1

        finalized = [
            _finalize_bucket(stats, min_sample)
            for stats in sorted(buckets.values(), key=lambda s: (s["sort_order"], s["bucket"]))
        ]
        slices_dict[dim_key] = finalized
        condition_list.append({
            "factor": dim_key,
            "dimension": dim_key,
            "label": dim_label,
            "buckets": finalized,
        })

    return slices_dict, condition_list


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None

    def _rank(values: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(xs)
    ry = _rank(ys)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    denom = n * (n * n - 1)
    if denom == 0:
        return None
    return 1.0 - (6.0 * d2) / denom


def build_score_pnl_correlation(
    records: List[Dict[str, Any]],
    score_bucket_edges: List[int],
) -> Dict[str, Any]:
    pairs = [
        (float(rec["snapshot"].get("score")), rec["roe"])
        for rec in records
        if _safe_float(rec["snapshot"].get("score")) is not None and rec["roe"] is not None
    ]
    if not pairs:
        return {"spearman": None, "score_buckets": [], "pair_count": 0}

    scores, roes = zip(*pairs)
    spearman = _spearman(list(scores), list(roes))

    edges = sorted({int(e) for e in score_bucket_edges if e is not None})
    if not edges:
        edges = [60, 80]

    def _bucket_label(score: float) -> str:
        if score < edges[0]:
            return f"<{edges[0]}"
        for i in range(len(edges) - 1):
            if edges[i] <= score < edges[i + 1]:
                return f"{edges[i]}-{edges[i + 1]}"
        return f"≥{edges[-1]}"

    bucket_map: Dict[str, Dict[str, Any]] = {}
    for score, roe in pairs:
        label = _bucket_label(score)
        b = bucket_map.setdefault(label, {"bucket": label, "n": 0, "_wins": 0, "_roe_sum": 0.0})
        b["n"] += 1
        if roe > 0:
            b["_wins"] += 1
        b["_roe_sum"] += roe

    score_buckets = []
    for label in sorted(bucket_map.keys(), key=lambda x: (x[0] != "<", x)):
        b = bucket_map[label]
        n = b["n"]
        score_buckets.append({
            "bucket": label,
            "n": n,
            "win_rate": b["_wins"] / n if n else None,
            "avg_roe": b["_roe_sum"] / n if n else None,
        })

    return {"spearman": spearman, "score_buckets": score_buckets, "pair_count": len(pairs)}


def build_performance_review(
    slices: Dict[str, List[Dict[str, Any]]],
    min_sample: int,
    overall_avg_roe: Optional[float],
) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    low_sample_warnings: List[Dict[str, Any]] = []

    for dim_key, buckets in slices.items():
        for b in buckets:
            entry = {
                "dimension": dim_key,
                "bucket": b.get("bucket"),
                "label": b.get("label"),
                "count": b.get("count") or 0,
                "win_rate": b.get("win_rate"),
                "avg_roe": b.get("avg_roe"),
                "avg_maee": b.get("avg_maee"),
            }
            if entry["count"] < min_sample:
                low_sample_warnings.append({**entry, "reason": "样本不足"})
                continue
            if entry["avg_roe"] is not None:
                candidates.append(entry)

    best_combo = sorted(
        [c for c in candidates if c.get("avg_roe") is not None],
        key=lambda c: c["avg_roe"],
        reverse=True,
    )[:3]

    worst_drawdown_combo = sorted(
        [c for c in candidates if c.get("avg_maee") is not None],
        key=lambda c: c["avg_maee"],
    )[:3]

    high_winrate_low_return = []
    if overall_avg_roe is not None:
        for c in candidates:
            wr = c.get("win_rate")
            roe = c.get("avg_roe")
            if wr is not None and roe is not None and wr >= 0.6 and roe < overall_avg_roe - 0.002:
                high_winrate_low_return.append(c)
        high_winrate_low_return = high_winrate_low_return[:3]

    return {
        "best_combo": best_combo,
        "worst_drawdown_combo": worst_drawdown_combo,
        "high_winrate_low_return": high_winrate_low_return,
        "low_sample_warnings": low_sample_warnings[:12],
    }


def build_review_records(
    positions: List[Dict[str, Any]],
    snapshots: Dict[int, Dict[str, Any]],
    excursions: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for pos in positions:
        pid = pos.get("id")
        if pid is None:
            continue
        pid_int = int(pid)
        excursion = excursions.get(pid_int) or {}
        records.append({
            "position": pos,
            "snapshot": snapshots.get(pid_int, {}),
            "win": (_safe_float(pos.get("realized_pnl")) or 0.0) > 0,
            "roe": position_roe(pos),
            "holding_days": position_holding_days(pos),
            "mae": _safe_float(excursion.get("mae")),
            "mfe": _safe_float(excursion.get("mfe")),
        })
    return records


def compute_annualized_returns(records: List[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for rec in records:
        roe = rec.get("roe")
        days = rec.get("holding_days")
        if roe is None or days is None:
            continue
        ann = compute_annualized_return(roe, days)
        if ann is not None:
            out.append(ann)
    return out
