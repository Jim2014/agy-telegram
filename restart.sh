#!/usr/bin/env bash
# Restart the agy-telegram gateway using a PID file for reliable stop.
# Usage:
#   ./restart.sh          restart (or start) the gateway
#   ./restart.sh stop     stop the gateway (and watchdog if running)
#   ./restart.sh status   show whether gateway and watchdog are running (alias: --status)
#   ./restart.sh watch    start the watchdog auto-restart supervisor
#   ./restart.sh stop-watch stop only the watchdog supervisor
#   ./restart.sh --detached   schedule kill+restart in a detached background
#                             process and return immediately (safe to call
#                             from a bot-spawned agy that is a child of the
#                             gateway: the gateway is never killed in-place)
#   ./restart.sh --check  config validation only
#   ./restart.sh --help   show this help message
set -u
cd "$(dirname "$0")" || exit 1
mkdir -p runtime
PIDFILE="runtime/gateway.pid"
LEGACY_PIDFILE="gateway.pid"
WATCHDOG_PIDFILE="runtime/watchdog.pid"
LAUNCHD_LABEL="ai.agy.telegram"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
REPO_PLIST="$(cd "$(dirname "$0")" && pwd)/ai.agy.telegram.plist"

# Determine python command (prioritize agy-telegram conda env, venv, or system python)
if [ -x "/opt/homebrew/Caskroom/miniconda/base/envs/agy-telegram/bin/python" ]; then
    PY_BIN="/opt/homebrew/Caskroom/miniconda/base/envs/agy-telegram/bin/python"
elif [ -x "./venv/bin/python" ]; then
    PY_BIN="./venv/bin/python"
elif command -v conda >/dev/null 2>&1 && conda env list 2>/dev/null | grep -q "agy-telegram"; then
    PY_BIN="$(conda info --base 2>/dev/null)/envs/agy-telegram/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
else
    PY_BIN="python"
fi

is_launchd_loaded() {
    command -v launchctl >/dev/null 2>&1 && launchctl list "$LAUNCHD_LABEL" >/dev/null 2>&1
}

enable_autostart() {
    mkdir -p "$HOME/Library/LaunchAgents"
    ensure_watchdog_stopped
    ensure_stopped

    if [ ! -f "$REPO_PLIST" ]; then
        echo "❌ Plist template not found at $REPO_PLIST"
        exit 1
    fi

    WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
    LAUNCHD_PATH="$(dirname "$PY_BIN"):$HOME/.local/bin:$HOME/.gemini/antigravity-cli/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    "$PY_BIN" -c '
import sys
src, dst, py_bin, work_dir, home_dir, user_name, path_val = sys.argv[1:8]
with open(src, "r", encoding="utf-8") as f:
    txt = f.read()
for k, v in (
    ("__PY_BIN__", py_bin),
    ("__WORK_DIR__", work_dir),
    ("__HOME__", home_dir),
    ("__USER__", user_name),
    ("__PATH__", path_val),
):
    txt = txt.replace(k, v)
with open(dst, "w", encoding="utf-8") as f:
    f.write(txt)
' "$REPO_PLIST" "$LAUNCHD_PLIST" "$PY_BIN" "$WORK_DIR" "$HOME" "${USER:-$(whoami)}" "$LAUNCHD_PATH"
    chmod 644 "$LAUNCHD_PLIST"

    # Unload first if previously loaded
    launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
    
    # Load and enable
    if launchctl load -w "$LAUNCHD_PLIST" 2>/dev/null; then
        echo "✅ macOS LaunchAgent registered: $LAUNCHD_PLIST"
        sleep 2
        if is_launchd_loaded; then
            echo "🚀 agy-telegram is now managed by launchd and will auto-start on macOS boot/login!"
        else
            echo "⚠️  LaunchAgent loaded, waiting for process..."
        fi
        exit 0
    else
        echo "❌ Failed to load LaunchAgent with launchctl."
        exit 1
    fi
}

