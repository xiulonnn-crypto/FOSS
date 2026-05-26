from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).parent / "schema.sql"
_DEFAULTS = Path(__file__).parent / "settings_default.json"

_OPTION_POOL_ENTRY_SIGNAL_COLUMNS = (
    ("latest_entry_signal_id", "INTEGER"),
    ("entry_signal_status", "TEXT"),
    ("entry_signal_score", "INTEGER"),
    ("entry_signal_summary", "TEXT"),
    ("entry_signal_generated_at", "TEXT"),
    ("entry_signal_payload", "TEXT"),
    ("state_features", "TEXT"),
)

_POSITION_EXIT_SIGNAL_COLUMNS = (
    ("latest_exit_signal_id", "INTEGER"),
    ("exit_signal_action", "TEXT"),
    ("exit_signal_severity", "TEXT"),
    ("exit_signal_score", "REAL"),
    ("exit_signal_summary", "TEXT"),
    ("exit_signal_generated_at", "TEXT"),
    ("exit_signal_payload", "TEXT"),
    ("close_signal_id", "INTEGER"),
    ("close_snapshot", "TEXT"),
)


def _add_column_if_missing(
    con: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _add_option_pool_entry_signal_columns(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "option_pool"):
        return
    for column, definition in _OPTION_POOL_ENTRY_SIGNAL_COLUMNS:
        _add_column_if_missing(con, "option_pool", column, definition)


def _add_position_exit_signal_columns(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "positions"):
        return
    for column, definition in _POSITION_EXIT_SIGNAL_COLUMNS:
        _add_column_if_missing(con, "positions", column, definition)


def _fill_missing(base: dict, defaults: dict) -> bool:
    changed = False
    for key, value in defaults.items():
        if key not in base:
            base[key] = value
            changed = True
        elif isinstance(base[key], dict) and isinstance(value, dict):
            changed = _fill_missing(base[key], value) or changed
    return changed


def _ensure_default_settings(con: sqlite3.Connection) -> None:
    defaults = json.loads(_DEFAULTS.read_text())
    row = con.execute("SELECT value FROM settings WHERE key='app'").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            ("app", json.dumps(defaults, ensure_ascii=False, indent=2)),
        )
        return
    try:
        current = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    if _fill_missing(current, defaults):
        con.execute(
            "UPDATE settings SET value=? WHERE key='app'",
            (json.dumps(current, ensure_ascii=False, indent=2),),
        )


def init_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        # Existing Phase 2 databases already have option_pool but not the
        # Phase 3 entry-signal columns.  Add them before replaying schema.sql
        # because schema.sql creates an index on those columns.
        _add_option_pool_entry_signal_columns(con)
        con.executescript(_SCHEMA.read_text())
        con.execute("PRAGMA journal_mode=WAL")
        _ensure_default_settings(con)
        _add_column_if_missing(con, "watchlist", "pool_status", "TEXT")
        _add_column_if_missing(con, "watchlist", "tags", "TEXT")
        _add_column_if_missing(con, "watchlist", "notes", "TEXT")
        _add_column_if_missing(con, "watchlist", "last_scanned_at", "TEXT")
        _add_column_if_missing(con, "watchlist", "last_candidate_count", "INTEGER")
        _add_column_if_missing(con, "watchlist", "last_pool_summary", "TEXT")
        _add_column_if_missing(con, "positions", "open_snapshot", "TEXT")
        _add_column_if_missing(con, "scan_runs", "diagnostics", "TEXT")
        _add_column_if_missing(con, "candidates", "quality_grade", "TEXT")
        _add_column_if_missing(con, "candidates", "quality_score", "INTEGER")
        _add_column_if_missing(con, "candidates", "quality_flags", "TEXT")
        _add_column_if_missing(con, "candidates", "quote_age_seconds", "INTEGER")
        _add_column_if_missing(con, "candidates", "greeks_source", "TEXT")
        _add_column_if_missing(con, "candidates", "iv_rank_source", "TEXT")
        _add_column_if_missing(con, "candidates", "state_features", "TEXT")
        _add_option_pool_entry_signal_columns(con)
        _add_position_exit_signal_columns(con)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/options.db")
    init_database(path)
    print(f"Database initialised at {path}")
