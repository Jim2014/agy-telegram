#!/usr/bin/env bash
# Restart the agy-telegram gateway using a PID file for reliable stop.
# Usage:
#   ./restart.sh          restart (or start) the gateway
#   ./restart.sh stop     stop the gateway
#   ./restart.sh status   show whether it is running
#   ./restart.sh --check  config validation only
set -u
cd "$(dirname "$0")" || exit 1
PIDFILE="gateway.pid"

ensure_stopped() {
    local pid=""
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE" 2>/dev/null || true)
    fi

    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "🛑 Stopping gateway (PID $pid)..."
        kill "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
        fi
    else
        # No pidfile (or stale): fall back to pattern match scoped to this project's venv.
        pkill -f "venv/bin/python main.py" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PIDFILE"
}

case "${1:-}" in
    --check)
        ./venv/bin/python main.py --check-config
        exit $?
        ;;
    stop)
        ensure_stopped
        echo "🛑 Gateway stopped."
        exit 0
        ;;
    status)
        if [ -f "$PIDFILE" ]; then
            pid=$(cat "$PIDFILE" 2>/dev/null || true)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                echo "✅ Running (PID $pid)"
            else
                echo "⚠️  Stale pidfile (PID $pid not running)"
            fi
        else
            echo "❌ Not running (no $PIDFILE)"
        fi
        exit 0
        ;;
esac

ensure_stopped

echo "🚀 Starting gateway..."
nohup ./venv/bin/python main.py >/dev/null 2>&1 &
echo $! > "$PIDFILE"
sleep 2

if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "✅ Gateway restarted (PID $(cat "$PIDFILE"))"
    echo "   Log: $(pwd)/gateway.log   (tail -f -n 50 gateway.log)"
else
    echo "❌ Gateway failed to start — check $(pwd)/gateway.log"
    exit 1
fi