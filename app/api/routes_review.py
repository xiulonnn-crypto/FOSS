from __future__ import annotations

import csv
import io
import math
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify, request

from app.core.entry_rehistory import recalculate_closed_position_insights
from app.core.review_analytics import (
    build_condition_slices,
    build_performance_review,
    build_review_records,
    build_score_pnl_correlation,
    compute_annualized_returns,
    filter_closed_positions,
    parse_review_filters,
    position_holding_days,
    position_roe,
)
from app.core.review_suggestions import (
    apply_suggestion_changes,
    build_suggestions,
)
from app.core.open_snapshot import build_open_snapshot_dict
from app.core.pnl_excursion import relative_mae_mfe_from_pnls_chronologic
from app.core.pnl_excursion_intraday import enrich_closed_position_intraday_bs
from app.core.time_et import APP_TZ, parse_instant_utc
from app.db.repo import Repo

bp_review = Blueprint("review", __name__, url_prefix="/api/review")

_CLOSED_POSITION_STATES = frozenset({"CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED"})


_INTRADAY_BS_VALID_INTERVALS = frozenset({"1d_hl", "hold_window_hl"})


def _intraday_bs_needs_daily_hl_migration(open_snapshot: Any) -> bool:
    """True when stored intraday_bs predates daily/window HL → BS refactor."""
    if not isinstance(open_snapshot, dict):
        return False
    bs = open_snapshot.get("intraday_bs")
    if not isinstance(bs, dict):
        return False
    if bs.get("model") != "daily_hl_bs_eod_iv":
        return True
    interval = bs.get("interval")
    if interval not in _INTRADAY_BS_VALID_INTERVALS:
        return True
    # Same ET calendar-day hold must use window H/L when available.
    hw = bs.get("hold_window")
    same_et_day = (
        isinstance(hw, dict)
        and hw.get("open_date_et")
        and hw.get("open_date_et") == hw.get("close_date_et")
    )
    if (
        same_et_day
        and interval == "1d_hl"
        and not bs.get("hold_window_fallback")
    ):
        return True
    return False


