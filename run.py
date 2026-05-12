from __future__ import annotations

"""
期权卖方助手启动入口

用法：
  python3 run.py          # 正常启动（安装依赖、初始化 DB、启动 worker + server）
  SKIP_INSTALL=1 python3 run.py  # 跳过 pip install（加速启动）
"""

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
VENV_CFG = PROJECT_ROOT / ".venv" / "pyvenv.cfg"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
DB_PATH = PROJECT_ROOT / "data" / "options.db"
PID_FILE = PROJECT_ROOT / "data" / "worker.pid"
SERVER_URL = "http://127.0.0.1:7000"

_worker_proc: "subprocess.Popen | None" = None


def _check_python_version() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 9):
        print(f"[run] 警告: Python {major}.{minor} 低于推荐版本 3.11+，部分功能可能受影响")
    else:
        print(f"[run] Python {major}.{minor}")


def _install_deps() -> None:
    if os.environ.get("SKIP_INSTALL") == "1":
        print("[run] 跳过 pip install（SKIP_INSTALL=1）")
        return
    print("[run] 检查并安装依赖...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS), "-q"],
        check=True,
    )
    print("[run] 依赖检查完毕")


def _init_db() -> None:
    print(f"[run] 初始化数据库: {DB_PATH}")
    from app.db.init_db import init_database
    init_database(DB_PATH)
    print("[run] 数据库就绪")


def _start_worker() -> subprocess.Popen:
    global _worker_proc
    print("[run] 启动 worker (端口 7001)...")
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "worker.py")],
        cwd=str(PROJECT_ROOT),
    )
    _worker_proc = proc
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    print(f"[run] worker PID={proc.pid} (写入 {PID_FILE})")
    return proc


def _kill_worker() -> None:
    global _worker_proc
    if _worker_proc is not None:
        print(f"\n[run] 终止 worker (PID={_worker_proc.pid})...")
        try:
            _worker_proc.terminate()
            _worker_proc.wait(timeout=5)
        except Exception:
            _worker_proc.kill()
        finally:
            _worker_proc = None
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass


def _signal_handler(sig, frame):
    _kill_worker()
    sys.exit(0)


def main():
    _check_python_version()
    _install_deps()
    _init_db()

    worker_proc = _start_worker()
    atexit.register(_kill_worker)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Brief pause to let worker Flask start
    time.sleep(1.5)

    print(f"[run] 启动 server (端口 7000)...")
    print(f"[run] 打开浏览器访问: {SERVER_URL}")

    # Try to open browser (non-blocking, failure is ok)
    try:
        import webbrowser
        webbrowser.open(SERVER_URL)
    except Exception:
        pass

    # Run server in foreground
    try:
        from server import create_app
        app = create_app(DB_PATH)
        app.run(host="127.0.0.1", port=7000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        _kill_worker()


if __name__ == "__main__":
    main()
