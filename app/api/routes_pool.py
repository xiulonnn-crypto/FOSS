from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, request

from app.core.open_snapshot import build_open_snapshot_dict
from app.core.symbols import normalize_ticker_symbol
from app.db.repo import Repo

bp_pool = Blueprint("pool", __name__, url_prefix="/api")

log = logging.getLogger(__name__)


_UNDERLYING_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED"}
_WATCH_RESTORABLE_STATUSES = {"WATCHING", "READY", "IGNORED", "EXPIRED"}
_WATCH_OPENABLE_STATUSES = {"WATCHING", "READY"}


def _json_error(message: str, status_code: int = 400, *, code: Optional[str] = None):
    payload: Dict[str, Any] = {"ok": False, "error": message}
    if code:
        payload["code"] = code
    return jsonify(payload), status_code


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid number")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid integer")


def _normalize_instant(value: object) -> str:
    if value is None:
        raise ValueError("missing instant")
    if not isinstance(value, str):
        raise ValueError("instant must be a string")
    text = value.strip()
    if not text:
        raise ValueError("empty instant")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _option_snapshot_payload(option: Dict[str, Any], body: Dict[str, Any], watch_id: int) -> Dict[str, Any]:
    payload = dict(body)
    payload.update(
        {
            "option_pool_id": option.get("id"),
            "option_watchlist_id": watch_id,
            "open_candidate_id": option.get("latest_candidate_id"),
        }
    )
    for field in (
        "iv_rank",
        "iv",
        "delta",
        "theta",
        "vega",
        "spot",
        "dte",
        "annualized_roi",
        "score",
        "quality_grade",
        "quality_score",
        "quality_flags",
        "quote_age_seconds",
        "greeks_source",
        "iv_rank_source",
        "latest_entry_signal_id",
        "entry_signal_status",
        "entry_signal_score",
        "entry_signal_summary",
    ):
        if option.get(field) is not None:
            payload[field] = option.get(field)
    if option.get("latest_entry_signal_id") is not None:
        payload["entry_signal_id"] = option.get("latest_entry_signal_id")
    if isinstance(option.get("entry_signal"), dict):
        payload["entry_signal"] = option.get("entry_signal")
    return payload


def _watch_mutation_payload(data: Dict[str, Any], *, restore: bool = False) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key in ("watch_reason", "ignore_reason", "notes"):
        if key in data:
            updates[key] = data.get(key)
    for key in ("target_premium", "target_score", "target_margin_buffer"):
        if key in data:
            updates[key] = _float_or_none(data.get(key))
    if "status" in data:
        status = str(data.get("status") or "").upper()
        if status not in _WATCH_RESTORABLE_STATUSES:
            raise ValueError("invalid status")
        updates["status"] = status
    elif restore:
        updates["status"] = "WATCHING"
    return updates


@bp_pool.route("/pool/underlyings", methods=["GET"])
def list_underlyings():
    repo: Repo = current_app.config["REPO"]
    rows = repo.list_pool_underlyings()
    rows.sort(key=lambda row: (0 if row.get("pool_status") == "ACTIVE" else 1, row.get("symbol") or ""))
    return jsonify({"underlyings": rows})


@bp_pool.route("/pool/underlyings/<symbol>", methods=["PATCH"])
def patch_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    sym = normalize_ticker_symbol(symbol)
    if not sym:
        return _json_error("invalid symbol")

    data = request.get_json(silent=True) or {}
    updates: Dict[str, Any] = {}
    if "pool_status" in data:
        status = str(data.get("pool_status") or "").upper()
        if status not in _UNDERLYING_STATUSES:
            return _json_error("invalid pool_status")
        updates["pool_status"] = status
    if "tags" in data:
        if not isinstance(data.get("tags"), list):
            return _json_error("tags must be a list")
        updates["tags"] = [str(tag).strip() for tag in data.get("tags") if str(tag).strip()]
    if "notes" in data:
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            return _json_error("notes must be a string")
        updates["notes"] = notes
    row = repo.update_pool_underlying(sym, updates)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/underlyings/<symbol>/pause", methods=["POST"])
def pause_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    row = repo.pause_pool_underlying(symbol)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/underlyings/<symbol>/archive", methods=["POST"])
def archive_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    row = repo.archive_pool_underlying(symbol)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/options", methods=["GET"])
def list_pool_options():
    repo: Repo = current_app.config["REPO"]
    status = request.args.get("status", "NEW,ACTIVE")
    try:
        rows = repo.list_option_pool(
            symbol=request.args.get("symbol") or None,
            status=status,
            quality_grade=request.args.get("quality_grade") or None,
            min_score=_float_or_none(request.args.get("min_score")),
            min_dte=_int_or_none(request.args.get("min_dte")),
            max_dte=_int_or_none(request.args.get("max_dte")),
            entry_signal_status=request.args.get("entry_signal_status") or None,
            min_entry_signal_score=_int_or_none(request.args.get("min_entry_signal_score")),
        )
    except ValueError:
        return _json_error("invalid filter value")
    return jsonify({"options": rows})


