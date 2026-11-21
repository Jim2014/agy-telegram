#!/usr/bin/env bash
# Watchdog supervisor for agy-telegram gateway.
# Periodically checks if the gateway process is alive. If not, auto-restarts it.
# Usage:
#   nohup ./watchdog.sh >/dev/null 2>&1 &
#   or via: ./restart.sh watch

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

mkdir -p runtime
WATCHDOG_PIDFILE="runtime/watchdog.pid"
GATEWAY_PIDFILE="runtime/gateway.pid"
WATCHDOG_LOG="runtime/watchdog.log"
CHECK_INTERVAL=10

echo $$ > "$WATCHDOG_PIDFILE"

log() {
    local msg="$1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [watchdog] $msg" >> "$WATCHDOG_LOG"
}

cleanup() {
    log "Watchdog stopping (PID $$)..."
    rm -f "$WATCHDOG_PIDFILE"
    exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP

log "Watchdog supervisor started (PID $$, check interval ${CHECK_INTERVAL}s)."

while true; do
    is_running=false

    if [ -f "$GATEWAY_PIDFILE" ]; then
        pid=$(cat "$GATEWAY_PIDFILE" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            is_running=true
        fi
    fi

    if [ "$is_running" = false ]; then
        log "⚠️ Gateway process is not running! Triggering auto-restart via ./restart.sh..."
        ./restart.sh >> "$WATCHDOG_LOG" 2>&1
        new_pid=$(cat "$GATEWAY_PIDFILE" 2>/dev/null || true)
        if [ -n "$new_pid" ] && kill -0 "$new_pid" 2>/dev/null; then
            log "✅ Gateway successfully auto-restarted with PID $new_pid."
        else
            log "❌ Gateway failed to auto-restart. Will retry in ${CHECK_INTERVAL}s."
        fi
    fi

    sleep "$CHECK_INTERVAL"
done