def _get_closed_positions(repo: Repo, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Return all non-OPEN positions for review calculations."""
    all_pos = repo.list_positions()
    rows = [p for p in all_pos if p.get("state") in _CLOSED_POSITION_STATES]
    if not include_deleted:
        rows = [p for p in rows if p.get("state") != "DELETED"]
    return rows


def _merge_open_snapshot_refresh(repo: Repo, position_id: int, pos: Dict[str, Any]) -> Dict[str, Any]:
    built = build_open_snapshot_dict(repo, pos, None)
    prev = repo.get_open_snapshot(position_id) or {}
    out = dict(prev)
    for k, v in built.items():
        if v is not None:
            out[k] = v
    return out


def _sorted_closed_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort by close time descending (fallback open_at)."""

    def _sort_key(p: Dict[str, Any]) -> str:
        return str(p.get("close_at") or p.get("open_at") or "")

    return sorted(rows, key=_sort_key, reverse=True)


def _sorted_all_positions(repo: Repo) -> List[Dict[str, Any]]:
    """All positions (OPEN + closed), excluding soft-deleted review rows."""
    rows = [p for p in repo.list_positions() if p.get("state") != "DELETED"]
    return _sorted_closed_positions(rows)


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
    # Some historical snapshots store percentages as 8.0 while current rows use 0.08.
    return out / 100.0 if abs(out) > 1.5 else out


def _position_roe(pos: Dict[str, Any]) -> Optional[float]:
    pnl = _safe_float(pos.get("realized_pnl"))
    strike = _safe_float(pos.get("strike"))
    contracts = int(_safe_float(pos.get("contracts")) or 1)
    if pnl is None or strike is None or strike <= 0 or contracts <= 0:
        return None
    margin = strike * 100 * contracts
    return pnl / margin if margin > 0 else None


def _position_holding_days(pos: Dict[str, Any]) -> Optional[float]:
    open_dt = parse_instant_utc(pos.get("open_at"))
    close_dt = parse_instant_utc(pos.get("close_at"))
    if not open_dt or not close_dt:
        return None
    seconds = max(0.0, (close_dt - open_dt).total_seconds())
    return seconds / 86400.0


def _load_open_snapshots(repo: Optional[Repo], position_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if repo is None:
        return {}
    snapshots: Dict[int, Dict[str, Any]] = {}
    for pid in position_ids:
        try:
            snap = repo.get_open_snapshot(pid)
        except AttributeError:
            snap = None
        snapshots[pid] = snap if isinstance(snap, dict) else {}
    return snapshots


def _load_excursions(repo: Optional[Repo], position_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if repo is None or not position_ids:
        return {}
    try:
        radar_data = repo.get_mae_mfe_for_positions(position_ids)
        intraday_data = repo.get_intraday_bs_mae_mfe_for_positions(position_ids)
    except AttributeError:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for pid in position_ids:
        src = intraday_data.get(pid) or radar_data.get(pid)
        if src:
            out[pid] = src
    return out


def _entry_signal_status(snapshot: Dict[str, Any]) -> str:
    raw = snapshot.get("entry_signal_status")
    if raw is None and isinstance(snapshot.get("entry_signal"), dict):
        raw = snapshot["entry_signal"].get("status")
    status = str(raw or "UNKNOWN").upper()
    return status if status in {"OPENABLE", "WAIT", "REJECT", "EXPIRED"} else "UNKNOWN"


def _bucket_quality(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
    grade = str(snapshot.get("quality_grade") or "unknown").upper()
    labels = {
        "A": "A 可决策",
        "B": "B 可观察",
        "C": "C 数据不足",
        "UNKNOWN": "未评级",
    }
    order = {"A": 0, "B": 1, "C": 2, "UNKNOWN": 3}
    if grade not in labels:
        grade = "UNKNOWN"
    return grade, labels[grade], order[grade]


def _bucket_entry_signal(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
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


def _bucket_delta(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
    delta = _safe_float(snapshot.get("delta"))
    if delta is None:
        return "unknown", "未知 Delta", 3
    abs_delta = abs(delta)
    if abs_delta <= 0.10:
        return "le_0_10", "|Delta| ≤ 0.10", 0
    if abs_delta <= 0.20:
        return "target_0_10_0_20", "0.10 < |Delta| ≤ 0.20", 1
    return "gt_0_20", "|Delta| > 0.20", 2


def _bucket_dte(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
    dte = _safe_float(snapshot.get("dte"))
    if dte is None:
        return "unknown", "未知 DTE", 3
    if dte <= 21:
        return "le_21", "≤ 21 天", 0
    if dte <= 45:
        return "d22_45", "22-45 天", 1
    return "gt_45", "> 45 天", 2


def _bucket_iv_rank(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
    iv_rank = _safe_float(snapshot.get("iv_rank"))
    if iv_rank is None:
        return "unknown", "未知 IV Rank", 3
    rank_pct = iv_rank * 100 if 0 <= iv_rank <= 1 else iv_rank
    if rank_pct < 30:
        return "lt_30", "< 30", 0
    if rank_pct <= 70:
        return "d30_70", "30-70", 1
    return "gt_70", "> 70", 2


def _bucket_margin_buffer(snapshot: Dict[str, Any]) -> tuple[str, str, int]:
    buffer = _ratio_value(snapshot.get("margin_buffer"))
    if buffer is None:
        return "unknown", "未知安全垫", 3
    if buffer < 0.08:
        return "lt_0_08", "< 8%", 0
    if buffer < 0.15:
        return "d0_08_0_15", "8%-15%", 1
    return "gte_0_15", "≥ 15%", 2


_FACTOR_BUCKETS = (
    ("quality_grade", "数据质量", _bucket_quality),
    ("entry_signal_status", "开仓信号", _bucket_entry_signal),
    ("delta_abs", "Delta 桶", _bucket_delta),
    ("dte", "DTE 桶", _bucket_dte),
    ("iv_rank", "IV Rank 桶", _bucket_iv_rank),
    ("margin_buffer", "安全垫桶", _bucket_margin_buffer),
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
    }


def _finalize_bucket(stats: Dict[str, Any]) -> Dict[str, Any]:
    count = int(stats["count"] or 0)
    roe_count = int(stats["_roe_count"] or 0)
    holding_count = int(stats["_holding_count"] or 0)
    mae_count = int(stats["_mae_count"] or 0)
    mfe_count = int(stats["_mfe_count"] or 0)
    return {
        "bucket": stats["bucket"],
        "label": stats["label"],
        "count": count,
        "win_rate": stats["_wins"] / count if count else None,
        "avg_roe": stats["_roe_sum"] / roe_count if roe_count else None,
        "avg_holding_days": stats["_holding_sum"] / holding_count if holding_count else None,
        "avg_maee": stats["_mae_sum"] / mae_count if mae_count else None,
        "avg_mfe": stats["_mfe_sum"] / mfe_count if mfe_count else None,
    }


def _build_factor_slices(positions: List[Dict[str, Any]], repo: Optional[Repo]) -> List[Dict[str, Any]]:
    position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
    if not position_ids:
        return []

    snapshots = _load_open_snapshots(repo, position_ids)
    excursions = _load_excursions(repo, position_ids)
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
            "roe": _position_roe(pos),
            "holding_days": _position_holding_days(pos),
            "mae": _safe_float(excursion.get("mae")),
            "mfe": _safe_float(excursion.get("mfe")),
        })

    slices: List[Dict[str, Any]] = []
    for factor, label, bucket_fn in _FACTOR_BUCKETS:
        buckets: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            bucket, bucket_label, order = bucket_fn(rec["snapshot"])
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

        finalized = [
            _finalize_bucket(stats)
            for stats in sorted(buckets.values(), key=lambda s: (s["sort_order"], s["bucket"]))
        ]
        slices.append({"factor": factor, "label": label, "buckets": finalized})
    return slices


def _bucket_index(factor_slices: List[Dict[str, Any]], factor: str) -> Dict[str, Dict[str, Any]]:
    for row in factor_slices:
        if row.get("factor") == factor:
            return {b["bucket"]: b for b in row.get("buckets", [])}
    return {}


def _bucket_worse(candidate: Optional[Dict[str, Any]], baseline: Optional[Dict[str, Any]]) -> bool:
    if not candidate or not baseline:
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


def _build_setting_suggestions(factor_slices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []

    delta = _bucket_index(factor_slices, "delta_abs")
    if _bucket_worse(delta.get("gt_0_20"), delta.get("target_0_10_0_20") or delta.get("le_0_10")):
        suggestions.append({
            "severity": "warn",
            "setting_key": "filters.delta_max",
            "title": "高 Delta 入场表现偏弱",
            "detail": "历史高 |Delta| 合约的胜率、ROE 或最大浮亏弱于低 Delta 桶，可考虑下调 Delta 上限或只保留更虚值的 Put。",
            "factor": "delta_abs",
            "bucket": "gt_0_20",
        })

    margin = _bucket_index(factor_slices, "margin_buffer")
    if _bucket_worse(margin.get("lt_0_08"), margin.get("gte_0_15") or margin.get("d0_08_0_15")):
        suggestions.append({
            "severity": "warn",
            "setting_key": "filters.margin_buffer_min",
            "title": "安全垫过低的交易拖累结果",
            "detail": "低安全垫桶的收益或回撤表现更弱，可考虑提高 margin_buffer 最低要求。",
            "factor": "margin_buffer",
            "bucket": "lt_0_08",
        })

    quality = _bucket_index(factor_slices, "quality_grade")
    if _bucket_worse(quality.get("B") or quality.get("UNKNOWN"), quality.get("A")):
        suggestions.append({
            "severity": "info",
            "setting_key": None,
            "title": "非 A 级数据入场需更谨慎",
            "detail": "B/未评级样本弱于 A 级样本时，优先把它们放入观察池，等待报价质量或信号改善再登记开仓。",
            "factor": "quality_grade",
            "bucket": "B",
        })

    signal = _bucket_index(factor_slices, "entry_signal_status")
    if _bucket_worse(signal.get("WAIT") or signal.get("REJECT"), signal.get("OPENABLE")):
        suggestions.append({
            "severity": "info",
            "setting_key": "entry_signal.openable_only",
            "title": "等待类信号不宜直接开仓",
            "detail": "WAIT/REJECT 样本弱于 OPENABLE 时，可把等待类合约留在观察池，而不是立即登记开仓。",
            "factor": "entry_signal_status",
            "bucket": "WAIT",
        })

    if not suggestions and any((b.get("count") or 0) > 0 for s in factor_slices for b in s.get("buckets", [])):
        suggestions.append({
            "severity": "info",
            "setting_key": None,
            "title": "样本继续积累",
            "detail": "当前复盘样本尚未显示稳定劣势桶，先保持参数不变，继续积累已平仓交易。",
            "factor": None,
            "bucket": None,
        })
    return suggestions[:4]


def _compute_summary(
    positions: List[Dict[str, Any]],
    repo: Optional[Repo],
    *,
    filters: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    filt = filters or {"min_sample": 5, "score_buckets": [60, 80]}
    min_sample = int(filt.get("min_sample", 5))
    settings = settings or {}

    if not positions:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_annualized_roi": None,
            "avg_roe": None,
            "avg_realized_roe": None,
            "avg_annualized_return": None,
            "total_premium": None,
            "total_realized_pnl": None,
            "by_close_reason": [],
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "avg_maee": None,
            "avg_mfe": None,
            "slices": {},
            "factor_slices": [],
            "performance_review": {
                "best_combo": [],
                "worst_drawdown_combo": [],
                "high_winrate_low_return": [],
                "low_sample_warnings": [],
            },
            "score_pnl_correlation": {"spearman": None, "score_buckets": [], "pair_count": 0},
            "setting_suggestions": [],
        }

    trade_count = len(positions)
    wins = 0
    sum_realized = 0.0
    roi_list = []
    premium_list = []
    position_ids = []

    for p in positions:
        pnl = float(p.get("realized_pnl") or 0)
        sum_realized += pnl
        if pnl > 0:
            wins += 1

        op = float(p.get("open_premium") or 0)
        contracts = int(p.get("contracts") or 1)
        premium_list.append(op * contracts * 100)

        roe = position_roe(p)
        if roe is not None:
            roi_list.append(roe)

        if p.get("id") is not None:
            position_ids.append(p["id"])

    win_rate = wins / trade_count if trade_count > 0 else None
    avg_roe = sum(roi_list) / len(roi_list) if roi_list else None
    total_premium = sum(premium_list)

    # Sharpe ratio: mean(roi) / stdev(roi)
    sharpe_ratio: Optional[float] = None
    if len(roi_list) >= 2:
        std = statistics.stdev(roi_list)
        if std != 0:
            sharpe_ratio = statistics.mean(roi_list) / std

    # Sortino ratio: mean(roi) / stdev(downside)
    sortino_ratio: Optional[float] = None
    if roi_list:
        mean_roi = statistics.mean(roi_list)
        downside = [r for r in roi_list if r < 0]
        if downside and len(downside) >= 2:
            std_down = statistics.stdev(downside)
            if std_down != 0:
                sortino_ratio = mean_roi / std_down
        elif downside and len(downside) == 1:
            # single downside — use population std (the value itself relative to 0)
            # standard practice: skip if can't compute stdev
            sortino_ratio = None

    # MAE / MFE: prefer intraday_bs (daily H/L × BS, same source as drawer) per position;
    # fall back to radar_snapshots MIN/MAX for positions without intraday_bs.
    avg_maee: Optional[float] = None
    avg_mfe: Optional[float] = None
    if repo is not None and position_ids:
        try:
            radar_data = repo.get_mae_mfe_for_positions(position_ids)
            intraday_data = repo.get_intraday_bs_mae_mfe_for_positions(position_ids)
            maes: List[float] = []
            mfes: List[float] = []
            for pid in position_ids:
                # intraday_bs takes priority (captures intraday H/L, consistent with drawer)
                src = intraday_data.get(pid) or radar_data.get(pid)
                if src is None:
                    continue
                if src.get("mae") is not None:
                    maes.append(float(src["mae"]))
                if src.get("mfe") is not None:
                    mfes.append(float(src["mfe"]))
            avg_maee = sum(maes) / len(maes) if maes else None
            avg_mfe = sum(mfes) / len(mfes) if mfes else None
        except AttributeError:
            pass

    # By close reason
    reason_map: Dict[str, Dict] = {}
    for p in positions:
        reason = p.get("close_reason") or "unknown"
        pnl = float(p.get("realized_pnl") or 0)
        strike = float(p.get("strike") or 1)
        contracts = int(p.get("contracts") or 1)
        margin = strike * 100 * contracts

        if reason not in reason_map:
            reason_map[reason] = {"count": 0, "wins": 0, "roi_sum": 0.0}
        reason_map[reason]["count"] += 1
        if pnl > 0:
            reason_map[reason]["wins"] += 1
        if margin > 0:
            reason_map[reason]["roi_sum"] += pnl / margin

    by_reason = []
    for reason, stats in sorted(reason_map.items()):
        count = stats["count"]
        by_reason.append({
            "close_reason": reason,
            "count": count,
            "win_rate": stats["wins"] / count if count > 0 else None,
            "avg_roi": stats["roi_sum"] / count if count > 0 else None,
        })

    snapshots = _load_open_snapshots(repo, position_ids) if repo else {}
    excursions = _load_excursions(repo, position_ids)
    records = build_review_records(positions, snapshots, excursions)
    slices_dict, factor_slices = build_condition_slices(records, min_sample)
    ann_list = compute_annualized_returns(records)
    avg_annualized_return = sum(ann_list) / len(ann_list) if ann_list else None
    performance_review = build_performance_review(slices_dict, min_sample, avg_roe)
    score_pnl_correlation = build_score_pnl_correlation(
        records, filt.get("score_buckets") or [60, 80]
    )
    suggestions = build_suggestions(slices_dict, performance_review, settings, min_sample)
    legacy_suggestions = [
        {
            "severity": s.get("severity", "info"),
            "setting_key": s.get("setting_key"),
            "title": s.get("title"),
            "detail": s.get("detail") or s.get("rationale"),
            "factor": s.get("dimension"),
            "bucket": s.get("bucket"),
        }
        for s in suggestions
    ]

    return {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_annualized_roi": avg_roe,
        "avg_roe": avg_roe,
        "avg_realized_roe": avg_roe,
        "avg_annualized_return": avg_annualized_return,
        "total_premium": total_premium,
        "total_realized_pnl": round(sum_realized, 2),
        "by_close_reason": by_reason,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "avg_maee": avg_maee,
        "avg_mfe": avg_mfe,
        "slices": slices_dict,
        "factor_slices": factor_slices,
        "performance_review": performance_review,
        "score_pnl_correlation": score_pnl_correlation,
        "setting_suggestions": legacy_suggestions,
        "filters_applied": {
            "since": filt.get("since").isoformat() if filt.get("since") else None,
            "until": filt.get("until").isoformat() if filt.get("until") else None,
            "symbols": sorted(filt["symbols"]) if filt.get("symbols") else None,
            "pool": filt.get("pool", "all"),
            "min_sample": min_sample,
        },
    }


def _review_summary_context(repo: Repo) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    settings = repo.get_settings()
    filters = parse_review_filters(request.args, settings)
    positions = _get_closed_positions(repo, include_deleted=filters.get("include_deleted", False))
    position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
    snapshots = _load_open_snapshots(repo, position_ids)
    positions = filter_closed_positions(positions, snapshots, filters)
    return positions, filters, settings


@bp_review.route("/summary")
def summary():
    repo: Repo = current_app.config["REPO"]
    positions, filters, settings = _review_summary_context(repo)
    body = _compute_summary(positions, repo, filters=filters, settings=settings)
    body["closed_positions"] = _sorted_closed_positions(positions)
    return jsonify(body)


@bp_review.route("/suggestions")
def review_suggestions():
    repo: Repo = current_app.config["REPO"]
    positions, filters, settings = _review_summary_context(repo)
    position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
    snapshots = _load_open_snapshots(repo, position_ids)
    excursions = _load_excursions(repo, position_ids)
    records = build_review_records(positions, snapshots, excursions)
    min_sample = int(filters.get("min_sample", 5))
    slices_dict, _ = build_condition_slices(records, min_sample)
    avg_roe = None
    roes = [r["roe"] for r in records if r.get("roe") is not None]
    if roes:
        avg_roe = sum(roes) / len(roes)
    performance_review = build_performance_review(slices_dict, min_sample, avg_roe)
    suggestions = build_suggestions(slices_dict, performance_review, settings, min_sample)
    return jsonify({"suggestions": suggestions, "filters_applied": filters})


@bp_review.route("/suggestions/apply", methods=["POST"])
def apply_review_suggestions():
    import json
    import urllib.error
    import urllib.request

    repo: Repo = current_app.config["REPO"]
    body = request.get_json(silent=True) or {}
    ids = body.get("suggestion_ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "suggestion_ids required"}), 400

    settings = repo.get_settings()
    filters = parse_review_filters(request.args, settings)
    if body.get("min_sample") is not None:
        try:
            filters["min_sample"] = max(1, int(body["min_sample"]))
        except (TypeError, ValueError):
            filters["min_sample"] = 1
    else:
        filters["min_sample"] = 1
    positions = _get_closed_positions(repo, include_deleted=filters.get("include_deleted", False))
    position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
    snapshots = _load_open_snapshots(repo, position_ids)
    positions = filter_closed_positions(positions, snapshots, filters)
    position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
    snapshots = _load_open_snapshots(repo, position_ids)
    excursions = _load_excursions(repo, position_ids)
    records = build_review_records(positions, snapshots, excursions)
    min_sample = int(filters.get("min_sample", 1))
    slices_dict, _ = build_condition_slices(records, min_sample)
    roes = [r["roe"] for r in records if r.get("roe") is not None]
    avg_roe = sum(roes) / len(roes) if roes else None
    performance_review = build_performance_review(slices_dict, min_sample, avg_roe)
    suggestions = build_suggestions(slices_dict, performance_review, settings, min_sample)

    patch = apply_suggestion_changes(settings, suggestions, [str(i) for i in ids])
    if not patch:
        return jsonify({"error": "no applicable changes for given ids"}), 400

    before = repo.get_settings()
    try:
        updated = repo.merge_settings(patch)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    worker_url = "http://127.0.0.1:7001/reload"
    try:
        req = urllib.request.Request(
            worker_url,
            data=json.dumps({}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
    except Exception as exc:
        repo.save_settings(before)
        return jsonify({
            "error": "worker_reload_failed",
            "message": str(exc),
            "settings_rolled_back": True,
        }), 409

    return jsonify({
        "ok": True,
        "applied_ids": [str(i) for i in ids],
        "patch": patch,
        "settings": updated,
    })


@bp_review.route("/closed_positions")
def closed_positions():
    """已结束持仓（历史成交）列表，按平仓时间从新到旧。"""
    repo: Repo = current_app.config["REPO"]
    rows = _sorted_closed_positions(_get_closed_positions(repo))
    return jsonify({"positions": rows})


@bp_review.route("/positions.csv")
def positions_csv():
    repo: Repo = current_app.config["REPO"]
    positions = repo.list_positions()

    output = io.StringIO()
    fields = [
        "id", "symbol", "expiration", "strike", "contracts",
        "open_at", "open_premium", "state",
        "close_at", "close_premium", "close_reason", "realized_pnl", "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for p in positions:
        if p.get("state") == "DELETED":
            continue
        writer.writerow({k: p.get(k, "") for k in fields})

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=positions.csv"},
    )


@bp_review.route("/positions/<int:position_id>/attribution")
def position_attribution(position_id: int):
    repo: Repo = current_app.config["REPO"]

    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "Position not found"}), 404

    # Gather entry data from open_candidate or open_snapshot
    spot_open = None
    entry_delta = None
    entry_theta = None
    entry_vega = None
    iv_open = None
    entry_source = None

    open_candidate_id = pos.get("open_candidate_id")
    if open_candidate_id is not None:
        try:
            cand = repo.get_candidate_by_id(open_candidate_id)
            if cand:
                spot_open = cand.get("spot")
                entry_delta = cand.get("delta")
                entry_theta = cand.get("theta")
                entry_vega = cand.get("vega")
                iv_open = cand.get("iv")
                entry_source = "candidate"
        except AttributeError:
            pass

    if entry_source is None:
        try:
            snap = repo.get_open_snapshot(position_id)
            if snap:
                spot_open = snap.get("spot")
                entry_delta = snap.get("delta")
                entry_theta = snap.get("theta")
                entry_vega = snap.get("vega")
                iv_open = snap.get("iv")
                entry_source = "open_snapshot"
        except AttributeError:
            pass

    if entry_source is None:
        return jsonify({"data_available": False, "reason": "no_entry_data"})

    snaps = repo.list_radar_snapshots(position_id, limit=500)

    close_at_raw = pos.get("close_at")
    close_dt = parse_instant_utc(close_at_raw) if close_at_raw else None
    open_dt_pos = parse_instant_utc(pos.get("open_at"))

    def _snap_time(s: Dict[str, Any]) -> Optional[datetime]:
        return parse_instant_utc(s.get("taken_at"))

    close_d_et = close_dt.astimezone(APP_TZ).date() if close_dt else None
    open_d_et = open_dt_pos.astimezone(APP_TZ).date() if open_dt_pos else None

    snaps_chrono = sorted(
        snaps,
        key=lambda s: (_snap_time(s) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
    )

    # Last radar at or before close (instant-aware; fixes space-vs-'T' lexicographic bugs)
    spot_close = None
    if close_dt:
        at_or_before = [
            s for s in snaps_chrono
            if _snap_time(s) is not None and _snap_time(s) <= close_dt
        ]
        if at_or_before:
            spot_close = at_or_before[-1].get("spot")
        elif snaps_chrono:
            parsed = [(t, s) for s in snaps_chrono if (t := _snap_time(s)) is not None]
            if parsed:
                _, nearest_s = min(parsed, key=lambda ts: abs((ts[0] - close_dt).total_seconds()))
                spot_close = nearest_s.get("spot")

    # MAE / MFE: daily synthetic replay uses session-end taken_at (~16:00 ET). Comparing that
    # instant to an intraday close_at on the **same ET calendar date** falsely drops every
    # point (common after entry_recalc + same-day manual close). Filter by ET **date** overlap
    # with [open calendar day, close calendar day] inclusive.
    # MAE/MFE: excursion versus **first** radar observation in chronological order (entry replay bar),
    # not bare min/max of pnl_pct (two flat daily marks both ~0 falsely show "0%/0%" as data).
    pnl_chrono: List[float] = []
    snap_pairs: List[tuple[float, float]] = []
    for s in snaps:
        t = _snap_time(s)
        if t is None or s.get("pnl_pct") is None:
            continue
        snap_d_et = t.astimezone(APP_TZ).date()
        if close_d_et is not None:
            if snap_d_et > close_d_et:
                continue
            if open_d_et is not None and snap_d_et < open_d_et:
                continue
        snap_pairs.append((t.timestamp(), float(s["pnl_pct"])))
    snap_pairs.sort(key=lambda x: x[0])
    pnl_chrono = [p for _, p in snap_pairs]
    mae, mfe = relative_mae_mfe_from_pnls_chronologic(pnl_chrono)
    mae_mfe_flat_replay = bool(
        len(pnl_chrono) >= 2 and mae is None and mfe is None
    )

    # PnL attribution (only if spot_close available)
    delta_contribution = None
    theta_contribution = None
    residual = None

    if spot_close is not None and spot_open is not None:
        open_dt = parse_instant_utc(pos["open_at"])
        close_dt_hold = parse_instant_utc(pos.get("close_at") or pos.get("open_at"))
        if open_dt is None or close_dt_hold is None:
            days_held = 1
        else:
            days_held = max((close_dt_hold - open_dt).days, 1)

        contracts = int(pos.get("contracts") or 1)
        total_pnl = float(pos.get("realized_pnl") or 0)

        delta_contribution = -(entry_delta or 0) * (spot_close - spot_open) * 100 * contracts
        theta_contribution = -(entry_theta or 0) * days_held * 100 * contracts
        residual = total_pnl - delta_contribution - theta_contribution
    else:
        days_held = None
        contracts = int(pos.get("contracts") or 1)
        total_pnl = float(pos.get("realized_pnl") or 0)

    return jsonify({
        "data_available": True,
        "position_id": position_id,
        "days_held": days_held if spot_close is not None and spot_open is not None else None,
        "spot_open": spot_open,
        "spot_close": spot_close,
        "iv_open": iv_open,
        "entry_delta": entry_delta,
        "entry_theta": entry_theta,
        "entry_vega": entry_vega,
        "delta_contribution": delta_contribution,
        "theta_contribution": theta_contribution,
        "residual": residual,
        "total_pnl": total_pnl,
        "mae": mae,
        "mfe": mfe,
        "mae_mfe_flat_replay": mae_mfe_flat_replay,
        "radar_points": len(snaps),
    })


@bp_review.route("/positions/<int:position_id>/snapshot")
def position_snapshot(position_id: int):
    repo: Repo = current_app.config["REPO"]

    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "Position not found"}), 404

    open_snapshot = None
    try:
        open_snapshot = repo.get_open_snapshot(position_id)
    except AttributeError:
        pass

    # Migrate legacy 5m/1h-series intraday_bs blobs (e.g. bar_count in the dozens/hundreds).
    # GET snapshot is cheaper than forcing users to re-close or hunt for recalc.
    if (
        str(pos.get("state") or "") in _CLOSED_POSITION_STATES
        and isinstance(open_snapshot, dict)
        and _intraday_bs_needs_daily_hl_migration(open_snapshot)
    ):
        try:
            enrich_closed_position_intraday_bs(repo, position_id)
            open_snapshot = repo.get_open_snapshot(position_id)
        except Exception as exc:
            current_app.logger.warning(
                "review snapshot: intraday_bs migration failed position_id=%s: %s",
                position_id,
                exc,
            )

    candidate_data = None
    open_candidate_id = pos.get("open_candidate_id")
    if open_candidate_id is not None:
        try:
            cand = repo.get_candidate_by_id(open_candidate_id)
            if cand:
                candidate_data = {
                    "iv_rank": cand.get("iv_rank"),
                    "iv": cand.get("iv"),
                    "delta": cand.get("delta"),
                    "theta": cand.get("theta"),
                    "vega": cand.get("vega"),
                    "spot": cand.get("spot"),
                    "dte": cand.get("dte"),
                    "annualized_roi": cand.get("annualized_roi"),
                    "score": cand.get("score"),
                }
        except AttributeError:
            pass

    return jsonify({
        "position_id": position_id,
        "open_snapshot": open_snapshot,
        "candidate_data": candidate_data,
    })


@bp_review.route("/positions/<int:position_id>/delete", methods=["POST"])
def soft_delete_closed_review_position(position_id: int):
    """Soft-delete a closed position from review (state=DELETED; hidden from stats/lists)."""
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "Position not found"}), 404
    st = str(pos.get("state") or "")
    if st == "OPEN":
        return jsonify({"error": "cannot delete OPEN position"}), 400
    if st == "DELETED":
        return jsonify({"ok": True, "already_deleted": True})
    if st not in _CLOSED_POSITION_STATES:
        return jsonify({"error": "invalid position state"}), 400
    repo.set_position_state(position_id, "DELETED")
    return jsonify({"ok": True})


@bp_review.route("/positions/<int:position_id>/snapshot/refresh", methods=["POST"])
def refresh_position_snapshot(position_id: int):
    """Recompute entry-environment fields as-of open_at and merge into open_snapshot."""
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "Position not found"}), 404

    merged = _merge_open_snapshot_refresh(repo, position_id, pos)
    if not merged:
        return jsonify({"ok": False, "reason": "no_snapshot_data"}), 422

    repo.save_open_snapshot(position_id, merged)
    return jsonify({"ok": True, "position_id": position_id, "fields": sorted(merged.keys())})


@bp_review.route("/positions/<int:position_id>/entry_recalc", methods=["POST"])
def entry_recalc_closed_position(position_id: int):
    """
    Closed-only: BS entry Greeks at open calendar date + synthetic daily radar (constant IV).
    Deletes existing radar_snapshots for this position.
    """
    repo: Repo = current_app.config["REPO"]
    settings = repo.get_settings()
    rf = float(settings.get("risk_free_rate", 0.045))
    try:
        payload = recalculate_closed_position_insights(
            repo, position_id, risk_free_rate=rf
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("entry_recalc_failed position_id=%s", position_id)
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify(payload)


@bp_review.route("/snapshots/refresh_closed", methods=["POST"])
@bp_review.route("/snapshots/refresh_entry", methods=["POST"])
def refresh_all_entry_snapshots():
    """Best-effort refresh entry snapshots for every position (OPEN + closed)."""
    repo: Repo = current_app.config["REPO"]
    rows = _sorted_all_positions(repo)
    saved = 0
    skipped_empty = 0
    errors: List[Dict[str, Any]] = []

    for i, pos in enumerate(rows):
        pid = pos["id"]
        try:
            merged = _merge_open_snapshot_refresh(repo, pid, pos)
            if not merged:
                skipped_empty += 1
                continue
            repo.save_open_snapshot(pid, merged)
            saved += 1
        except Exception as exc:
            errors.append({"position_id": pid, "error": str(exc)})
        if i + 1 < len(rows):
            time.sleep(0.15)

    return jsonify({
        "total": len(rows),
        "saved": saved,
        "skipped_empty": skipped_empty,
        "errors": errors,
    })
