from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).parent / "schema.sql"
_DEFAULTS = Path(__file__).parent / "settings_default.json"


def init_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_SCHEMA.read_text())
        con.execute("PRAGMA journal_mode=WAL")
        row = con.execute("SELECT 1 FROM settings WHERE key='app'").fetchone()
        if row is None:
            defaults = _DEFAULTS.read_text()
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?)", ("app", defaults))
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/options.db")
    init_database(path)
    print(f"Database initialised at {path}")
