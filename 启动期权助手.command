#!/bin/bash
# 双击运行：校验并释放本机 7000/7001，再在仓库根目录启动 python3 run.py
#
# （7000/7001 上可能是旧 server.py / worker.py / 未退出的 run.py；先停再启，避免连到陈旧进程）

FOSS_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$FOSS_ROOT" || {
    echo "[错误] 无法进入目录: $FOSS_ROOT" >&2
    exit 1
}

echo "=========================================="
echo "  FOSS 期权助手  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  目录: $FOSS_ROOT"
echo "=========================================="

if ! command -v lsof >/dev/null 2>&1; then
    echo "[警告] 未找到 lsof，无法自动检查端口 7000/7001（将直接尝试启动）" >&2
else
    for PORT in 7000 7001; do
        for _attempt in 1 2 3; do
            STOP_PIDS=$(lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)
            if [ -z "$STOP_PIDS" ]; then
                echo "[校验] 端口 $PORT 空闲"
                break
            fi
            echo "[校验] 发现占用端口 $PORT（可能是旧 server.py / worker.py / run.py）：$STOP_PIDS"
            kill $STOP_PIDS 2>/dev/null || true
            sleep 1
        done
        STILL=$(lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)
        if [ -n "$STILL" ]; then
            echo "[校验] SIGTERM 未释放端口 $PORT，强制结束: $STILL"
            kill -9 $STILL 2>/dev/null || true
            sleep 0.5
        fi
        if lsof -nP -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
            echo "[错误] 端口 $PORT 仍被占用，请先手动排查： lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
            exit 1
        fi
    done
fi

if [ ! -f "$FOSS_ROOT/run.py" ]; then
    echo "[错误] 未找到 run.py: $FOSS_ROOT/run.py" >&2
    exit 1
fi

echo "[启动] python3 run.py"
exec python3 "$FOSS_ROOT/run.py"
