#!/usr/bin/env bash
# ============================================================================
# agy-telegram Interactive Setup Script
# ============================================================================
# Sets up the Python environment (Conda 'agy-telegram' or Python venv),
# installs dependencies, configures .env (including live Telegram Bot Token
# verification & automatic Chat ID detection), validates configuration,
# and optionally starts the gateway / enables macOS LaunchAgent autostart.
#
# Usage:
#   ./setup.sh            Interactive setup (prefers Conda, fallback to venv)
#   ./setup.sh --venv     Force Python venv instead of Conda
#   ./setup.sh --check    Skip env creation and validate existing configuration
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

USE_VENV=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --venv) USE_VENV=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help)
            echo "Usage: ./setup.sh [--venv] [--check]"
            exit 0
            ;;
    esac
done

echo ""
echo "=========================================="
echo "  agy-telegram First-Time Setup Wizard"
echo "=========================================="
echo ""

# 1. Check prerequisites
echo "[1/5] Checking prerequisites..."
if command -v agy >/dev/null 2>&1; then
    echo "  ✅ agy CLI found: $(command -v agy)"
elif [ -x "$HOME/.local/bin/agy" ]; then
    echo "  ✅ agy CLI found: $HOME/.local/bin/agy"
else
    echo "  ⚠️  agy CLI not found in PATH or ~/.local/bin/agy."
    echo "     Install and authenticate Google Antigravity CLI before running tasks."
fi

if command -v ffmpeg >/dev/null 2>&1; then
    echo "  ✅ ffmpeg found (offline voice fallback ready)"
else
    echo "  ℹ️  ffmpeg not found (optional; Edge-TTS works without it)"
fi

# 2. Prepare Python environment (Conda 'agy-telegram' or Python venv)
echo ""
echo "[2/5] Setting up Python environment..."
PY_BIN=""

find_conda_python() {
    if [ -x "/opt/homebrew/Caskroom/miniconda/base/envs/agy-telegram/bin/python" ]; then
        echo "/opt/homebrew/Caskroom/miniconda/base/envs/agy-telegram/bin/python"
        return 0
    fi
    if command -v conda >/dev/null 2>&1; then
        local cbase
        cbase="$(conda info --base 2>/dev/null || true)"
        if [ -n "$cbase" ] && [ -x "$cbase/envs/agy-telegram/bin/python" ]; then
            echo "$cbase/envs/agy-telegram/bin/python"
            return 0
        fi
    fi
    return 1
}

if [ "$CHECK_ONLY" -eq 1 ]; then
    PY_BIN="$(find_conda_python || true)"
    if [ -z "$PY_BIN" ] && [ -x "./venv/bin/python" ]; then
        PY_BIN="./venv/bin/python"
    fi
    if [ -z "$PY_BIN" ]; then
        echo "  ❌ No agy-telegram Conda env or ./venv found. Run ./setup.sh first."
        exit 1
    fi
    "$PY_BIN" main.py --check-config
    exit $?
fi

if [ "$USE_VENV" -eq 0 ] && command -v conda >/dev/null 2>&1; then
    PY_BIN="$(find_conda_python || true)"
    if [ -n "$PY_BIN" ]; then
        echo "  ✅ Conda env 'agy-telegram' already exists ($PY_BIN)"
        read -r -p "  Update dependencies from requirements.txt? [y/N]: " upd_deps
        if [[ "${upd_deps:-n}" =~ ^[Yy]$ ]]; then
            "$PY_BIN" -m pip install -r requirements.txt || exit 1
        fi
    else
        echo "  Creating Conda environment 'agy-telegram' from environment.yml..."
        conda env create -f environment.yml || {
            echo "  ❌ Failed to create Conda env 'agy-telegram'."
            exit 1
        }
        PY_BIN="$(find_conda_python || true)"
    fi
fi

if [ -z "$PY_BIN" ]; then
    if [ "$USE_VENV" -eq 0 ]; then
        echo "  ℹ️  Conda not detected; falling back to Python venv (./venv)."
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "  ❌ python3 is required but not installed."
        exit 1
    fi
    if [ ! -x "./venv/bin/python" ]; then
        echo "  Creating Python virtual environment at ./venv..."
        python3 -m venv venv || exit 1
    fi
    PY_BIN="./venv/bin/python"
    echo "  Installing dependencies from requirements.txt..."
    "$PY_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$PY_BIN" -m pip install -r requirements.txt || exit 1
fi

echo "  ✅ Using Python: $PY_BIN"

# 3. Interactive .env & Telegram onboarding
echo ""
echo "[3/5] Configuring Telegram & .env..."
"$PY_BIN" -m gateway.setup_helper || {
    echo "  ❌ Configuration setup failed or was cancelled."
    exit 1
}

# 4. Validate config & optional smoke test
echo ""
echo "[4/5] Validating configuration..."
"$PY_BIN" main.py --check-config || {
    echo "  ⚠️  Configuration check reported issues. Review .env and re-run ./setup.sh."
    exit 1
}

read -r -p "Run a quick agy CLI smoke test? [y/N]: " run_smoke
if [[ "${run_smoke:-n}" =~ ^[Yy]$ ]]; then
    "$PY_BIN" main.py --smoke-test || echo "  ⚠️  Smoke test failed — verify 'agy' CLI authentication."
fi

# 5. Start gateway / autostart
echo ""
echo "[5/5] Service startup..."
chmod +x restart.sh watchdog.sh 2>/dev/null || true

if [ "$(uname -s)" = "Darwin" ]; then
    read -r -p "Enable macOS boot/login autostart via LaunchAgent (starts gateway now)? [y/N]: " enable_mac
    if [[ "${enable_mac:-n}" =~ ^[Yy]$ ]]; then
        ./restart.sh autostart
        echo ""
        echo "🎉 Setup complete! Open your bot in Telegram and send /help."
        exit 0
    fi
fi

read -r -p "Start agy-telegram gateway in background now? [Y/n]: " start_now
if [[ ! "${start_now:-y}" =~ ^[Nn]$ ]]; then
    ./restart.sh
fi

echo ""
echo "🎉 Setup complete! Open your bot in Telegram and send /help to begin."
