from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

from app.db.repo import Repo
from app.notify.bus import bus

log = logging.getLogger(__name__)

bp_internal = Blueprint("internal", __name__, url_prefix="/api/internal")


@bp_internal.route("/notify", methods=["POST"])
def notify():
    # Only allow calls from localhost
    if request.remote_addr not in ("127.0.0.1", "::1"):
        log.warning("internal/notify rejected from %s", request.remote_addr)
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    event_id = data.get("id")
    if event_id is None:
        return jsonify({"error": "missing id"}), 400

    from flask import current_app
    repo: Repo = current_app.config["REPO"]
    event = repo.get_event(int(event_id))
    if event is None:
        return jsonify({"error": "event not found"}), 404

    bus.publish(event)
    return jsonify({"ok": True})
