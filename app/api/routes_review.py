from __future__ import annotations

import csv
import io
import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify

from app.db.repo import Repo

bp_review = Blueprint("review", __name__, url_prefix="/api/review")


def _get_closed_positions(repo: Repo) -> List[Dict[str, Any]]:
    """Return all non-OPEN positions for review calculations."""
    closed_states = ("CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED")
    all_pos = repo.list_positions()
    return [p for p in all_pos if p.get("state") in closed_states]


def _sorted_closed_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort by close time descending (fallback open_at)."""

    def _sort_key(p: Dict[str, Any]) -> str:
        return str(p.get("close_at") or p.get("open_at") or "")

    return sorted(rows, key=_sort_key, reverse=True)


def _compute_summary(positions: List[Dict[str, Any]], repo: Optional[Repo]) -> Dict[str, Any]:
    if not positions:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_annualized_roi": None,
            "avg_roe": None,
            "total_premium": None,
            "total_realized_pnl": None,
            "by_close_reason": [],
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "avg_maee": None,
            "avg_mfe": None,
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

        strike = float(p.get("strike") or 1)
        if strike > 0:
            margin = strike * 100 * contracts
            if margin > 0:
                roi_list.append(pnl / margin)

        if p.get("id") is not None:
            position_ids.append(p["id"])

    win_rate = wins / trade_count if trade_count > 0 else None
    avg_roi = sum(roi_list) / len(roi_list) if roi_list else None
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

    # MAE / MFE via repo method (added by another task)
    avg_maee: Optional[float] = None
    avg_mfe: Optional[float] = None
    if repo is not None and position_ids:
        try:
            mae_mfe_data = repo.get_mae_mfe_for_positions(position_ids)
            maes = [v["mae"] for v in mae_mfe_data.values() if v.get("mae") is not None]
            mfes = [v["mfe"] for v in mae_mfe_data.values() if v.get("mfe") is not None]
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

    return {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_annualized_roi": avg_roi,
        "avg_roe": avg_roi,
        "total_premium": total_premium,
        "total_realized_pnl": round(sum_realized, 2),
        "by_close_reason": by_reason,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "avg_maee": avg_maee,
        "avg_mfe": avg_mfe,
    }


@bp_review.route("/summary")
def summary():
    repo: Repo = current_app.config["REPO"]
    positions = _get_closed_positions(repo)
    body = _compute_summary(positions, repo)
    body["closed_positions"] = _sorted_closed_positions(positions)
    return jsonify(body)


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

    # Get radar snapshots sorted ascending
    snaps = repo.list_radar_snapshots(position_id, limit=500)
    snaps_sorted = sorted(snaps, key=lambda s: str(s.get("taken_at") or ""))

    # Find close spot: last snapshot with taken_at <= close_at
    close_at = pos.get("close_at")
    spot_close = None
    if close_at:
        candidates = [s for s in snaps_sorted if str(s.get("taken_at") or "") <= str(close_at)]
        if candidates:
            spot_close = candidates[-1].get("spot")

    # MAE / MFE from radar snapshots
    pnl_values = [s["pnl_pct"] for s in snaps if s.get("pnl_pct") is not None]
    mae = min(pnl_values) if pnl_values else None
    mfe = max(pnl_values) if pnl_values else None

    # PnL attribution (only if spot_close available)
    delta_contribution = None
    theta_contribution = None
    residual = None

    if spot_close is not None and spot_open is not None:
        open_dt = datetime.fromisoformat(str(pos["open_at"]).replace("Z", "+00:00"))
        close_dt_str = pos.get("close_at") or pos.get("open_at")
        close_dt = datetime.fromisoformat(str(close_dt_str).replace("Z", "+00:00"))
        days_held = max((close_dt - open_dt).days, 1)

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
