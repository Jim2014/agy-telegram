#!/usr/bin/env bash
# Restart the agy-telegram gateway using a PID file for reliable stop.
# Usage:
#   ./restart.sh          restart (or start) the gateway
#   ./restart.sh stop     stop the gateway
#   ./restart.sh status   show whether it is running
#   ./restart.sh --check  config validation only
set -u
cd "$(dirname "$0")" || exit 1
mkdir -p runtime
PIDFILE="runtime/gateway.pid"
LEGACY_PIDFILE="gateway.pid"

# Determine python command (venv, conda, or system python)
if [ -x "./venv/bin/python" ]; then
    PY_BIN="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
else
    PY_BIN="python"
fi

ensure_stopped() {
    local pid=""
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE" 2>/dev/null || true)
    elif [ -f "$LEGACY_PIDFILE" ]; then
        pid=$(cat "$LEGACY_PIDFILE" 2>/dev/null || true)
    fi

    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "🛑 Stopping gateway (PID $pid)..."
        kill "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
        fi
    else
        # No pidfile (or stale): fall back to pattern match scoped to this project.
        pkill -f "python.*main.py" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PIDFILE" "$LEGACY_PIDFILE"
}

case "${1:-}" in
    --check)
        $PY_BIN main.py --check-config
        exit $?
        ;;
    stop)
        ensure_stopped
        echo "🛑 Gateway stopped."
        exit 0
        ;;
    status)
        active_pid=""
        if [ -f "$PIDFILE" ]; then
            active_pid=$(cat "$PIDFILE" 2>/dev/null || true)
        elif [ -f "$LEGACY_PIDFILE" ]; then
            active_pid=$(cat "$LEGACY_PIDFILE" 2>/dev/null || true)
        fi

        if [ -n "$active_pid" ]; then
            if kill -0 "$active_pid" 2>/dev/null; then
                echo "✅ Running (PID $active_pid)"
            else
                echo "⚠️  Stale pidfile (PID $active_pid not running)"
            fi
        else
            echo "❌ Not running (no $PIDFILE)"
        fi
        exit 0
        ;;
esac

ensure_stopped

echo "🚀 Starting gateway..."
nohup $PY_BIN main.py >/dev/null 2>&1 &
echo $! > "$PIDFILE"
sleep 2

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "✅ Gateway restarted (PID $(cat "$PIDFILE"))"
    echo "   Log: $(pwd)/runtime/gateway.log   (tail -f -n 50 runtime/gateway.log)"
else
    echo "❌ Gateway failed to start — check $(pwd)/runtime/gateway.log"
    exit 1
fi