disable_autostart() {
    if [ -f "$LAUNCHD_PLIST" ]; then
        launchctl unload -w "$LAUNCHD_PLIST" 2>/dev/null || true
        rm -f "$LAUNCHD_PLIST"
        echo "🛑 LaunchAgent autostart disabled and $LAUNCHD_PLIST removed."
    else
        echo "ℹ️  LaunchAgent plist not found ($LAUNCHD_PLIST)."
    fi
    ensure_stopped
    exit 0
}

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
        pkill -f "watchdog.sh" 2>/dev/null || true
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
    -h|--help|help)
        echo "Usage: ./restart.sh [command]"
        echo "  (no args)           Restart (or start) the gateway"
        echo "  status | --status   Show gateway, launchd, and watchdog status"
        echo "  stop | --stop       Stop the gateway (and watchdog if running)"
        echo "  watch | --watch     Start the watchdog auto-restart supervisor"
        echo "  stop-watch          Stop only the watchdog supervisor"
        echo "  autostart           Enable macOS LaunchAgent autostart"
        echo "  autostart-disable   Disable macOS LaunchAgent autostart"
        echo "  autostart-status    Check macOS LaunchAgent status"
        echo "  --check             Validate configuration only"
        echo "  --detached          Schedule a detached restart safely"
        exit 0
        ;;
    --check)
        $PY_BIN main.py --check-config
        exit $?
        ;;
    autostart-enable|enable-autostart|install-service|install-autostart|autostart)
        enable_autostart
        ;;
    autostart-disable|disable-autostart|uninstall-service|uninstall-autostart)
        disable_autostart
        ;;
    autostart-status|--autostart-status)
        if [ -f "$LAUNCHD_PLIST" ]; then
            if is_launchd_loaded; then
                echo "🚀 Autostart: Enabled & Loaded in launchd ($LAUNCHD_LABEL)"
            else
                echo "⚠️  Autostart: Plist installed at $LAUNCHD_PLIST but NOT loaded in launchctl"
            fi
        else
            echo "⚪ Autostart: Disabled (not installed)"
        fi
        exit 0
        ;;
    stop|--stop)
        ensure_watchdog_stopped
        if is_launchd_loaded; then
            echo "🛑 Stopping gateway via launchctl..."
            launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
        fi
        ensure_stopped
        echo "🛑 Gateway and watchdog stopped."
        exit 0
        ;;
    stop-watch|--stop-watch)
        ensure_watchdog_stopped
        echo "🛑 Watchdog stopped."
        exit 0
        ;;
    watch|--watch)
        if [ -f "$WATCHDOG_PIDFILE" ]; then
            wpid=$(cat "$WATCHDOG_PIDFILE" 2>/dev/null || true)
            if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
                echo "ℹ️  Watchdog is already running (PID $wpid)"
                exit 0
            fi
        fi
        chmod +x watchdog.sh 2>/dev/null || true
        nohup ./watchdog.sh >/dev/null 2>&1 &
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
    status|--status)
        active_pid=""
        if [ -f "$PIDFILE" ]; then
            active_pid=$(cat "$PIDFILE" 2>/dev/null || true)
        elif [ -f "$LEGACY_PIDFILE" ]; then
            active_pid=$(cat "$LEGACY_PIDFILE" 2>/dev/null || true)
        fi

        if [ -n "$active_pid" ] && kill -0 "$active_pid" 2>/dev/null; then
            echo "✅ Gateway: Running (PID $active_pid)"
        else
            lpid=""
            if command -v launchctl >/dev/null 2>&1; then
                lpid=$(launchctl list 2>/dev/null | awk -v lbl="$LAUNCHD_LABEL" '$3 == lbl {print $1}')
            fi
            if [ -n "$lpid" ] && [ "$lpid" != "-" ]; then
                echo "✅ Gateway: Running under launchd (PID $lpid)"
            elif [ -n "$active_pid" ]; then
                echo "⚠️  Gateway: Stale pidfile (PID $active_pid not running)"
            else
                echo "❌ Gateway: Not running (no $PIDFILE)"
            fi
        fi

        if [ -f "$LAUNCHD_PLIST" ]; then
            if is_launchd_loaded; then
                echo "🚀 Autostart (LaunchAgent): Enabled & Loaded ($LAUNCHD_LABEL)"
            else
                echo "⚠️  Autostart (LaunchAgent): Plist installed but NOT loaded in launchctl"
            fi
        else
            echo "⚪ Autostart (LaunchAgent): Disabled (not installed)"
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
        # If an active agy turn is in progress, queue runtime/restart.requested
        # so the gateway delivers the full final report first before restarting!
        SELF_ABS="$(cd "$(dirname "$0")" && pwd)/restart.sh"
        TURN_LOCK="runtime/turn_active.lock"
        RESTART_FLAG="runtime/restart.requested"
        gw_alive=0
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null || true)" 2>/dev/null; then
            gw_alive=1
        elif is_launchd_loaded; then
            lpid=""
            if command -v launchctl >/dev/null 2>&1; then
                lpid=$(launchctl list 2>/dev/null | awk -v lbl="$LAUNCHD_LABEL" '$3 == lbl {print $1}')
            fi
            if [ -n "$lpid" ] && [ "$lpid" != "-" ]; then
                gw_alive=1
            fi
        fi

        turn_lock_fresh=0
        if [ -f "$TURN_LOCK" ] && find "$TURN_LOCK" -mmin -15 2>/dev/null | grep -q .; then
            turn_lock_fresh=1
        fi

        if [ -f "$TURN_LOCK" ] && { [ "$gw_alive" -eq 1 ] || [ "$turn_lock_fresh" -eq 1 ]; }; then
            touch "$RESTART_FLAG"
            # Safety fallback after 120s in case the active turn hangs unexpectedly
            nohup bash -c "sleep 120; if [ -f '$RESTART_FLAG' ]; then rm -f '$RESTART_FLAG' '$TURN_LOCK'; exec '$SELF_ABS' --do-restart; fi" >/dev/null 2>&1 &
            disown 2>/dev/null || true
            echo "🔄 Active turn detected — restart queued."
            echo "   Gateway will restart automatically right after delivering the final report."
            exit 0
        fi

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
    "")
        # Default: restart (or start) the gateway
        ;;
    *)
        echo "❌ Unknown command or flag: $1"
        echo "Run './restart.sh --help' for valid options."
        exit 1
        ;;
esac

# If managed by launchd, use launchctl kickstart to cleanly restart
if is_launchd_loaded; then
    echo "🔄 Restarting gateway via launchd supervisor..."
    launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || {
        launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
        sleep 1
        launchctl start "$LAUNCHD_LABEL" 2>/dev/null || true
    }
    sleep 2
    ./restart.sh status
    exit 0
fi

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
    chmod +x watchdog.sh 2>/dev/null || true
    nohup ./watchdog.sh >/dev/null 2>&1 &
    echo $! > "$WATCHDOG_PIDFILE"
    sleep 1
    if [ -f "$WATCHDOG_PIDFILE" ] && kill -0 "$(cat "$WATCHDOG_PIDFILE")" 2>/dev/null; then
        echo "👁️  Watchdog supervisor resumed (PID $(cat "$WATCHDOG_PIDFILE"))"
    else
        echo "⚠️  Watchdog failed to resume — run ./restart.sh watch to start it."
    fi
fi