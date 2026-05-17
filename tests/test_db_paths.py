from __future__ import annotations

import os

from app.db.paths import PROJECT_ROOT, options_db_path


def test_options_db_path_anchored_not_cwd_relative(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_DB_PATH", raising=False)
    cwd_before = os.getcwd()
    try:
        os.chdir(tmp_path)
        p = options_db_path()
        assert p.name == "options.db"
        assert p.parent.resolve() == (PROJECT_ROOT / "data").resolve()
    finally:
        os.chdir(cwd_before)