@bp_pool.route("/pool/options/<int:option_pool_id>/entry-signal", methods=["GET"])
def get_pool_option_entry_signal(option_pool_id: int):
    repo: Repo = current_app.config["REPO"]
    option = repo.get_option_pool(option_pool_id)
    if not option:
        return _json_error("option pool row not found", 404, code="not_found")
    return jsonify({"entry_signal": repo.get_latest_entry_signal(option_pool_id)})


@bp_pool.route("/watch/options", methods=["GET"])
def list_option_watches():
    repo: Repo = current_app.config["REPO"]
    rows = repo.list_option_watches(status=request.args.get("status") or None)
    return jsonify({"watches": rows})


@bp_pool.route("/watch/options", methods=["POST"])
def create_option_watch():
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    try:
        option_pool_id = int(data.get("option_pool_id"))
    except (TypeError, ValueError):
        return _json_error("missing or invalid option_pool_id")
    try:
        payload = _watch_mutation_payload(data, restore=True)
    except ValueError:
        return _json_error("invalid watch payload")
    payload["option_pool_id"] = option_pool_id
    watch = repo.create_option_watch(payload)
    if not watch:
        return _json_error("option pool row not found", 404, code="not_found")
    return jsonify(watch), 201


@bp_pool.route("/watch/options/<int:watch_id>", methods=["PATCH"])
def patch_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    try:
        payload = _watch_mutation_payload(data, restore=True)
    except ValueError:
        return _json_error("invalid watch payload")
    watch = repo.update_option_watch(watch_id, payload)
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    return jsonify(watch)


@bp_pool.route("/watch/options/<int:watch_id>/ignore", methods=["POST"])
def ignore_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    watch = repo.ignore_option_watch(watch_id, reason=data.get("ignore_reason"))
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    return jsonify(watch)


@bp_pool.route("/watch/options/<int:watch_id>/open", methods=["POST"])
def open_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    watch = repo.get_option_watch(watch_id)
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    if str(watch.get("status") or "").upper() not in _WATCH_OPENABLE_STATUSES:
        return _json_error("watch status is not openable")

    option = watch.get("option") or repo.get_option_pool(int(watch.get("option_pool_id") or 0))
    if not option:
        return _json_error("option pool row not found", 404, code="not_found")
    if str(option.get("status") or "").upper() in {"EXPIRED", "BLOCKED"}:
        return _json_error("option status is not openable")

    body = request.get_json(silent=True) or {}
    for field in ("open_premium", "contracts"):
        if field not in body:
            return _json_error(f"missing field: {field}")
    try:
        contracts = int(body.get("contracts"))
        open_premium = float(body.get("open_premium"))
    except (TypeError, ValueError):
        return _json_error("invalid open_premium or contracts")
    if contracts <= 0:
        return _json_error("contracts must be positive")
    if open_premium < 0:
        return _json_error("open_premium must be non-negative")

    open_at_raw = body.get("open_at")
    try:
        open_at = _normalize_instant(open_at_raw) if open_at_raw else datetime.now(timezone.utc).isoformat()
    except ValueError:
        return _json_error("invalid open_at")

    pos = {
        "symbol": option["symbol"],
        "expiration": option["expiration"],
        "strike": float(option["strike"]),
        "contracts": contracts,
        "open_at": open_at,
        "open_premium": open_premium,
        "open_candidate_id": option.get("latest_candidate_id"),
        "state": "OPEN",
        "notes": body.get("notes") if "notes" in body else watch.get("notes"),
    }
    position_id = repo.insert_position(pos)
    try:
        snapshot_payload = _option_snapshot_payload(option, body, watch_id)
        snapshot = build_open_snapshot_dict(repo, pos, snapshot_payload)
        if snapshot:
            repo.save_open_snapshot(position_id, snapshot)
    except Exception as exc:
        log.warning("watch open_snapshot capture failed: %s", exc)

    repo.mark_option_watch_opened(
        watch_id,
        {
            "status": "OPENED",
            "reason": "user_opened",
            "position_id": position_id,
        },
    )
    return jsonify({"id": position_id, **pos, "option_pool_id": option.get("id"), "option_watchlist_id": watch_id}), 201
