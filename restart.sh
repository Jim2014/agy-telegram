#!/usr/bin/env bash
# Restart the agy-telegram gateway using a PID file for reliable stop.
# Usage:
#   ./restart.sh          restart (or start) the gateway
#   ./restart.sh stop     stop the gateway (and watchdog if running)
#   ./restart.sh status   show whether gateway and watchdog are running
#   ./restart.sh watch    start the watchdog auto-restart supervisor
#   ./restart.sh stop-watch stop only the watchdog supervisor
#   ./restart.sh --detached   schedule kill+restart in a detached background
#                             process and return immediately (safe to call
#                             from a bot-spawned agy that is a child of the
#                             gateway: the gateway is never killed in-place)
#   ./restart.sh --check  config validation only
set -u
cd "$(dirname "$0")" || exit 1
mkdir -p runtime
PIDFILE="runtime/gateway.pid"
LEGACY_PIDFILE="gateway.pid"
WATCHDOG_PIDFILE="runtime/watchdog.pid"

# Determine python command (venv, conda, or system python)
if [ -x "./venv/bin/python" ]; then
    PY_BIN="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
else
    PY_BIN="python"
fi

ensure_watchdog_stopped() {
    local wpid=""
    if [ -f "$WATCHDOG_PIDFILE" ]; then
        wpid=$(cat "$WATCHDOG_PIDFILE" 2>/dev/null || true)
    fi
    if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
        echo "🛑 Stopping watchdog supervisor (PID $wpid)..."
        kill "$wpid" 2>/dev/null
        sleep 1
        if kill -0 "$wpid" 2>/dev/null; then
            kill -9 "$wpid" 2>/dev/null
        fi
    else
        pkill -f "runtime/watchdog.sh" 2>/dev/null || true
    fi
    rm -f "$WATCHDOG_PIDFILE"
}

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
        ensure_watchdog_stopped
        ensure_stopped
        echo "🛑 Gateway and watchdog stopped."
        exit 0
        ;;
    stop-watch)
        ensure_watchdog_stopped
        echo "🛑 Watchdog stopped."
        exit 0
        ;;
    watch)
        if [ -f "$WATCHDOG_PIDFILE" ]; then
            wpid=$(cat "$WATCHDOG_PIDFILE" 2>/dev/null || true)
            if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
                echo "ℹ️  Watchdog is already running (PID $wpid)"
                exit 0
            fi
        fi
        chmod +x runtime/watchdog.sh 2>/dev/null || true
        nohup ./runtime/watchdog.sh >/dev/null 2>&1 &
        wpid=$!
        echo $wpid > "$WATCHDOG_PIDFILE"
        sleep 1
        if [ -f "$WATCHDOG_PIDFILE" ] && kill -0 "$(cat "$WATCHDOG_PIDFILE")" 2>/dev/null; then
            echo "👁️  Watchdog supervisor started (PID $(cat "$WATCHDOG_PIDFILE"))"
            echo "   Log: $(pwd)/runtime/watchdog.log"
        else
            echo "❌ Failed to start watchdog supervisor"
            exit 1
        fi
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
                echo "✅ Gateway: Running (PID $active_pid)"
            else
                echo "⚠️  Gateway: Stale pidfile (PID $active_pid not running)"
            fi
        else
            echo "❌ Gateway: Not running (no $PIDFILE)"
        fi

        if [ -f "$WATCHDOG_PIDFILE" ]; then
            wpid=$(cat "$WATCHDOG_PIDFILE" 2>/dev/null || true)
            if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
                echo "👁️  Watchdog: Running (PID $wpid)"
            else
                echo "⚠️  Watchdog: Stale pidfile (PID $wpid not running)"
            fi
        else
            echo "⚪ Watchdog: Not running"
        fi
        exit 0
        ;;
    --detached)
        # Fire-and-forget: hand the kill+restart to an independent background
        # process so THIS caller (which may itself be a child of the gateway,
        # e.g. a bot-spawned agy) never kills its own ancestor in place.
        SELF_ABS="$(cd "$(dirname "$0")" && pwd)/restart.sh"
        nohup bash -c "sleep 1; exec '$SELF_ABS' --do-restart" >/dev/null 2>&1 &
        disown 2>/dev/null || true
        echo "🔄 Restart scheduled in background (detached)."
        echo "   Gateway + watchdog will be stopped and started ~1s from now."
        exit 0
        ;;
    --do-restart)
        # Internal: pause the watchdog so it cannot race the restart, then fall
        # through to the inline restart below; the watchdog is resumed after.
        ensure_watchdog_stopped
        RESTART_WATCH=1
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

# If this was a --do-restart, bring the watchdog supervisor back up.
if [ "${RESTART_WATCH:-0}" = "1" ]; then
    chmod +x runtime/watchdog.sh 2>/dev/null || true
    nohup ./runtime/watchdog.sh >/dev/null 2>&1 &
    echo $! > "$WATCHDOG_PIDFILE"
    sleep 1
    if [ -f "$WATCHDOG_PIDFILE" ] && kill -0 "$(cat "$WATCHDOG_PIDFILE")" 2>/dev/null; then
        echo "👁️  Watchdog supervisor resumed (PID $(cat "$WATCHDOG_PIDFILE"))"
    else
        echo "⚠️  Watchdog failed to resume — run ./restart.sh watch to start it."
    fi
fi