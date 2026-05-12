from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    # Watchlist
    # ------------------------------------------------------------------

    def list_watchlist(self) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT symbol, added_at, earnings_at, enabled FROM watchlist ORDER BY symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_symbols(self, symbols: List[str]) -> None:
        now = _now_utc()
        with self._connect() as con:
            for sym in symbols:
                con.execute(
                    "INSERT INTO watchlist(symbol, added_at) VALUES(?, ?) "
                    "ON CONFLICT(symbol) DO NOTHING",
                    (sym.upper().strip(), now),
                )

    def set_earnings(self, symbol: str, earnings_at: Optional[str]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE watchlist SET earnings_at=? WHERE symbol=?",
                (earnings_at, symbol.upper()),
            )

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
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE scan_runs SET finished_at=?, candidate_count=?, snapshot_path=? "
                "WHERE id=?",
                (_now_utc(), candidate_count, snapshot_path, run_id),
            )

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
        ]
        placeholders = ",".join("?" * len(columns))
        col_str = ",".join(columns)
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO candidates({col_str}) VALUES({placeholders})",
                [[row.get(c) for c in columns] for row in rows],
            )

    def list_candidates(self, scan_run_id: int, limit: int = 20) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM candidates WHERE scan_run_id=? ORDER BY score DESC LIMIT ?",
                (scan_run_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

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
        return [dict(r) for r in rows]

    def get_position(self, position_id: int) -> Optional[Dict]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM positions WHERE id=?", (position_id,)
            ).fetchone()
        return dict(row) if row else None

    def close_position(
        self,
        position_id: int,
        state: str,
        close_premium: Optional[float],
        close_reason: str,
        realized_pnl: float,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE positions SET state=?, close_at=?, close_premium=?, "
                "close_reason=?, realized_pnl=? WHERE id=?",
                (state, _now_utc(), close_premium, close_reason, realized_pnl, position_id),
            )

    # ------------------------------------------------------------------
    # Radar snapshots
    # ------------------------------------------------------------------

    def insert_radar_snapshot(self, snap: Dict[str, Any]) -> None:
        columns = ["position_id", "taken_at", "spot", "current_mid",
                   "pnl_pct", "delta", "margin_buffer", "signals"]
        placeholders = ",".join("?" * len(columns))
        col_str = ",".join(columns)
        with self._connect() as con:
            con.execute(
                f"INSERT INTO radar_snapshots({col_str}) VALUES({placeholders})",
                [snap.get(c) for c in columns],
            )

    def list_radar_snapshots(self, position_id: int, limit: int = 100) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM radar_snapshots WHERE position_id=? "
                "ORDER BY taken_at DESC LIMIT ?",
                (position_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

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
