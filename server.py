from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from flask_cors import CORS

from app.db.init_db import init_database
from app.db.repo import Repo

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("server")

DB_PATH = Path("data/options.db")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 7000))
FRONTEND_DIR = Path(__file__).parent / "frontend"


def create_app(db_path: Path = DB_PATH) -> Flask:
    app = Flask(__name__, static_folder=None)
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

    app.register_blueprint(bp_internal)
    app.register_blueprint(bp_events)
    app.register_blueprint(bp_settings)
    app.register_blueprint(bp_scan)
    app.register_blueprint(bp_positions)

    # Serve frontend static files
    @app.route("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(str(FRONTEND_DIR), filename)

    log.info("server: app created, DB=%s", db_path)
    return app


if __name__ == "__main__":
    application = create_app()
    log.info("server: listening on http://127.0.0.1:%d", SERVER_PORT)
    application.run(host="127.0.0.1", port=SERVER_PORT, debug=False, use_reloader=False)
