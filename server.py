from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.routing import ValidationError
from werkzeug.routing.converters import PathConverter

from app.db.init_db import init_database
from app.db.paths import options_db_path
from app.db.repo import Repo

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("server")

DB_PATH = options_db_path()
SERVER_PORT = int(os.environ.get("SERVER_PORT", 7000))
FRONTEND_DIR = Path(__file__).parent / "frontend"


class NonApiFrontendPath(PathConverter):
    """Path segments under frontend/SPA; rejects `api` and `api/...` so `/api/*` never hits catch-all."""

    def to_python(self, value: str) -> str:
        if value == "api" or value.startswith("api/"):
            raise ValidationError()
        return super().to_python(value)


def create_app(db_path: Path = DB_PATH) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.url_map.converters["non_api_path"] = NonApiFrontendPath
    CORS(app)

    # Ensure DB
    init_database(db_path)
    repo = Repo(db_path)
    app.config["REPO"] = repo
    app.config["DB_PATH"] = db_path

    # Register blueprints
    from app.api.internal_notify import bp_internal
    from app.api.routes_events import bp_events
    from app.api.routes_settings import bp_settings
    from app.api.routes_scan import bp_scan
    from app.api.routes_positions import bp_positions
    from app.api.routes_pool import bp_pool

    app.register_blueprint(bp_internal)
    app.register_blueprint(bp_events)
    app.register_blueprint(bp_settings)
    app.register_blueprint(bp_scan)
    app.register_blueprint(bp_positions)
    app.register_blueprint(bp_pool)

    from app.api.routes_review import bp_review
    app.register_blueprint(bp_review)

    # POST/PUT/PATCH/DELETE under /api/... must never fall through to the GET-only
    # SPA static catch-all — Werkzeug can match that rule and return 405 without
    # running NonApiFrontendPath validation. Unknown API writes get 404 + hint.
    @app.route(
        "/api/<path:unknown_api_path>",
        methods=["POST", "PUT", "PATCH", "DELETE"],
    )
    def api_unmatched_write(unknown_api_path: str):
        _ = unknown_api_path
        return jsonify({
            "error": "unknown_api_route",
            "path": request.path,
            "hint": "接口不存在，或服务进程仍为旧版本未加载新路由；若刚更新代码，请重启监听本端口的进程（如 run.py）后再试。",
        }), 404

    # Serve frontend static files
    @app.route("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/<non_api_path:filename>")
    def static_files(filename):
        return send_from_directory(str(FRONTEND_DIR), filename)

    log.info("server: app created, DB=%s", db_path)
    return app


if __name__ == "__main__":
    application = create_app()
    log.info("server: listening on http://127.0.0.1:%d", SERVER_PORT)
    application.run(host="127.0.0.1", port=SERVER_PORT, debug=False, use_reloader=False)
