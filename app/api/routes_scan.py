from __future__ import annotations

import logging
import threading
from flask import Blueprint, current_app, jsonify

from app.data.provider_yfinance import YFinanceProvider
from app.db.repo import Repo
from app.jobs.job_screener import run_screener

log = logging.getLogger(__name__)

bp_scan = Blueprint("scan", __name__, url_prefix="/api")


@bp_scan.route("/scan/run", methods=["POST"])
def manual_scan():
    repo: Repo = current_app.config["REPO"]
    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    provider_name = settings.get("provider", "yfinance")

    def _run():
        provider = YFinanceProvider()
        run_screener(repo, provider, trigger="manual", risk_free_rate=risk_free)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": "scan started in background"})


@bp_scan.route("/scan/latest", methods=["GET"])
def latest_candidates():
    repo: Repo = current_app.config["REPO"]
    con = repo._connect()
    row = con.execute(
        "SELECT id FROM scan_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        return jsonify([])
    run_id = row["id"]
    return jsonify(repo.list_candidates(run_id, limit=50))
