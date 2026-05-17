from __future__ import annotations

import logging
import sys
import threading

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request

from app.db.init_db import init_database
from app.db.paths import options_db_path
from app.db.repo import Repo
from app.jobs.scheduler_config import build_scheduler, register_jobs
from app.jobs.job_screener import run_option_pool_maintenance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("worker")

DB_PATH = options_db_path()
WORKER_HOST = "127.0.0.1"
WORKER_PORT = 7001

# Global scheduler instance so /reload can reach it
_scheduler = None
_repo = None


def create_internal_app() -> Flask:
    """Tiny Flask app for internal management endpoints only."""
    app = Flask("worker_internal")
    app.config["TESTING"] = False

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify({"ok": True, "jobs": len(_scheduler.get_jobs()) if _scheduler else 0})

    @app.route("/reload", methods=["POST"])
    def reload():
        if _scheduler is None or _repo is None:
            return jsonify({"error": "scheduler not ready"}), 503
        try:
            register_jobs(_scheduler, _repo)
            log.info("worker: jobs reloaded via /reload")
            return jsonify({"ok": True})
        except Exception as exc:
            log.exception("worker: reload failed")
            return jsonify({"error": str(exc)}), 500

    return app


def main():
    global _scheduler, _repo

    # Ensure DB exists
    init_database(DB_PATH)
    _repo = Repo(DB_PATH)

    # Build and start scheduler
    try:
        result = run_option_pool_maintenance(_repo)
        log.info("worker: option pool maintenance completed: %s", result)
    except Exception as exc:
        log.warning("worker: option pool maintenance skipped: %s", exc)

    _scheduler = build_scheduler(_repo)
    register_jobs(_scheduler, _repo)
    _scheduler.start()
    log.info("worker: scheduler started with %d jobs", len(_scheduler.get_jobs()))

    # Start internal Flask in a daemon thread
    internal_app = create_internal_app()
    flask_thread = threading.Thread(
        target=lambda: internal_app.run(
            host=WORKER_HOST,
            port=WORKER_PORT,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
    )
    flask_thread.start()
    log.info("worker: internal HTTP listening on %s:%d", WORKER_HOST, WORKER_PORT)

    # Block main thread — keep alive until Ctrl+C
    try:
        flask_thread.join()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker: shutting down")
        _scheduler.shutdown(wait=False)
        sys.exit(0)


if __name__ == "__main__":
    main()
