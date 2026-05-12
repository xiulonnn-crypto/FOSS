from __future__ import annotations

import json
import logging
import queue
import time
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from app.db.repo import Repo
from app.notify.bus import bus

log = logging.getLogger(__name__)

bp_events = Blueprint("events", __name__, url_prefix="/api")


@bp_events.route("/events")
def list_events():
    repo: Repo = current_app.config["REPO"]
    limit = int(request.args.get("limit", 50))
    unread_only = request.args.get("unread", "false").lower() == "true"
    if unread_only:
        events = repo.list_unread_events(limit=limit)
    else:
        events = repo.list_events(limit=limit)
    return jsonify(events)


@bp_events.route("/events/<int:event_id>/ack", methods=["PUT"])
def ack_event(event_id: int):
    repo: Repo = current_app.config["REPO"]
    repo.ack_event(event_id)
    return jsonify({"ok": True})


@bp_events.route("/events/all-read", methods=["POST"])
def all_read():
    repo: Repo = current_app.config["REPO"]
    events = repo.list_unread_events(limit=500)
    for e in events:
        repo.ack_event(e["id"])
    return jsonify({"acked": len(events)})


@bp_events.route("/events/stream")
def stream():
    def generate():
        q = bus.subscribe()
        try:
            # Send buffered unread events first
            repo: Repo = current_app.config["REPO"]
            for evt in repo.list_unread_events(limit=20):
                yield f"event: event\ndata: {json.dumps(evt, default=str)}\n\n"

            last_heartbeat = time.time()
            while True:
                try:
                    data = q.get(timeout=5.0)
                    yield f"event: event\ndata: {data}\n\n"
                except queue.Empty:
                    now = time.time()
                    if now - last_heartbeat >= 30:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
        except GeneratorExit:
            pass
        finally:
            bus.unsubscribe(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
