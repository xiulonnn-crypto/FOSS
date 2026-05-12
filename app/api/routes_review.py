from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify, request

from app.db.repo import Repo

bp_review = Blueprint("review", __name__, url_prefix="/api/review")


def _get_closed_positions(repo: Repo) -> List[Dict[str, Any]]:
    """Return all non-OPEN positions for review calculations."""
    closed_states = ("CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED")
    all_pos = repo.list_positions()
    return [p for p in all_pos if p.get("state") in closed_states]


def _compute_summary(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not positions:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_annualized_roi": None,
            "total_premium": None,
            "by_close_reason": [],
        }

    trade_count = len(positions)
    wins = 0
    total_pnl = 0.0
    roi_list = []
    premium_list = []

    for p in positions:
        pnl = float(p.get("realized_pnl") or 0)
        if pnl > 0:
            wins += 1
        total_pnl += pnl

        # Total collected premium (open_premium * contracts * 100)
        op = float(p.get("open_premium") or 0)
        contracts = int(p.get("contracts") or 1)
        premium_list.append(op * contracts * 100)

        # Annualized ROI estimate:
        # If we have realized_pnl, compute vs margin (strike * 100 * contracts)
        strike = float(p.get("strike") or 1)
        if strike > 0:
            margin = strike * 100 * contracts
            if margin > 0:
                roi_list.append(pnl / margin)

    win_rate = wins / trade_count if trade_count > 0 else None
    avg_roi = sum(roi_list) / len(roi_list) if roi_list else None
    total_premium = sum(premium_list)

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
        "total_premium": total_premium,
        "by_close_reason": by_reason,
    }


@bp_review.route("/summary")
def summary():
    repo: Repo = current_app.config["REPO"]
    positions = _get_closed_positions(repo)
    return jsonify(_compute_summary(positions))


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
