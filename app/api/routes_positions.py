from __future__ import annotations

import json as _json
import logging as _logging
from datetime import datetime, timezone
from flask import Blueprint, current_app, jsonify, request

from app.core.position_mark import mark_short_put_position
from app.core.settlement import calc_realized_pnl
from app.data.provider_yfinance import YFinanceProvider
from app.db.repo import Repo

bp_positions = Blueprint("positions", __name__, url_prefix="/api")


def _capture_open_snapshot(repo: Repo, position_id: int, request_data: dict, pos: dict) -> None:
    """
    Gather entry environment metrics and store them as open_snapshot.
    Best-effort: individual failures are caught and skipped.
    """
    snapshot: dict = {}

    cand_id = request_data.get("open_candidate_id")
    if cand_id:
        try:
            cand = repo.get_candidate_by_id(int(cand_id))
            if cand:
                for field in ("iv_rank", "iv", "delta", "theta", "vega", "spot", "dte", "annualized_roi", "score"):
                    if cand.get(field) is not None:
                        snapshot[field] = cand[field]
        except Exception:
            pass

    if "spot" not in snapshot and request_data.get("spot"):
        snapshot["spot"] = float(request_data["spot"])

    symbol = pos.get("symbol", "").upper()
    if symbol:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d", interval="1d", auto_adjust=True)
            if not hist.empty and len(hist) >= 7:
                closes = [float(c) for c in hist["Close"].tolist()]
                from app.core.technicals import compute_rsi, compute_bb_lower_distance_pct
                rsi_6 = compute_rsi(closes, 6)
                rsi_12 = compute_rsi(closes, 12)
                rsi_24 = compute_rsi(closes, 24)
                bb_dist = compute_bb_lower_distance_pct(closes, window=20)
                if rsi_6 is not None:
                    snapshot["rsi_6"] = rsi_6
                if rsi_12 is not None:
                    snapshot["rsi_12"] = rsi_12
                if rsi_24 is not None:
                    snapshot["rsi_24"] = rsi_24
                if bb_dist is not None:
                    snapshot["bb_distance_pct"] = bb_dist
        except Exception as e:
            _logging.getLogger(__name__).info("RSI/BB snapshot fetch skipped: %s", e)

    if snapshot:
        repo.save_open_snapshot(position_id, snapshot)


@bp_positions.route("/positions", methods=["GET"])
def list_positions():
    repo: Repo = current_app.config["REPO"]
    state = request.args.get("state")
    return jsonify(repo.list_positions(state=state))


@bp_positions.route("/positions/marks", methods=["GET"])
def list_positions_with_marks():
    """OPEN positions with live spot / option mid / unrealized P&L (same math as radar)."""
    repo: Repo = current_app.config["REPO"]
    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    positions = repo.list_positions(state="OPEN")
    quoted_at = datetime.now(timezone.utc).isoformat()
    provider = YFinanceProvider()
    out_rows = []
    for pos in positions:
        row = dict(pos)
        mark = mark_short_put_position(row, provider, risk_free)
        row["mark"] = mark
        out_rows.append(row)
    return jsonify({"quoted_at": quoted_at, "positions": out_rows})


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

    try:
        _capture_open_snapshot(repo, pid, data, pos)
    except Exception as _snap_exc:
        _logging.getLogger(__name__).warning("open_snapshot capture failed: %s", _snap_exc)

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
        fee_legs=2,
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
