from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.symbols import normalize_ticker_symbol


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _watchlist_entry_enabled(w: Dict[str, Any]) -> bool:
    """True if symbol should participate in scan/jobs.

    SQLite may store NULL in enabled in legacy rows; dict.get("enabled", 1) still
    returns None when the key exists with a NULL value, excluding the symbol — treat
    None as enabled.
    """
    status = w.get("pool_status")
    if status:
        return str(status).upper() == "ACTIVE"
    v = w.get("enabled")
    if v is None:
        return True
    try:
        return int(v) != 0
    except (TypeError, ValueError):
        return bool(v)


def _json_dumps_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads_dict(value: Any) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_loads_list(value: Any) -> List[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_loads_any(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _candidate_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    candidate = dict(row)
    candidate["quality_flags"] = _json_loads_list(candidate.get("quality_flags"))
    if "state_features" in candidate:
        candidate["state_features"] = _json_loads_dict(candidate.get("state_features"))
    if candidate.get("quality_grade") is None:
        candidate["quality_grade"] = "unknown"
    return candidate


def _pool_status_enabled(status: Optional[str]) -> int:
    return 1 if (status or "").upper() == "ACTIVE" else 0


def _derive_pool_status(enabled: Any, status: Any) -> str:
    if status:
        normalized = str(status).upper()
        if normalized in {"ACTIVE", "PAUSED", "ARCHIVED"}:
            return normalized
    try:
        return "ACTIVE" if int(enabled) != 0 else "PAUSED"
    except (TypeError, ValueError):
        return "ACTIVE" if enabled is None else "PAUSED"


def _watchlist_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    status = _derive_pool_status(item.get("enabled"), item.get("pool_status"))
    item["pool_status"] = status
    item["enabled"] = _pool_status_enabled(status)
    item["tags"] = _json_loads_list(item.get("tags"))
    item["last_pool_summary"] = _json_loads_dict(item.get("last_pool_summary")) or {}
    return item


def _option_pool_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["quality_flags"] = _json_loads_list(item.get("quality_flags"))
    if "state_features" in item:
        item["state_features"] = _json_loads_dict(item.get("state_features"))
    item["is_watched"] = bool(item.get("watch_id"))
    payload = _json_loads_dict(item.get("entry_signal_payload"))
    if payload:
        item["entry_signal"] = payload
    elif item.get("entry_signal_status"):
        item["entry_signal"] = {
            "schema": "entry_signal_v1",
            "status": item.get("entry_signal_status"),
            "decision_score": item.get("entry_signal_score"),
            "summary": item.get("entry_signal_summary"),
            "generated_at": item.get("entry_signal_generated_at"),
        }
    return item


def _option_watch_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["last_signal"] = _json_loads_dict(item.get("last_signal")) or {}
    option = {
        key[7:]: item.pop(key)
        for key in list(item.keys())
        if key.startswith("option_") and key != "option_pool_id"
    }
    if option:
        option["quality_flags"] = _json_loads_list(option.get("quality_flags"))
        option["state_features"] = _json_loads_dict(option.get("state_features"))
        payload = _json_loads_dict(option.get("entry_signal_payload"))
        if payload:
            option["entry_signal"] = payload
        elif option.get("entry_signal_status"):
            option["entry_signal"] = {
                "schema": "entry_signal_v1",
                "status": option.get("entry_signal_status"),
                "decision_score": option.get("entry_signal_score"),
                "summary": option.get("entry_signal_summary"),
                "generated_at": option.get("entry_signal_generated_at"),
            }
        item["option"] = option
    return item


def _entry_signal_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    signal = _json_loads_dict(item.get("signal_json")) or {}
    if signal:
        signal["id"] = item.get("id")
        signal["entry_signal_id"] = item.get("id")
        signal["is_latest"] = bool(item.get("is_latest"))
        return signal
    item["metrics"] = _json_loads_dict(item.get("metrics_json")) or {}
    item["reasons"] = _json_loads_list(item.get("reasons_json"))
    item["blockers"] = _json_loads_list(item.get("blockers_json"))
    item["schema"] = "entry_signal_v1"
    item["entry_signal_id"] = item.get("id")
    item["is_latest"] = bool(item.get("is_latest"))
    return item


def _exit_signal_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    signal = _json_loads_dict(item.get("signal_json")) or {}
    if signal:
        signal.setdefault("position_id", item.get("position_id"))
        signal.setdefault("radar_snapshot_id", item.get("radar_snapshot_id"))
        signal.setdefault("action", item.get("action"))
        signal.setdefault("severity", item.get("severity"))
        signal.setdefault("urgency_score", item.get("urgency_score"))
        signal.setdefault("suggested_close_reason", item.get("suggested_close_reason"))
        signal.setdefault("summary", item.get("summary"))
        signal.setdefault("generated_at", item.get("created_at"))
        signal["id"] = item.get("id")
        signal["exit_signal_id"] = item.get("id")
        signal["is_latest"] = bool(item.get("is_latest"))
        return signal
    item["signal_json"] = signal
    item["schema"] = "exit_signal_v1"
    item["exit_signal_id"] = item.get("id")
    item["is_latest"] = bool(item.get("is_latest"))
    return item


def _position_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    if "exit_signal_payload" in item:
        item["exit_signal_payload"] = _json_loads_any(item.get("exit_signal_payload"))
    if "close_snapshot" in item:
        item["close_snapshot"] = _json_loads_any(item.get("close_snapshot"))
    return item


_OPTION_POOL_COLUMNS = [
    "symbol", "expiration", "strike", "right",
    "bid", "ask", "mid", "spot", "iv", "iv_rank",
    "delta", "theta", "vega", "gamma",
    "dte", "annualized_roi", "pop", "spread_pct",
    "breakeven", "margin_buffer", "score", "open_interest",
    "quality_grade", "quality_score", "quality_flags",
    "quote_age_seconds", "greeks_source", "iv_rank_source",
    "first_seen_at", "last_seen_at", "last_scan_run_id",
    "latest_candidate_id", "missed_scan_count", "status",
    "state_features",
]


_OPTION_POOL_METRIC_COLUMNS = [
    col
    for col in _OPTION_POOL_COLUMNS
    if col not in {"symbol", "expiration", "strike", "right", "first_seen_at"}
]


_OPTION_WATCH_UPDATE_COLUMNS = {
    "status",
    "watch_reason",
    "ignore_reason",
    "target_premium",
    "target_score",
    "target_margin_buffer",
    "notes",
    "last_evaluated_at",
    "last_signal",
}


_OPTION_WATCH_SELECT = """
    SELECT
      ow.*,
      op.id AS option_id,
      op.symbol AS option_symbol,
      op.expiration AS option_expiration,
      op.strike AS option_strike,
      op.right AS option_right,
      op.bid AS option_bid,
      op.ask AS option_ask,
      op.mid AS option_mid,
      op.spot AS option_spot,
      op.iv AS option_iv,
      op.iv_rank AS option_iv_rank,
      op.delta AS option_delta,
      op.theta AS option_theta,
      op.vega AS option_vega,
      op.gamma AS option_gamma,
      op.dte AS option_dte,
      op.annualized_roi AS option_annualized_roi,
      op.pop AS option_pop,
      op.spread_pct AS option_spread_pct,
      op.breakeven AS option_breakeven,
      op.margin_buffer AS option_margin_buffer,
      op.score AS option_score,
      op.open_interest AS option_open_interest,
      op.quality_grade AS option_quality_grade,
      op.quality_score AS option_quality_score,
      op.quality_flags AS option_quality_flags,
      op.quote_age_seconds AS option_quote_age_seconds,
      op.greeks_source AS option_greeks_source,
      op.iv_rank_source AS option_iv_rank_source,
      op.status AS option_status,
      op.last_seen_at AS option_last_seen_at,
      op.last_scan_run_id AS option_last_scan_run_id,
      op.latest_candidate_id AS option_latest_candidate_id,
      op.latest_entry_signal_id AS option_latest_entry_signal_id,
      op.entry_signal_status AS option_entry_signal_status,
      op.entry_signal_score AS option_entry_signal_score,
      op.entry_signal_summary AS option_entry_signal_summary,
      op.entry_signal_generated_at AS option_entry_signal_generated_at,
      op.entry_signal_payload AS option_entry_signal_payload,
      op.state_features AS option_state_features
    FROM option_watchlist ow
    JOIN option_pool op ON op.id = ow.option_pool_id
"""


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _normalize_status_filter(value: Any) -> Optional[List[str]]:
    if value is None or value == "all":
        return None
    if isinstance(value, str):
        statuses = [part.strip().upper() for part in value.split(",") if part.strip()]
    else:
        statuses = [str(part).upper() for part in value if part]
    return statuses or None


class Repo:
    """Thin DAO wrapping SQLite. All methods accept/return plain Python types."""

    def __init__(self, db_path: Path):
        self._path = db_path

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(self) -> Dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key='app'").fetchone()
        if row is None:
            return {}
        return json.loads(row["value"])

    def save_settings(self, settings: Dict[str, Any]) -> None:
        value = json.dumps(settings, ensure_ascii=False)
        with self._connect() as con:
            con.execute(
                "INSERT INTO settings(key, value) VALUES('app', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (value,),
            )

    def merge_settings(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        """Deep-merge partial into existing settings and save."""
        current = self.get_settings()
        _deep_merge(current, partial)
        self.save_settings(current)
        return current

    # ------------------------------------------------------------------
    # Phase-one feature snapshots
    # ------------------------------------------------------------------

    def upsert_market_iv_snapshot(self, row: Dict[str, Any]) -> None:
        symbol = normalize_ticker_symbol(row.get("symbol"))
        if not symbol:
            return
        as_of_date = _date_text(row.get("as_of_date"))
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO market_iv_snapshots(
                    symbol, as_of_date, iv30, atm_strike, skew, vix, source
                )
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(symbol, as_of_date) DO UPDATE SET
                    iv30=excluded.iv30,
                    atm_strike=excluded.atm_strike,
                    skew=excluded.skew,
                    vix=excluded.vix,
                    source=excluded.source
                """,
                (
                    symbol,
                    as_of_date,
                    row.get("iv30"),
                    row.get("atm_strike"),
                    row.get("skew"),
                    row.get("vix"),
                    row.get("source"),
                ),
            )

    def list_market_iv_snapshots(self, symbol: str, limit: int = 252) -> List[Dict[str, Any]]:
        sym = normalize_ticker_symbol(symbol)
        if not sym:
            return []
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM market_iv_snapshots WHERE symbol=? "
                "ORDER BY as_of_date DESC LIMIT ?",
                (sym, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_market_iv_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        rows = self.list_market_iv_snapshots(symbol, limit=1)
        return rows[0] if rows else None

    def insert_feature_snapshot(
        self,
        entity_type: str,
        entity_id: int,
        features: Dict[str, Any],
        *,
        as_of: Optional[str] = None,
    ) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO feature_snapshots(entity_type, entity_id, as_of, features_json) "
                "VALUES(?,?,?,?)",
                (
                    str(entity_type),
                    int(entity_id),
                    as_of or _now_utc(),
                    json.dumps(features, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def latest_feature_snapshot(self, entity_type: str, entity_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM feature_snapshots WHERE entity_type=? AND entity_id=? "
                "ORDER BY as_of DESC, id DESC LIMIT 1",
                (str(entity_type), int(entity_id)),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["features"] = _json_loads_dict(item.get("features_json")) or {}
        return item

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def list_watchlist(self) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT symbol, added_at, earnings_at, enabled, pool_status, tags, notes, "
                "last_scanned_at, last_candidate_count, last_pool_summary "
                "FROM watchlist ORDER BY symbol"
            ).fetchall()
        return [_watchlist_from_row(r) for r in rows]

    def list_pool_underlyings(self) -> List[Dict]:
        return self.list_watchlist()

    def list_active_underlying_symbols(self) -> List[str]:
        return [w["symbol"] for w in self.list_pool_underlyings() if w["pool_status"] == "ACTIVE"]

    def list_enabled_watchlist_symbols(self) -> List[str]:
        """Symbols included in scans (enabled≠0; NULL/missing counts as enabled)."""
        return [w["symbol"] for w in self.list_watchlist() if _watchlist_entry_enabled(w)]

    def upsert_symbols(self, symbols: List[str]) -> None:
        """Replace the active watchlist: only these symbols stay enabled.

        Symbols omitted from ``symbols`` are set to enabled=0 (history rows kept).
        """
        now = _now_utc()
        wanted = list(
            dict.fromkeys(
                norm
                for norm in (normalize_ticker_symbol(s) for s in symbols)
                if norm
            )
        )
        with self._connect() as con:
            con.execute(
                "UPDATE watchlist SET enabled=0, "
                "pool_status=CASE WHEN pool_status='ARCHIVED' THEN 'ARCHIVED' ELSE 'PAUSED' END"
            )
            for sym in wanted:
                con.execute(
                    "INSERT INTO watchlist(symbol, added_at, enabled, pool_status) "
                    "VALUES(?, ?, 1, 'ACTIVE') "
                    "ON CONFLICT(symbol) DO UPDATE SET enabled=1, pool_status='ACTIVE'",
                    (sym, now),
                )

    def set_earnings(self, symbol: str, earnings_at: Optional[str]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE watchlist SET earnings_at=? WHERE symbol=?",
                (earnings_at, symbol.upper()),
            )

    def update_pool_underlying(self, symbol: str, updates: Dict[str, Any]) -> Optional[Dict]:
        sym = normalize_ticker_symbol(symbol)
        if not sym:
            return None
        now = _now_utc()
        prepared: Dict[str, Any] = {}
        for key, value in updates.items():
            if key == "pool_status":
                status = str(value).upper()
                if status not in {"ACTIVE", "PAUSED", "ARCHIVED"}:
                    continue
                prepared["pool_status"] = status
                prepared["enabled"] = _pool_status_enabled(status)
            elif key == "tags":
                prepared["tags"] = _json_dumps_or_none(value if isinstance(value, list) else [])
            elif key == "last_pool_summary":
                prepared["last_pool_summary"] = _json_dumps_or_none(value if isinstance(value, dict) else {})
            elif key in {"notes", "last_scanned_at", "last_candidate_count", "earnings_at"}:
                prepared[key] = value
        with self._connect() as con:
            con.execute(
                "INSERT INTO watchlist(symbol, added_at, enabled, pool_status) "
                "VALUES(?, ?, 1, 'ACTIVE') ON CONFLICT(symbol) DO NOTHING",
                (sym, now),
            )
            if prepared:
                cols = ",".join(f"{key}=?" for key in prepared)
                con.execute(
                    f"UPDATE watchlist SET {cols} WHERE symbol=?",
                    [*prepared.values(), sym],
                )
            row = con.execute(
                "SELECT symbol, added_at, earnings_at, enabled, pool_status, tags, notes, "
                "last_scanned_at, last_candidate_count, last_pool_summary "
                "FROM watchlist WHERE symbol=?",
                (sym,),
            ).fetchone()
        return _watchlist_from_row(row) if row else None

    def pause_pool_underlying(self, symbol: str) -> Optional[Dict]:
        return self.update_pool_underlying(symbol, {"pool_status": "PAUSED"})

    def archive_pool_underlying(self, symbol: str) -> Optional[Dict]:
        return self.update_pool_underlying(symbol, {"pool_status": "ARCHIVED"})

    # ------------------------------------------------------------------
    # Scan runs
    # ------------------------------------------------------------------

    def insert_scan_run(
        self,
        provider: str,
        trigger: str,
        symbol_count: int = 0,
    ) -> int:
        now = _now_utc()
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO scan_runs(started_at, provider, trigger, symbol_count) "
                "VALUES(?,?,?,?)",
                (now, provider, trigger, symbol_count),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def finish_scan_run(
        self,
        run_id: int,
        candidate_count: int,
        snapshot_path: Optional[str] = None,
        diagnostics: Optional[dict] = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE scan_runs SET finished_at=?, candidate_count=?, snapshot_path=?, "
                "diagnostics=? "
                "WHERE id=?",
                (
                    _now_utc(),
                    candidate_count,
                    snapshot_path,
                    _json_dumps_or_none(diagnostics),
                    run_id,
                ),
            )

    def get_scan_run_meta(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM scan_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        meta = dict(row)
        meta["diagnostics"] = _json_loads_dict(meta.get("diagnostics"))
        return meta

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def insert_candidates(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        columns = [
            "scan_run_id", "symbol", "expiration", "strike",
            "bid", "ask", "mid", "spot", "iv", "iv_rank",
            "delta", "theta", "vega", "gamma",
            "dte", "annualized_roi", "pop", "spread_pct",
            "breakeven", "margin_buffer", "score", "open_interest",
            "quality_grade", "quality_score", "quality_flags",
            "quote_age_seconds", "greeks_source", "iv_rank_source",
            "state_features",
        ]
        placeholders = ",".join("?" * len(columns))
        col_str = ",".join(columns)
        values = []
        for row in rows:
            prepared = []
            for col in columns:
                value = row.get(col)
                if col in {"quality_flags", "state_features"} and not isinstance(value, str):
                    value = _json_dumps_or_none(value)
                prepared.append(value)
            values.append(prepared)
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO candidates({col_str}) VALUES({placeholders})",
                values,
            )

    def count_candidates(self, scan_run_id: int) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM candidates WHERE scan_run_id=?",
                (scan_run_id,),
            ).fetchone()
            return int(row["n"]) if row is not None else 0

    def list_candidates(self, scan_run_id: int, limit: int = 20) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM candidates WHERE scan_run_id=? ORDER BY score DESC LIMIT ?",
                (scan_run_id, limit),
            ).fetchall()
        return [_candidate_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def insert_position(self, pos: Dict[str, Any]) -> int:
        columns = [
            "symbol", "expiration", "strike", "contracts",
            "open_at", "open_premium", "open_candidate_id", "state", "notes",
        ]
        placeholders = ",".join("?" * len(columns))
        col_str = ",".join(columns)
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO positions({col_str}) VALUES({placeholders})",
                [pos.get(c) for c in columns],
            )
            return cur.lastrowid  # type: ignore[return-value]

    def list_positions(self, state: Optional[str] = None) -> List[Dict]:
        with self._connect() as con:
            if state:
                rows = con.execute(
                    "SELECT * FROM positions WHERE state=? ORDER BY open_at DESC",
                    (state,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM positions ORDER BY open_at DESC"
                ).fetchall()
        return [_position_from_row(r) for r in rows]

    def get_position(self, position_id: int) -> Optional[Dict]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM positions WHERE id=?", (position_id,)
            ).fetchone()
        return _position_from_row(row) if row else None

    def close_position(
        self,
        position_id: int,
        state: str,
        close_premium: Optional[float],
        close_reason: str,
        realized_pnl: float,
        close_at: Optional[str] = None,
    ) -> None:
        ts = close_at if close_at else _now_utc()
        with self._connect() as con:
            con.execute(
                "UPDATE positions SET state=?, close_at=?, close_premium=?, "
                "close_reason=?, realized_pnl=? WHERE id=?",
                (state, ts, close_premium, close_reason, realized_pnl, position_id),
            )

    def set_position_state(self, position_id: int, state: str) -> None:
        """Update positions.state only (used for soft-delete in review)."""
        with self._connect() as con:
            con.execute("UPDATE positions SET state=? WHERE id=?", (state, position_id))

    def update_position_fields(self, position_id: int, updates: Dict[str, Any]) -> None:
        allowed = (
            "symbol", "expiration", "strike", "contracts", "open_at", "open_premium",
            "notes", "close_at", "close_premium", "close_reason", "realized_pnl",
        )
        cols: List[str] = []
        vals: List[Any] = []
        for k, v in updates.items():
            if k not in allowed:
                continue
            cols.append(k)
            vals.append(v)
        if not cols:
            return
        set_clause = ",".join(f"{c}=?" for c in cols)
        with self._connect() as con:
            con.execute(
                f"UPDATE positions SET {set_clause} WHERE id=?",
                [*vals, position_id],
            )

    # ------------------------------------------------------------------
    # Radar snapshots
    # ------------------------------------------------------------------

    def insert_radar_snapshot(self, snap: Dict[str, Any]) -> Optional[int]:
        columns = ["position_id", "taken_at", "spot", "current_mid",
                   "pnl_pct", "delta", "margin_buffer", "signals"]
        placeholders = ",".join("?" * len(columns))
        col_str = ",".join(columns)
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO radar_snapshots({col_str}) VALUES({placeholders})",
                [snap.get(c) for c in columns],
            )
            return int(cur.lastrowid)

    def delete_radar_snapshots_for_position(self, position_id: int) -> int:
        """Remove all radar rows for a position (used before synthetic replay)."""
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM radar_snapshots WHERE position_id=?",
                (position_id,),
            )
        return int(cur.rowcount or 0)

    def get_candidate_by_id(self, candidate_id: int) -> Optional[Dict]:
        """Return a single candidate row by primary key."""
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        return _candidate_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Option pool
    # ------------------------------------------------------------------

    def upsert_option_pool_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"inserted": 0, "updated": 0, "upserted_ids": []}
        now = _now_utc()
        inserted = 0
        updated = 0
        upserted_ids: List[int] = []
        insert_cols = ",".join(_OPTION_POOL_COLUMNS)
        insert_placeholders = ",".join("?" for _ in _OPTION_POOL_COLUMNS)
        update_clause = ",".join(f"{col}=?" for col in _OPTION_POOL_METRIC_COLUMNS)
        with self._connect() as con:
            for row in rows:
                symbol = normalize_ticker_symbol(row.get("symbol"))
                if not symbol:
                    continue
                expiration = _date_text(row.get("expiration"))
                strike = row.get("strike")
                right = str(row.get("right") or "P").upper()
                existing = con.execute(
                    "SELECT * FROM option_pool WHERE symbol=? AND expiration=? "
                    "AND strike=? AND right=?",
                    (symbol, expiration, strike, right),
                ).fetchone()
                existing_dict = dict(existing) if existing else {}
                requested_status = row.get("status")
                quality_grade = row.get("quality_grade")
                if requested_status:
                    requested = str(requested_status).upper()
                    status = "ACTIVE" if existing and requested == "NEW" else requested
                elif quality_grade == "C":
                    status = "BLOCKED"
                elif existing:
                    status = "ACTIVE"
                else:
                    status = "NEW"
                prepared: Dict[str, Any] = {}
                for col in _OPTION_POOL_COLUMNS:
                    if col == "symbol":
                        prepared[col] = symbol
                    elif col == "expiration":
                        prepared[col] = expiration
                    elif col == "right":
                        prepared[col] = right
                    elif col == "first_seen_at":
                        prepared[col] = existing_dict.get(col) or row.get(col) or now
                    elif col == "last_seen_at":
                        prepared[col] = row.get(col) or now
                    elif col == "missed_scan_count":
                        prepared[col] = row.get(col, 0)
                    elif col == "status":
                        prepared[col] = status
                    elif col in {"quality_flags", "state_features"}:
                        value = row.get(col, existing_dict.get(col))
                        prepared[col] = value if isinstance(value, str) else _json_dumps_or_none(value)
                    else:
                        prepared[col] = row.get(col, existing_dict.get(col))
                if existing:
                    con.execute(
                        f"UPDATE option_pool SET {update_clause} WHERE id=?",
                        [prepared[col] for col in _OPTION_POOL_METRIC_COLUMNS] + [existing["id"]],
                    )
                    option_pool_id = int(existing["id"])
                    updated += 1
                else:
                    cur = con.execute(
                        f"INSERT INTO option_pool({insert_cols}) VALUES({insert_placeholders})",
                        [prepared[col] for col in _OPTION_POOL_COLUMNS],
                    )
                    option_pool_id = int(cur.lastrowid)
                    inserted += 1
                upserted_ids.append(option_pool_id)
        return {"inserted": inserted, "updated": updated, "upserted_ids": upserted_ids}

    def list_option_pool(
        self,
        *,
        symbol: Optional[str] = None,
        status: Any = None,
        quality_grade: Optional[str] = None,
        min_score: Optional[float] = None,
        min_dte: Optional[int] = None,
        max_dte: Optional[int] = None,
        entry_signal_status: Any = None,
        min_entry_signal_score: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if symbol:
            where.append("op.symbol=?")
            params.append(normalize_ticker_symbol(symbol))
        statuses = _normalize_status_filter(status)
        if statuses:
            where.append(f"op.status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if quality_grade:
            where.append("op.quality_grade=?")
            params.append(quality_grade)
        if min_score is not None:
            where.append("op.score>=?")
            params.append(min_score)
        if min_dte is not None:
            where.append("op.dte>=?")
            params.append(min_dte)
        if max_dte is not None:
            where.append("op.dte<=?")
            params.append(max_dte)
        signal_statuses = _normalize_status_filter(entry_signal_status)
        if signal_statuses:
            where.append(f"op.entry_signal_status IN ({','.join('?' for _ in signal_statuses)})")
            params.extend(signal_statuses)
        if min_entry_signal_score is not None:
            where.append("op.entry_signal_score>=?")
            params.append(min_entry_signal_score)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as con:
            rows = con.execute(
                "SELECT op.*, ow.id AS watch_id, ow.status AS watch_status "
                "FROM option_pool op "
                "LEFT JOIN option_watchlist ow ON ow.id = ("
                "  SELECT id FROM option_watchlist "
                "  WHERE option_pool_id=op.id AND status IN ('WATCHING','READY') "
                "  ORDER BY updated_at DESC LIMIT 1"
                f") {where_sql} "
                "ORDER BY COALESCE(op.score, -1) DESC, op.last_seen_at DESC",
                params,
            ).fetchall()
        return [_option_pool_from_row(row) for row in rows]

    def get_option_pool(self, option_pool_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT op.*, ow.id AS watch_id, ow.status AS watch_status "
                "FROM option_pool op "
                "LEFT JOIN option_watchlist ow ON ow.id = ("
                "  SELECT id FROM option_watchlist "
                "  WHERE option_pool_id=op.id AND status IN ('WATCHING','READY') "
                "  ORDER BY updated_at DESC LIMIT 1"
                ") WHERE op.id=?",
                (option_pool_id,),
            ).fetchone()
        return _option_pool_from_row(row) if row else None

    def mark_pool_missed_or_expired(
        self,
        seen_option_pool_ids: Optional[List[int]] = None,
        today: Optional[Any] = None,
    ) -> Dict[str, int]:
        today_text = _date_text(today or date.today())
        expired = 0
        missed = 0
        stale = 0
        with self._connect() as con:
            cur = con.execute(
                "UPDATE option_pool SET status='EXPIRED' "
                "WHERE expiration < ? AND status!='EXPIRED'",
                (today_text,),
            )
            expired = int(cur.rowcount or 0)
            if seen_option_pool_ids is None:
                return {"expired": expired, "missed": 0, "stale": 0}
            params: List[Any] = []
            not_seen_sql = ""
            if seen_option_pool_ids:
                not_seen_sql = f"AND id NOT IN ({','.join('?' for _ in seen_option_pool_ids)})"
                params.extend(seen_option_pool_ids)
            rows = con.execute(
                "SELECT id, missed_scan_count, status FROM option_pool "
                "WHERE expiration >= ? AND status IN ('NEW','ACTIVE','STALE') "
                f"{not_seen_sql}",
                [today_text, *params],
            ).fetchall()
            for row in rows:
                next_missed = int(row["missed_scan_count"] or 0) + 1
                next_status = "STALE" if next_missed >= 2 else row["status"]
                con.execute(
                    "UPDATE option_pool SET missed_scan_count=?, status=? WHERE id=?",
                    (next_missed, next_status, row["id"]),
                )
                missed += 1
                if next_status == "STALE" and row["status"] != "STALE":
                    stale += 1
        return {"expired": expired, "missed": missed, "stale": stale}

    # ------------------------------------------------------------------
    # Entry signals
    # ------------------------------------------------------------------

    def insert_entry_signal(self, signal: Dict[str, Any]) -> int:
        source = signal.get("source") or {}
        option_pool_id = source.get("option_pool_id")
        scan_run_id = source.get("scan_run_id")
        candidate_id = source.get("latest_candidate_id") or source.get("candidate_id")
        metrics = signal.get("metrics") or {}
        reasons = signal.get("reasons") or []
        blockers = signal.get("blockers") or []
        created_at = signal.get("generated_at") or _now_utc()
        status = str(signal.get("status") or "UNKNOWN").upper()
        decision_score = signal.get("decision_score")
        summary = signal.get("summary")
        signal_json = _json_dumps_or_none(signal) or "{}"
        with self._connect() as con:
            if option_pool_id is not None:
                con.execute(
                    "UPDATE entry_signals SET is_latest=0 WHERE option_pool_id=?",
                    (option_pool_id,),
                )
            cur = con.execute(
                "INSERT INTO entry_signals("
                "option_pool_id, scan_run_id, candidate_id, symbol, expiration, strike, right, "
                "status, decision_score, summary, metrics_json, reasons_json, blockers_json, "
                "signal_json, created_at, is_latest"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    option_pool_id,
                    scan_run_id,
                    candidate_id,
                    signal.get("symbol") or metrics.get("symbol"),
                    signal.get("expiration") or (metrics.get("risk") or {}).get("expiration"),
                    signal.get("strike") or (metrics.get("risk") or {}).get("strike"),
                    signal.get("right") or "P",
                    status,
                    decision_score,
                    summary,
                    _json_dumps_or_none(metrics),
                    _json_dumps_or_none(reasons),
                    _json_dumps_or_none(blockers),
                    signal_json,
                    created_at,
                ),
            )
            signal_id = int(cur.lastrowid)
            if option_pool_id is not None:
                con.execute(
                    "UPDATE option_pool SET latest_entry_signal_id=?, "
                    "entry_signal_status=?, entry_signal_score=?, entry_signal_summary=?, "
                    "entry_signal_generated_at=?, entry_signal_payload=? WHERE id=?",
                    (
                        signal_id,
                        status,
                        decision_score,
                        summary,
                        created_at,
                        signal_json,
                        option_pool_id,
                    ),
                )
        return signal_id

    def get_entry_signal(self, entry_signal_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM entry_signals WHERE id=?",
                (entry_signal_id,),
            ).fetchone()
        return _entry_signal_from_row(row) if row else None

    def get_latest_entry_signal(self, option_pool_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM entry_signals WHERE option_pool_id=? "
                "ORDER BY is_latest DESC, created_at DESC, id DESC LIMIT 1",
                (option_pool_id,),
            ).fetchone()
        return _entry_signal_from_row(row) if row else None

    def list_entry_signals(
        self,
        *,
        option_pool_id: Optional[int] = None,
        status: Any = None,
        latest_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if option_pool_id is not None:
            where.append("option_pool_id=?")
            params.append(option_pool_id)
        statuses = _normalize_status_filter(status)
        if statuses:
            where.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if latest_only:
            where.append("is_latest=1")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT * FROM entry_signals {where_sql} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        return [_entry_signal_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Option watchlist
    # ------------------------------------------------------------------

    def create_option_watch(self, watch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        option_pool_id = watch.get("option_pool_id")
        if option_pool_id is None or self.get_option_pool(int(option_pool_id)) is None:
            return None
        now = _now_utc()
        with self._connect() as con:
            existing = con.execute(
                "SELECT id FROM option_watchlist WHERE option_pool_id=? "
                "AND status IN ('WATCHING','READY') ORDER BY updated_at DESC LIMIT 1",
                (option_pool_id,),
            ).fetchone()
        if existing:
            updates = {k: v for k, v in watch.items() if k in _OPTION_WATCH_UPDATE_COLUMNS}
            if "status" not in updates:
                updates["status"] = "WATCHING"
            return self.update_option_watch(int(existing["id"]), updates)
        row = {
            "option_pool_id": option_pool_id,
            "status": str(watch.get("status") or "WATCHING").upper(),
            "watch_reason": watch.get("watch_reason"),
            "ignore_reason": watch.get("ignore_reason"),
            "target_premium": watch.get("target_premium"),
            "target_score": watch.get("target_score"),
            "target_margin_buffer": watch.get("target_margin_buffer"),
            "notes": watch.get("notes"),
            "created_at": watch.get("created_at") or now,
            "updated_at": watch.get("updated_at") or now,
            "last_evaluated_at": watch.get("last_evaluated_at"),
            "last_signal": _json_dumps_or_none(watch.get("last_signal")),
        }
        columns = list(row.keys())
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO option_watchlist({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)})",
                [row[col] for col in columns],
            )
            watch_id = int(cur.lastrowid)
        return self.get_option_watch(watch_id)

    def list_option_watches(self, status: Any = None) -> List[Dict[str, Any]]:
        statuses = _normalize_status_filter(status)
        where = ""
        params: List[Any] = []
        if statuses:
            where = f"WHERE ow.status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        with self._connect() as con:
            rows = con.execute(
                f"{_OPTION_WATCH_SELECT} {where} "
                "ORDER BY CASE ow.status WHEN 'READY' THEN 0 WHEN 'WATCHING' THEN 1 ELSE 2 END, "
                "ow.updated_at DESC",
                params,
            ).fetchall()
        return [_option_watch_from_row(row) for row in rows]

    def get_option_watch(self, watch_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                f"{_OPTION_WATCH_SELECT} WHERE ow.id=?",
                (watch_id,),
            ).fetchone()
        return _option_watch_from_row(row) if row else None

    def update_option_watch(self, watch_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prepared: Dict[str, Any] = {}
        for key, value in updates.items():
            if key not in _OPTION_WATCH_UPDATE_COLUMNS:
                continue
            if key == "status" and value is not None:
                prepared[key] = str(value).upper()
            elif key == "last_signal":
                prepared[key] = _json_dumps_or_none(value)
            else:
                prepared[key] = value
        prepared["updated_at"] = _now_utc()
        if prepared:
            with self._connect() as con:
                con.execute(
                    f"UPDATE option_watchlist SET {','.join(f'{k}=?' for k in prepared)} "
                    "WHERE id=?",
                    [*prepared.values(), watch_id],
                )
        return self.get_option_watch(watch_id)

    def ignore_option_watch(self, watch_id: int, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.update_option_watch(
            watch_id,
            {"status": "IGNORED", "ignore_reason": reason},
        )

    def mark_option_watch_opened(self, watch_id: int, last_signal: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        return self.update_option_watch(
            watch_id,
            {"status": "OPENED", "last_signal": last_signal},
        )

    def persist_option_watch_evaluation(
        self,
        watch_id: int,
        *,
        status: Optional[str] = None,
        last_signal: Optional[Dict[str, Any]] = None,
        evaluated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        updates: Dict[str, Any] = {"last_evaluated_at": evaluated_at or _now_utc()}
        if status:
            updates["status"] = status
        if last_signal is not None:
            updates["last_signal"] = last_signal
        return self.update_option_watch(watch_id, updates)

    def mark_option_watches_expired(self, today: Optional[Any] = None) -> int:
        today_text = _date_text(today or date.today())
        with self._connect() as con:
            cur = con.execute(
                "UPDATE option_watchlist SET status='EXPIRED', updated_at=? "
                "WHERE status IN ('WATCHING','READY') "
                "AND option_pool_id IN (SELECT id FROM option_pool WHERE expiration < ?)",
                (_now_utc(), today_text),
            )
        return int(cur.rowcount or 0)

    def get_mae_mfe_for_positions(self, position_ids: List[int]) -> Dict[int, Dict]:
        """
        For each position_id, compute MAE (min pnl_pct) and MFE (max pnl_pct)
        from radar_snapshots. Returns {position_id: {"mae": float, "mfe": float}}.
        """
        if not position_ids:
            return {}
        placeholders = ",".join("?" * len(position_ids))
        with self._connect() as con:
            rows = con.execute(
                f"SELECT position_id, MIN(pnl_pct) AS mae, MAX(pnl_pct) AS mfe "
                f"FROM radar_snapshots WHERE position_id IN ({placeholders}) "
                f"GROUP BY position_id",
                position_ids,
            ).fetchall()
        return {row["position_id"]: {"mae": row["mae"], "mfe": row["mfe"]} for row in rows}

    def get_intraday_bs_mae_mfe_for_positions(self, position_ids: List[int]) -> Dict[int, Dict]:
        """
        For each position_id that has open_snapshot.intraday_bs with
        mae_pnl_pct / mfe_pnl_pct, return those values.
        Falls back gracefully when open_snapshot is absent or unparseable.
        Returns {position_id: {"mae": float, "mfe": float}}.
        """
        if not position_ids:
            return {}
        placeholders = ",".join("?" * len(position_ids))
        with self._connect() as con:
            rows = con.execute(
                f"SELECT id, open_snapshot FROM positions WHERE id IN ({placeholders})",
                position_ids,
            ).fetchall()
        result: Dict[int, Dict] = {}
        for row in rows:
            raw = row["open_snapshot"]
            if not raw:
                continue
            try:
                snap = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            ibs = snap.get("intraday_bs") if isinstance(snap, dict) else None
            if not isinstance(ibs, dict):
                continue
            mae = ibs.get("mae_pnl_pct")
            mfe = ibs.get("mfe_pnl_pct")
            if mae is not None or mfe is not None:
                result[row["id"]] = {"mae": mae, "mfe": mfe}
        return result

    def save_open_snapshot(self, position_id: int, snapshot: Dict[str, Any]) -> None:
        """Persist the entry environment snapshot for a position."""
        with self._connect() as con:
            con.execute(
                "UPDATE positions SET open_snapshot=? WHERE id=?",
                (json.dumps(snapshot, ensure_ascii=False), position_id),
            )

    def get_open_snapshot(self, position_id: int) -> Optional[Dict]:
        """Return the parsed open_snapshot dict for a position, or None."""
        with self._connect() as con:
            row = con.execute(
                "SELECT open_snapshot FROM positions WHERE id=?", (position_id,)
            ).fetchone()
        if row is None or row["open_snapshot"] is None:
            return None
        try:
            return json.loads(row["open_snapshot"])
        except (json.JSONDecodeError, TypeError):
            return None

    def list_radar_snapshots(self, position_id: int, limit: int = 100) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM radar_snapshots WHERE position_id=? "
                "ORDER BY taken_at DESC LIMIT ?",
                (position_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Exit signals and position actions
    # ------------------------------------------------------------------

    def insert_exit_signal(
        self,
        signal: Dict[str, Any],
        radar_snapshot_id: Optional[int] = None,
    ) -> int:
        source = signal.get("source") or {}
        position_id = signal.get("position_id") or source.get("position_id")
        if position_id is None:
            raise ValueError("exit signal requires position_id")
        snapshot_id = radar_snapshot_id
        if snapshot_id is None:
            snapshot_id = signal.get("radar_snapshot_id") or source.get("radar_snapshot_id")
        action = signal.get("action") or "UNKNOWN"
        severity = signal.get("severity") or "info"
        urgency_score = signal.get("urgency_score")
        if urgency_score is None:
            urgency_score = signal.get("score")
        suggested_close_reason = signal.get("suggested_close_reason")
        summary = signal.get("summary")
        created_at = signal.get("generated_at") or signal.get("created_at") or _now_utc()
        signal_json = _json_dumps_or_none(signal) or "{}"
        with self._connect() as con:
            con.execute(
                "UPDATE exit_signals SET is_latest=0 WHERE position_id=?",
                (position_id,),
            )
            cur = con.execute(
                "INSERT INTO exit_signals("
                "position_id, radar_snapshot_id, action, severity, urgency_score, "
                "suggested_close_reason, summary, signal_json, created_at, is_latest"
                ") VALUES(?,?,?,?,?,?,?,?,?,1)",
                (
                    position_id,
                    snapshot_id,
                    action,
                    severity,
                    urgency_score,
                    suggested_close_reason,
                    summary,
                    signal_json,
                    created_at,
                ),
            )
            signal_id = int(cur.lastrowid)
            con.execute(
                "UPDATE positions SET latest_exit_signal_id=?, exit_signal_action=?, "
                "exit_signal_severity=?, exit_signal_score=?, exit_signal_summary=?, "
                "exit_signal_generated_at=?, exit_signal_payload=? WHERE id=?",
                (
                    signal_id,
                    action,
                    severity,
                    urgency_score,
                    summary,
                    created_at,
                    signal_json,
                    position_id,
                ),
            )
        return signal_id

    def get_exit_signal(self, exit_signal_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM exit_signals WHERE id=?",
                (exit_signal_id,),
            ).fetchone()
        return _exit_signal_from_row(row) if row else None

    def get_latest_exit_signal(self, position_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM exit_signals WHERE position_id=? "
                "ORDER BY is_latest DESC, created_at DESC, id DESC LIMIT 1",
                (position_id,),
            ).fetchone()
        return _exit_signal_from_row(row) if row else None

    def list_exit_signals(self, position_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM exit_signals WHERE position_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (position_id, int(limit)),
            ).fetchall()
        return [_exit_signal_from_row(row) for row in rows]

    def insert_position_action_log(
        self,
        position_id: int,
        action_type: str,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        exit_signal_id: Optional[int] = None,
        created_at: Optional[str] = None,
    ) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO position_action_logs("
                "position_id, exit_signal_id, action_type, reason, notes, created_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    position_id,
                    exit_signal_id,
                    action_type,
                    reason,
                    notes,
                    created_at or _now_utc(),
                ),
            )
            return int(cur.lastrowid)

    def list_position_action_logs(self, position_id: int) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM position_action_logs WHERE position_id=? "
                "ORDER BY created_at DESC, id DESC",
                (position_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_position_action_logs_by_position_ids(
        self,
        position_ids: List[int],
    ) -> Dict[int, int]:
        ids = [int(pid) for pid in position_ids if pid is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as con:
            rows = con.execute(
                "SELECT position_id, COUNT(*) AS n FROM position_action_logs "
                f"WHERE position_id IN ({placeholders}) GROUP BY position_id",
                ids,
            ).fetchall()
        return {int(row["position_id"]): int(row["n"]) for row in rows}

    def save_position_close_snapshot(
        self,
        position_id: int,
        snapshot: Any,
        close_signal_id: Optional[int] = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE positions SET close_snapshot=?, close_signal_id=? WHERE id=?",
                (_json_dumps_or_none(snapshot), close_signal_id, position_id),
            )

    def exit_signal_event_exists(
        self,
        position_id: int,
        action: str,
        suggested_close_reason: Optional[str],
        severity: str,
    ) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM exit_signals WHERE position_id=? "
                "AND action=? AND severity=? "
                "AND ((? IS NULL AND suggested_close_reason IS NULL) "
                "OR suggested_close_reason=?) "
                "LIMIT 1",
                (position_id, action, severity, suggested_close_reason, suggested_close_reason),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def insert_event(
        self,
        level: str,
        category: str,
        title: str,
        payload: Optional[Dict] = None,
    ) -> int:
        payload_str = json.dumps(payload) if payload else None
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO events(created_at, level, category, title, payload) "
                "VALUES(?,?,?,?,?)",
                (_now_utc(), level, category, title, payload_str),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def list_unread_events(self, limit: int = 50) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM events WHERE ack_at IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            result.append(d)
        return result

    def list_events(self, limit: int = 50) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            result.append(d)
        return result

    def ack_event(self, event_id: int) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE events SET ack_at=? WHERE id=?",
                (_now_utc(), event_id),
            )

    def get_event(self, event_id: int) -> Optional[Dict]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        return d

    def event_signal_exists(self, position_id: int, signal_type: str) -> bool:
        """Return True if an unacked event for this position+signal already exists."""
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM events WHERE ack_at IS NULL "
                "AND json_extract(payload, '$.position_id')=? "
                "AND json_extract(payload, '$.signal_type')=?",
                (position_id, signal_type),
            ).fetchone()
        return row is not None


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
