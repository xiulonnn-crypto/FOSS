"""Single place for project-root–anchored paths (avoid cwd-dependent relative paths)."""

from __future__ import annotations

import os
from pathlib import Path

# app/db/paths.py -> parent -> app/db, app, repo root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def options_db_path() -> Path:
    override = os.environ.get("OPTIONS_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (PROJECT_ROOT / "data" / "options.db").resolve()


def snapshots_dir() -> Path:
    return (PROJECT_ROOT / "data" / "snapshots").resolve()
