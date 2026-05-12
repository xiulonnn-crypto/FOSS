from __future__ import annotations

import logging
from datetime import datetime, timezone
from flask import Blueprint, current_app, jsonify, request

from app.core.settlement import calc_realized_pnl
from app.db.repo import Repo

log = logging.getLogger(__name__)

bp_positions = Blueprint("positions", __name__, url_prefix="/api")


@bp_positions.route("/positions", methods=["GET"])
def list_positions():
    repo: Repo = current_app.config["REPO"]
    state = request.args.get("state")
    return jsonify(repo.list_positions(state=state))


@bp_positions.route("/positions", methods=["POST"])
def create_position():
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    required = ["symbol", "expiration", "strike", "contracts", "open_premium"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"missing field: {field}"}), 400

    pos = {
        "symbol": data["symbol"].upper(),
        "expiration": data["expiration"],
        "strike": float(data["strike"]),
        "contracts": int(data["contracts"]),
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": float(data["open_premium"]),
        "open_candidate_id": data.get("open_candidate_id"),
        "state": "OPEN",
        "notes": data.get("notes"),
    }
    pid = repo.insert_position(pos)
    return jsonify({"id": pid, **pos}), 201


@bp_positions.route("/positions/<int:position_id>", methods=["GET"])
def get_position(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    return jsonify(pos)


@bp_positions.route("/positions/<int:position_id>/close", methods=["POST"])
def close_position(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    if pos["state"] != "OPEN":
        return jsonify({"error": "position is not OPEN"}), 400

    data = request.get_json(silent=True) or {}
    close_premium = float(data.get("close_premium", 0))
    close_reason = data.get("close_reason", "manual")
    settings = repo.get_settings()
    fee = float(settings.get("fees", {}).get("usd_per_contract", 1.0))
    pnl = calc_realized_pnl(
        float(pos["open_premium"]),
        close_premium,
        int(pos["contracts"]),
        fee,
    )
    repo.close_position(position_id, "CLOSED_EARLY", close_premium, close_reason, pnl)
    return jsonify({"ok": True, "realized_pnl": pnl})


@bp_positions.route("/positions/<int:position_id>/notes", methods=["PATCH"])
def patch_notes(position_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    note = data.get("notes", "")
    import sqlite3
    with repo._connect() as con:
        con.execute("UPDATE positions SET notes=? WHERE id=?", (note, position_id))
    return jsonify({"ok": True})


@bp_positions.route("/positions/<int:position_id>/radar", methods=["GET"])
def radar_history(position_id: int):
    repo: Repo = current_app.config["REPO"]
    limit = int(request.args.get("limit", 100))
    snaps = repo.list_radar_snapshots(position_id, limit=limit)
    return jsonify(snaps)
