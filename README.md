# agy-telegram — Direct Telegram Gateway for agy (Antigravity CLI)

A lightweight, real-time Telegram gateway for interacting directly with `agy` (Antigravity CLI) from Telegram. Stream **thinking progress, tool calls, and live partial outputs**, send **mid-flight steering instructions**, and control workspaces with `/` slash commands — without any intermediate proxy overhead.

---

## Table of Contents
- [Main Features](#main-features)
- [⚠️ Limitations & Security Notice](#%EF%B8%8F-limitations--security-notice)
- [Prerequisites](#prerequisites)
- [Environment Setup](#environment-setup)
  - [Quick Start (Recommended): `./setup.sh`](#quick-start-recommended-one-command-interactive-setup-setupsh)
  - [Manual Conda / `venv` Setup](#manual-option-a-conda-environment-recommended-env-agy-telegram)
- [Runtime Isolation (`runtime/`)](#runtime-isolation-runtime)
- [Configuration Guide (`.env`)](#configuration-guide-env)
- [Starting and Managing the Service](#starting-and-managing-the-service)
- [Telegram Commands & Interactive UI](#telegram-commands--interactive-ui)
- [Architecture & Module Overview](#architecture--module-overview)
- [Testing & Verification](#testing--verification)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

---

## Main Features

- **Direct Real-Time Streaming via Telegram**: Built on Telegram Long-Polling (no public IP or webhook required) to stream `agy` thinking progress, live tool calls, and partial outputs directly to mobile.
- **Mid-Flight Steering & Warm Process Pool**: Send a follow-up text, voice note, file, or `/steer` while `agy` is working to guide the active turn seamlessly; rotates the Telegram progress reply cleanly and delivers one consolidated final report.
- **Multi-Project Isolation**: Manage separate workspaces (`/new`, `/open`, `/projects`, `/delete`, `/reset`), each with its own directory path, `conversation_id`, token/duration stats, and pinned status banner (`/pin`).
- **Dynamic Model & Effort Controls**: Auto-discovers available models from `agy models` via `/model` (with refresh) and adjusts reasoning depth via `/effort` (`low` / `medium` / `high`).
- **Rich Mobile-First Output & WebApp Viewer**: Converts Markdown to Telegram HTML, renders code/diffs/CSVs/directory trees as mobile-friendly standalone HTML (`/file -h`, `/dir`, `/html`), and serves previews in an embedded Telegram Mini App (`:8765`).
- **Voice Interaction (Offline STT + Bilingual TTS)**: Transcribes Telegram voice messages offline via `faster-whisper` and generates spoken audio summaries (`[AGY_VOICE]`) with on-demand playback buttons or auto-send (`/voice`).
- **One-Tap Setup & Hardened Access Control**: Interactive `./setup.sh` wizard with live token verification and automatic Chat ID detection, **deny-by-default** authorization with **DM pairing** (`gateway.pairing`), crash watchdog (`watchdog.sh`), and macOS LaunchAgent autostart.
- **Cross-Project Multi-Agent Mesh (`AGY_IPC`)**: Dispatches tasks across registered project directories with pinned expandable progress cards in Telegram and audit logging (`runtime/ipc.log`).
- **Automated Test Suite**: **162 tests** covering unit logic, Telegram API resilience, protocol extraction, onboarding setup, DM pairing, deferred restarts, and live `agy` CLI smoke testing (see [ROADMAP.md](ROADMAP.md)).

---

## ⚠️ Limitations & Security Notice

> [!WARNING]
> **Please read carefully before deploying on a personal or production machine:**
>
> 1. **Platform Testing Scope (macOS Tested Only):**
>    `agy-telegram` has only been tested on **macOS** (Apple Silicon). It should also work on **Linux** or inside a **Virtual Machine / Container** (note: macOS-specific features like `launchd` autostart and `say` offline TTS fallback are skipped on Linux, while Edge-TTS works cross-platform), though non-macOS environments have not been formally tested.
> 2. **Unrestricted Tool & Shell Execution (`--dangerously-skip-permissions`):**
>    Because Telegram acts as a non-interactive headless bridge, `agy-telegram` launches `agy` with `--dangerously-skip-permissions`. This grants `agy` permission to use **all enabled tools** and **execute any shell command** without interactive confirmation prompts.
>    - **Strongly Recommended:** Run `agy-telegram` inside an **isolated container (Docker) or Virtual Machine (VM)**, or make sure you explicitly trust granting `agy` full tool and command-line permissions on the host machine.
>    - Always keep `ALLOW_ALL_CHATS=false` and configure `ALLOWED_CHAT_IDS` + DM pairing so unauthorized Telegram users cannot reach your bot.

---

## Disclaimer

- **Use at Your Own Risk:** This software is provided *"AS IS"*, without warranty of any kind. Because `agy-telegram` enables autonomous AI execution with unrestricted local tool and shell permissions (`--dangerously-skip-permissions`), **you are solely responsible** for any commands executed, files modified or deleted, system changes, API/model quota usage, or unintended behavior resulting from running this tool. The author and contributors assume no liability for any damages or data loss.
- **No Official Affiliation:** `agy-telegram` is an independent open-source project and is not affiliated with, endorsed by, or officially connected to Google, Antigravity, or Telegram.

---

## Prerequisites

Before cloning and running `agy-telegram`, make sure you have:
- **Antigravity CLI (`agy`)** installed and authenticated on your machine (verify with `agy --version` and `agy models`).
- **Python 3.10+** (or **Miniconda / Anaconda**).
- **Telegram** account (mobile or desktop).
- *Optional:* `ffmpeg` installed if you want macOS offline TTS audio fallback (`brew install ffmpeg`).

---

## Environment Setup

First, clone the repository:

```bash
git clone https://github.com/Jim2014/agy-telegram.git
cd agy-telegram
```

### Quick Start (Recommended): One-Command Interactive Setup (`./setup.sh`)

Run the interactive onboarding wizard to create the Python environment (Conda `agy-telegram` or `venv`), verify your Telegram bot token via `getMe`, auto-detect your Telegram `chat_id` when you send `/start`, write `.env` with `0600` permissions, and validate the setup:

```bash
./setup.sh
```

*(Pass `./setup.sh --venv` to force Python `venv` instead of Conda, or `./setup.sh --check` to validate an existing config.)*

---

### Manual Option A: Conda Environment (Recommended, env: `agy-telegram`)

An `environment.yml` (Python 3.10) is provided:

```bash
# 1. Create and activate the conda environment
conda env create -f environment.yml
conda activate agy-telegram

# 2. Verify or update Python dependencies (if needed)
pip install -r requirements.txt
```

### Option B: Python `venv`

```bash
# 1. Create virtual environment
python3 -m venv venv

# 2. Install dependencies
./venv/bin/pip install -r requirements.txt
```

---

## Runtime Isolation (`runtime/`)

To keep the repository clean and prevent runtime data from entering Git history:

```text
agy-telegram/
├── runtime/              # Runtime dynamic data directory (gitignored)
│   ├── gateway.log       # Main gateway logs
│   ├── gateway.pid       # Gateway process PID file
│   ├── ipc.log           # Cross-project IPC audit log
│   ├── watchdog.log      # Watchdog logs
│   ├── watchdog.pid      # Watchdog PID file
│   └── sessions.json     # Persistent chat, project & stats store
├── gateway/              # Core Python gateway modules
├── tests/                # Unit, integration & smoke tests
├── setup.sh              # Interactive first-time setup & Telegram onboarding wizard
├── restart.sh            # Service, watchdog & autostart control script
├── watchdog.sh           # Watchdog auto-restart supervisor script
├── environment.yml       # Conda environment specification
└── README.md
```

- The `runtime/` directory is created automatically on startup.
- Legacy root-level `sessions.json` files are migrated automatically if present.

---

## Configuration Guide (`.env`)

### Step 1: Obtain a Telegram Bot Token

1. Open Telegram and search for the official [@BotFather](https://t.me/BotFather) account.
2. Send `/newbot` and follow the prompts to choose a display name and username (e.g. `my_agy_bot`).
3. Copy the generated API token (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`).

### Step 2: Find Your Telegram Chat ID (Security Whitelist)

> [!NOTE]
> `agy-telegram` enforces **deny-by-default + DM pairing** (`ALLOW_ALL_CHATS=false`, `DM_PAIRING_ENABLED=true`). If `ALLOWED_CHAT_IDS` is empty, unknown senders are denied and receive a pairing code you can approve from the terminal (`python -m gateway.pairing approve <CODE>`). Setting your own Chat ID in `ALLOWED_CHAT_IDS` also enables one-tap `[ ✅ Approve ]` / `[ ❌ Reject ]` notifications in Telegram.

1. Search for [@userinfobot](https://t.me/userinfobot) on Telegram and press **Start**.
2. Copy your numeric **Id** (for example, `123456789`).

### Step 3: Configure `.env`

Create `.env` from the template:

```bash
cp .env.example .env
```

Edit `.env` and fill in at least `BOT_TOKEN` and `ALLOWED_CHAT_IDS`:

```ini
# Required: Bot token from @BotFather
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Comma-separated Telegram Chat IDs allowed to access the bot (deny-by-default if empty).
ALLOWED_CHAT_IDS=
ALLOW_ALL_CHATS=false
DM_PAIRING_ENABLED=true

# agy CLI binary path (defaults to ~/.local/bin/agy or agy in PATH)
AGY_BIN_PATH=~/.local/bin/agy

# Default working directory (--add-dir). Leave empty to use the repository root.
AGY_WORKING_DIR=

# Default model and reasoning effort
DEFAULT_MODEL=gemini-3.8-flash
DEFAULT_EFFORT=high

# Streaming edit throttle interval in seconds (default: 1.0)
STREAM_THROTTLE_SECONDS=1.0

# Subprocess timeout in seconds (default: 300)
AGY_TIMEOUT_SECONDS=300

# Default HTML attachment mode (auto | explicit | off)
HTML_MODE=auto

# Logging level (INFO / DEBUG) and runtime file paths
LOG_LEVEL=INFO
LOG_FILE=runtime/gateway.log
SESSIONS_FILE=runtime/sessions.json
```

---

## Starting and Managing the Service

### 1. Validate Configuration

```bash
conda run -n agy-telegram python main.py --check-config
# Or via helper script:
./restart.sh --check
```

### 2. Run `agy` CLI Smoke Test

Verify that the local `agy` CLI runs properly and emits `stream-json` events:

```bash
conda run -n agy-telegram python main.py --smoke-test
```

### 3. Start & Manage the Gateway

For a first-time test in the **foreground** (so you can watch live logs directly in your terminal):

```bash
conda run -n agy-telegram python main.py
# Or with venv:
./venv/bin/python main.py
```

Or use `restart.sh` to start and manage the gateway in the **background** (and macOS LaunchAgent autostart):

```bash
# Start or restart the gateway in background
./restart.sh

# Check gateway, WebApp health, and LaunchAgent status
./restart.sh status

# (macOS only) Enable boot/login autostart via LaunchAgent
./restart.sh autostart

# (macOS only) Disable macOS autostart
./restart.sh autostart-disable

# Start crash auto-recovery watchdog supervisor (Linux / macOS)
./restart.sh watch

# Stop gateway (and watchdog if running)
./restart.sh stop

# Stop only the watchdog
./restart.sh stop-watch
```

Logs are written to `runtime/gateway.log` and `runtime/watchdog.log`:

```bash
tail -f -n 50 runtime/gateway.log
tail -f -n 50 runtime/watchdog.log
```

### 4. First Chat in Telegram

1. Open the bot link provided by `@BotFather` (`https://t.me/<your_bot_username>`) in Telegram and tap **Start** (or send `/help`).
2. You should see the interactive **agy-telegram Command Center** with quick-action buttons.
3. Send any prompt directly (for example: `Summarize this repository`) — on first message, a `default` project is created at `AGY_WORKING_DIR`.
4. Useful first-time setup commands in Telegram:
   - `/new myapp /path/to/your/project` — create and switch to another project workspace
   - `/model` — inspect and select models auto-discovered from `agy models`
   - `/effort` — choose reasoning depth (`low`, `medium`, `high`)
   - `/voice` & `/html` — configure spoken voice notes and automatic HTML report attachments

---

## Telegram Commands & Interactive UI

### Adaptive Slash Menu & Interactive Controls

- **Adaptive Pure-LRU Command Menu**: All 20 commands are registered in the Telegram `/` menu. `/help` stays pinned at position 0, while the most recently used commands automatically float to the top per chat.
- **Pinned Workspace Status Banner (`/pin`)**: Keeps a live pinned status banner at the top of the chat displaying active project, workspace path, model, token totals, uptime, and quick-action buttons (`[ 📊 Status ]`, `[ 📁 Projects ]`, `[ 🪙 Quota ]`, `[ 📖 Help ]`).
- **Interactive Decision & Action Buttons (`[AGY_ACTIONS]`)**: Renders one-tap inline buttons under responses when `ask_question` or `[AGY_ACTIONS]` blocks are returned.
- **Expandable Tool Progress (`<blockquote expandable>`)**: Keeps long tool logs compact on mobile while letting you expand details in place.
- **Instant Emoji Reactions**: Reacts with `👀` on receipt, `⚡` during tool execution, `✅` on completion, and `⚠️` on errors.
- **`/usage` Quota Dashboard**: Queries Google Antigravity weekly and 5-hour quota limits, reset timers, and AI credits with progress bars.
- **`/skills` Overview**: Lists active built-in and plugin skills in the workspace.
- **`/artifacts` Manager**: Lists generated HTML reports, diffs, and `scratch/` scripts with one-tap download buttons.
- **`/projects` Switcher**: Interactive project buttons with `[ 🔄 Refresh ]` support.
- **`/effort` Controls**: Instant `[ 🟢 Low ]`, `[ 🟡 Medium ]`, `[ 🔴 High ]` toggles.
- **`/model` Selector**: Auto-discovers available models from `agy models` (with caching and `[ 🔄 Refresh ]`) for instant switching.

### Supported Commands & Inputs

| Command / Input | Description | Example |
| :--- | :--- | :--- |
| **Plain text message** | Starts a new turn when idle; **automatically steers** the active session when busy | `List files in this repository` |
| **🎙️ Voice / Audio message** | Downloaded locally and transcribed offline via `faster-whisper`; steers automatically if busy | 🎙️ Send a voice note |
| **📎 File / Photo attachment** | Saved to `runtime/documents/` and passed with metadata to `agy`; supports mid-flight steering | 📎 Upload code, docs, or images |
| `/voice [mode]` | Configure TTS mode: `disable`, `autogen` (on-demand button), or `autoplay` | `/voice` or `/voice autoplay` |
| `/html [mode]` | Configure HTML attachment mode: `auto` (long replies & diffs), `explicit` (`[AGY_FILE]` only), or `off` | `/html` or `/html explicit` |
| `/pin` or `/banner` | Refresh and pin the live workspace status banner at the top of the chat | `/pin` |
| `/help` | Show interactive command center dashboard | `/help` |
| `/usage` or `/quota` | Zero-token real-time query of Antigravity quota limits and reset timers | `/usage` |
| `/skills` or `/plugins` | List registered agent skills and plugins | `/skills` |
| `/artifacts [index]` | Browse or download generated HTML reports and scratch artifacts | `/artifacts` or `/artifacts 1` |
| `/status` | Show active project, session stats (turns, tokens, thinking tokens, uptime, duration), and system status | `/status` |
| `/projects` | List all configured projects and switch workspaces | `/projects` |
| `/open <name>` | Switch to an existing project and restore its conversation state and stats | `/open myapp` |
| `/new <name> <path>` | Create a new project (auto-creates missing directories) and set it active | `/new myapp /path/to/myapp` |
| `/model <name>` | Switch active model (or `/model refresh` to reload from `agy models`) | `/model gemini-3.8-flash` |
| `/effort <level>` | Set reasoning effort (`low`, `medium`, `high`) | `/effort high` |
| `/dir` or `/ls [path]` | Scan workspace directory and generate an interactive HTML file tree | `/dir` or `/ls tests` |
| `/file [-h] <path>` | Send a workspace file; use `-h` to render code, Markdown, diffs, or CSVs as standalone HTML | `/file -h bot.py` |
| `/export` | Export current session state or latest output as a standalone HTML report | `/export` |
| `/steer <instruction>` | Explicitly inject steering guidance into the running `agy` turn | `/steer Use async/await` |
| `/stop` | Interrupt and terminate the active `agy` subprocess | `/stop` |
| `/reset` | Clear `conversation_id` and reset turn/token counters for the active project | `/reset` |
| `/delete <name>` | Remove a project from session configuration | `/delete myapp` |

### Dynamic Multi-Template HTML Rendering (`/file -h`)

When using `/file -h <path>`, the gateway selects a renderer based on file extension and outputs `<filename>.html`:
- **Source Code** (`.py`, `.js`, `.ts`, `.html`, `.css`, `.json`, `.yaml`, `.sql`, `.sh`, `.rs`, `.go`, etc.): Line numbers, syntax highlighting, copy button, and mobile-friendly horizontal scrolling.
- **Markdown** (`.md`, `.markdown`): Styled article layout supporting headings, lists, tables, and code blocks.
- **Diff / Patch** (`.diff`, `.patch`): Unified diff viewer with green/red line highlighting and hunk markers.
- **Tabular Data** (`.csv`, `.tsv`): Responsive table view with sticky headers and row counts.
- **Plain Text / Logs / Configs** (`.txt`, `.log`, `.env*`, `.gitignore`): Monospace console view with line numbers.
- **Binary / Unsupported Files**: Automatically falls back to sending the original raw file.

### Mid-Flight Steering & System Prompt Injection

- **First-Turn System Prompt Injection**: When starting a fresh session (or after `/reset`), the gateway injects output formatting conventions once without repeating them across follow-up turns or steering messages. Supports overrides via `AGY_SYSTEM_PROMPT` or `AGY_SYSTEM_PROMPT_FILE` in `.env`.
- **Mid-Flight Steering**: Sending a regular message, voice note, file, or `/steer <instruction>` while `agy` is running injects guidance via `stream-json` `stdin`, freezes the previous progress message, opens a new progress reply under your steering message, and sends a single consolidated final report upon completion.
- **Direct Standalone HTML Export (`[AGY_FILE]`)**:
  ```text
  [AGY_FILE]
  filename: diff_report.html      # Optional (default: report.html)
  title: Code Diff Report         # Optional (default: agy Response)
  ---
  <Standalone HTML content or workspace HTML file path>
  [END_AGY_FILE]
  ```

---

## Architecture & Module Overview

Each Python module is single-purpose and supports standalone CLI verification:

- `gateway/config.py` (`python -m gateway.config`): Environment loading, validation, dynamic binary resolution, and system prompt configuration.
- `gateway/setup_helper.py` (`python -m gateway.setup_helper`): Interactive onboarding wizard for `.env` generation, Telegram `getMe` verification, and automatic `chat_id` detection.
- `gateway/pairing.py` (`python -m gateway.pairing list|approve|revoke`): Deny-by-default DM pairing store (`runtime/pairing.json`) and CLI approval tool.
- `gateway/session_manager.py` (`python -m gateway.session_manager`): Multi-project session persistence (`runtime/sessions.json`), LRU command stats, and token metrics.
- `gateway/ui_keyboards.py` (`python -m gateway.ui_keyboards`): Inline keyboard builders, dynamic `agy models` discovery, adaptive LRU command menus, and WebApp viewer buttons.
- `gateway/transcriber.py` (`python -m gateway.transcriber`): Offline `faster-whisper` speech-to-text transcription with lazy model caching.
- `gateway/media_handlers.py` (`python -m gateway.media_handlers`): Voice/audio and document/photo download handlers with mid-flight steering routing.
- `gateway/commands.py` (`python -m gateway.commands`): Slash command handlers and inline callback query routing.
- `gateway/dir_tree.py` (`python -m gateway.dir_tree`): Recursive workspace directory scanner and interactive HTML file tree generator.
- `gateway/file_renderers.py` (`python -m gateway.file_renderers`): Multi-format standalone HTML renderers for `/file -h`.
- `gateway/message_formatter.py` (`python -m gateway.message_formatter`): Markdown-to-Telegram-HTML conversion, protocol block extractors (`[AGY_FILE]`, `[AGY_ACTIONS]`, `[AGY_VOICE]`), spoken summary distillation, and standalone HTML report generation.
- `gateway/agy_bridge.py` (`python -m gateway.agy_bridge "<prompt>"`): Asynchronous `agy` subprocess manager, `stream-json` bidirectional streaming, keep-alive process pooling, and steering injection.
- `gateway/ipc_router.py` (`python -m gateway.ipc_router`): Cross-project `[AGY_IPC]` message parsing, envelope formatting, and `runtime/ipc.log` auditing.
- `gateway/voice_tts.py` (`python -m gateway.voice_tts`): Dynamic bilingual TTS synthesis (`edge-tts` + offline fallback) and OGG Opus conversion.
- `gateway/webapp_server.py` (`python -m gateway.webapp_server`): Lightweight asynchronous HTTP server for Telegram Mini App document viewing.
- `gateway/telegram_client.py` (`python -m gateway.telegram_client`): Async Telegram Bot API client with retries, HTML fallback, multipart uploads, and rate-limit backoff.
- `gateway/bot.py`: Core orchestrator dispatching long-polling updates, streaming progress updates, steering rotation, and exports.
- `main.py` (`python main.py`): CLI entry point supporting `--check-config` and `--smoke-test`.

### Design Documents

- [docs/cross_project_mesh_design.md](docs/cross_project_mesh_design.md): Cross-project decentralized `AGY_IPC` protocol and Telegram pinned expandable card architecture.
- [ROADMAP.md](ROADMAP.md): Project milestones and feature roadmap.

---

## Testing & Verification

Run the full automated test suite:

```bash
conda run -n agy-telegram pytest -v
# Or with venv:
./venv/bin/pytest -v
```

Test coverage (**162 tests** across 16 test modules):
- `tests/test_config.py`: Configuration loading, token validation, deny-by-default chat whitelist, default runtime paths, and system prompt overrides.
- `tests/test_pairing.py`: DM pairing code lifecycle, expiration, atomic `0600` persistence, revocation, and CLI commands.
- `tests/test_setup_helper.py`: Telegram `getMe` verification, `getUpdates` Chat ID auto-detection, `.env` rendering/permissions (`0600`), and interactive onboarding flow.
- `tests/test_session_manager.py`: Session CRUD, persistence, legacy migration, token/duration stats, and LRU command tracking.
- `tests/test_ui_keyboards.py`: Project switchers, dynamic `agy models` parsing and fallback discovery, effort keyboards, adaptive LRU menus, and WebApp buttons.
- `tests/test_webapp_server.py`: Async WebApp lifecycle, document registration, Telegram WebApp SDK injection, and `/health` endpoint.
- `tests/test_message_formatter.py`: HTML escaping, Markdown-to-HTML conversion, protocol extraction (`[AGY_FILE]`, `[AGY_ACTIONS]`, `[AGY_VOICE]`), spoken summary distillation, and mobile-responsive HTML exports.
- `tests/test_dir_tree.py`: Directory tree scanning and interactive HTML navigator generation.
- `tests/test_file_renderers.py`: Syntax highlighting, Markdown, diff/patch, CSV table rendering, and binary fallback.
- `tests/test_agy_bridge.py`: Command construction, `stream-json` parsing, keep-alive process pooling, `steer()`, and project switching.
- `tests/test_telegram_client.py`: API calls, HTML fallback, multipart uploads, voice downloads, and emoji reactions.
- `tests/test_bot.py`: Slash command routing, unauthorized chat blocking, streaming pipeline, mid-flight steering rotation, voice/document handlers, TTS auto-send, and network error resilience.
- `tests/test_ipc_router.py`: `[AGY_IPC]` parsing, envelope generation, pinned card formatting, and audit logging.
- `tests/test_transcriber.py`: `faster-whisper` singleton caching and async audio transcription.
- `tests/test_voice_tts.py`: Bilingual TTS synthesis and OGG Opus output.
- `tests/test_smoke_agy.py`: Live `agy` CLI smoke test.

---

## Contributing

Contributions are welcome! Feel free to open an Issue or submit a Pull Request:

1. Fork the repository and create a feature or fix branch.
2. Keep all source code, comments, docstrings, UI strings, and documentation in **English**.
3. Run the full test suite (`conda run -n agy-telegram pytest -v`) and ensure all tests pass.
4. Never commit `.env`, `runtime/`, credentials, or personal paths.

---

## License

Released under the [MIT License](LICENSE).
