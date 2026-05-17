from __future__ import annotations

import logging
import json
import re
import unicodedata
import urllib.error
import urllib.request

from flask import Blueprint, current_app, jsonify, request

from app.core.symbols import normalize_ticker_symbol
from app.db.repo import Repo

log = logging.getLogger(__name__)

bp_settings = Blueprint("settings", __name__, url_prefix="/api")

WORKER_RELOAD_URL = "http://127.0.0.1:7001/reload"


@bp_settings.route("/settings", methods=["GET"])
def get_settings():
    repo: Repo = current_app.config["REPO"]
    return jsonify(repo.get_settings())


@bp_settings.route("/settings", methods=["POST"])
def post_settings():
    repo: Repo = current_app.config["REPO"]
    partial = request.get_json(silent=True) or {}
    updated = repo.merge_settings(partial)
    # Notify worker to reload schedules
    try:
        data = b"{}"
        req = urllib.request.Request(WORKER_RELOAD_URL, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=2):
            pass
        log.info("settings: worker reload triggered")
    except Exception as exc:
        log.debug("settings: worker reload failed (non-fatal): %s", exc)
    return jsonify(updated)


@bp_settings.route("/watchlist", methods=["GET"])
def get_watchlist():
    repo: Repo = current_app.config["REPO"]
    return jsonify(repo.list_watchlist())


@bp_settings.route("/watchlist", methods=["POST"])
def post_watchlist():
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    raw = data.get("symbols", "")
    raw_norm = unicodedata.normalize("NFKC", raw or "")
    symbols = [
        normalize_ticker_symbol(part)
        for part in re.split(r"[,，\s]+", raw_norm)
        if part.strip()
    ]
    symbols = list(dict.fromkeys(s for s in symbols if s))
    repo.upsert_symbols(symbols)
    return jsonify(repo.list_watchlist())
