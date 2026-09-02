import asyncio
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.message_formatter import escape_html

logger = logging.getLogger(__name__)


# Master list of all supported bot commands with default priority rank
ALL_BOT_COMMAND_SPECS: List[Dict[str, Any]] = [
    {"command": "help", "description": "📖 Manual & interactive dashboard", "default_priority": 100, "pinned": True},
    {"command": "usage", "description": "🪙 View quota & remaining limits", "default_priority": 90},
    {"command": "status", "description": "📊 Active project & metrics", "default_priority": 85},
    {"command": "projects", "description": "📁 List & switch workspaces", "default_priority": 80},
    {"command": "open", "description": "📂 Open/switch project (/open <name>)", "default_priority": 75},
    {"command": "new", "description": "✨ New project (/new <name> <path>)", "default_priority": 70},
    {"command": "model", "description": "🧠 Switch model (Flash/Pro)", "default_priority": 65},
    {"command": "effort", "description": "⚡ Reasoning effort (low/med/high)", "default_priority": 60},
    {"command": "skills", "description": "🧩 List active skills & plugins", "default_priority": 55},
    {"command": "artifacts", "description": "📦 Browse & download artifacts", "default_priority": 50},
    {"command": "dir", "description": "📁 Browse workspace file tree", "default_priority": 45},
    {"command": "file", "description": "📎 Send workspace file document", "default_priority": 40},
    {"command": "export", "description": "📄 Export conversation as HTML", "default_priority": 35},
    {"command": "steer", "description": "🎯 Steer running prompt", "default_priority": 30},
    {"command": "voice", "description": "🎙️ Configure voice notes mode", "default_priority": 28},
    {"command": "pin", "description": "📌 Pin workspace status banner", "default_priority": 26},
    {"command": "stop", "description": "🛑 Interrupt running agy prompt", "default_priority": 25},
    {"command": "reset", "description": "🔄 Clear active conversation", "default_priority": 20},
    {"command": "delete", "description": "🗑️ Delete a project (/delete <name>)", "default_priority": 10},
]

# Canonical command alias mapping
COMMAND_ALIASES: Dict[str, str] = {
    "start": "help",
    "help": "help",
    "usage": "usage",
    "quota": "usage",
    "status": "status",
    "projects": "projects",
    "open": "open",
    "new": "new",
    "model": "model",
    "effort": "effort",
    "skills": "skills",
    "plugins": "skills",
    "artifacts": "artifacts",
    "scratch": "artifacts",
    "dir": "dir",
    "ls": "dir",
    "file": "file",
    "export": "export",
    "steer": "steer",
    "voice": "voice",
    "tts": "voice",
    "audio": "voice",
    "pin": "pin",
    "banner": "pin",
    "stop": "stop",
    "reset": "reset",
    "delete": "delete",
}


def build_adaptive_bot_commands(
    session: Optional[Any] = None,
    max_commands: Optional[int] = None,
) -> List[Dict[str, str]]:
    """
    Generate an adaptive, pure LRU-sorted command list for the Telegram slash menu.
    - /help is always permanently fixed / pinned at index 0.
    - Remaining commands are sorted by LRU (last_used_timestamp descending),
      falling back to default_priority descending for unused commands.
    - Returns all supported bot commands (or up to `max_commands` if explicitly passed).
    """
    last_used = getattr(session, "command_last_used", {}) if session else {}

    pinned: List[Dict[str, str]] = []
    others: List[Tuple[float, int, Dict[str, str]]] = []

    for spec in ALL_BOT_COMMAND_SPECS:
        cmd_name = spec["command"]
        desc = spec["description"]
        entry = {"command": cmd_name, "description": desc}

        if spec.get("pinned"):
            pinned.append(entry)
            continue

        ts = float(last_used.get(cmd_name, 0.0))
        default_prio = int(spec.get("default_priority", 0))

        # Pure LRU sort key: (-ts, -default_prio)
        # Most recently used (highest timestamp) bubbles to the top right after /help.
        # Unused commands (ts == 0.0) preserve their relative default priority order.
        others.append((-ts, -default_prio, entry))

    # Sort others by (-ts, -default_prio)
    others.sort(key=lambda x: (x[0], x[1]))

    result = list(pinned) + [item for _, _, item in others]
    if max_commands is not None and max_commands > 0:
        result = result[:max_commands]

    return result


def build_projects_keyboard(
    projects: Dict[str, Any],
    active_project: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    """Build the interactive projects list message text and inline keyboard markup."""
    if not projects:
        text = (
            "📂 <b>No projects configured yet.</b>\n\n"
            "Use <code>/new &lt;name&gt; &lt;path&gt;</code> to create your first project workspace!"
        )
        return text, {"inline_keyboard": []}

    lines = ["📁 <b>Available Projects:</b>\n"]
    keyboard: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []

    for name, proj in projects.items():
        is_active = name == active_project
        active_marker = "🌟 <b>[ACTIVE]</b> " if is_active else "• "
        conv_id = proj.get("conversation_id")
        conv_str = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(None)</i>"
        path_str = proj.get("path", "")
        lines.append(
            f"{active_marker}<b>{escape_html(name)}</b>\n"
            f"   Switch: <code>/open {escape_html(name)}</code>\n"
            f"   Path: <code>{escape_html(path_str)}</code>\n"
            f"   Session: {conv_str}\n"
        )

        btn_text = f"🌟 {name}" if is_active else f"📂 {name}"
        row.append({"text": btn_text, "callback_data": f"open:{name}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        {"text": "🔄 Refresh", "callback_data": "projects:refresh"},
        {"text": "📊 Status", "callback_data": "status"},
    ])

    lines.append("💡 <i>Tap any project button below to switch directly, or use /open &lt;name&gt;.</i>")
    return "\n".join(lines), {"inline_keyboard": keyboard}


DEFAULT_STATIC_MODELS: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "icon": "⚡",
        "desc": "Fast multimodal model with dynamic reasoning",
    },
    {
        "id": "gemini-3.1-pro",
        "name": "Gemini 3.1 Pro",
        "icon": "🧠",
        "desc": "State-of-the-art coding and deep reasoning",
    },
    {
        "id": "gemini-3.7-flash",
        "name": "Gemini 3.7 Flash",
        "icon": "⚡",
        "desc": "Multimodal hybrid reasoning model",
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "icon": "🟣",
        "desc": "Anthropic Claude 4.6 (Thinking)",
    },
    {
        "id": "claude-opus-4-6-thinking",
        "name": "Claude Opus 4.6",
        "icon": "🎭",
        "desc": "Anthropic Claude 4.6 Opus (Thinking)",
    },
    {
        "id": "gpt-oss-120b-medium",
        "name": "GPT-OSS 120B",
        "icon": "🤖",
        "desc": "High-parameter open-weights model",
    },
]

_CACHED_MODELS: Optional[List[Dict[str, Any]]] = None
_CACHED_MODELS_EXPIRY: float = 0.0


def parse_agy_models(output: str) -> List[Dict[str, Any]]:
    """Parse 'agy models' CLI stdout into structured model dictionaries."""
    models: List[Dict[str, Any]] = []
    seen_base_ids: Set[str] = set()

    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Fetching") or line.startswith("Usage:") or line.startswith("List"):
            continue
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        raw_id = parts[0].strip()
        raw_name = parts[1].strip() if len(parts) > 1 else raw_id

        # Determine base id (for Gemini family, strip -high/-medium/-low for clean deduplication)
        if raw_id.startswith("gemini-"):
            base_id = re.sub(r"-(high|medium|low)$", "", raw_id)
        else:
            base_id = raw_id

        if base_id in seen_base_ids:
            continue
        seen_base_ids.add(base_id)

        # Determine clean display name
        clean_name = re.sub(r"\s*\((High|Medium|Low|Thinking)\)", "", raw_name).strip()
        if not clean_name or clean_name == base_id:
            clean_name = " ".join(word.capitalize() for word in base_id.split("-"))

        if "flash" in base_id.lower():
            icon = "⚡"
            desc = "Fast multimodal model with dynamic reasoning"
        elif "pro" in base_id.lower():
            icon = "🧠"
            desc = "Deep reasoning and complex coding"
        elif "claude" in base_id.lower():
            icon = "🟣"
            desc = "Anthropic reasoning model"
        elif "gpt" in base_id.lower() or "oss" in base_id.lower():
            icon = "🤖"
            desc = "Open-weights 120B model"
        else:
            icon = "✨"
            desc = "Antigravity CLI model"

        models.append({
            "id": base_id,
            "raw_id": raw_id,
            "name": clean_name,
            "icon": icon,
            "desc": desc,
        })

    return models


async def fetch_agy_models(
    agy_bin: str = "/Users/jim/.local/bin/agy",
    force_refresh: bool = False,
    timeout: float = 6.0,
) -> List[Dict[str, Any]]:
    """Query 'agy models' asynchronously with caching and fallback."""
    global _CACHED_MODELS, _CACHED_MODELS_EXPIRY

    now = time.time()
    if not force_refresh and _CACHED_MODELS and now < _CACHED_MODELS_EXPIRY:
        return _CACHED_MODELS

    # Resolve executable
    resolved_bin = agy_bin
    if not os.path.isfile(resolved_bin):
        resolved_bin = shutil.which(agy_bin) or "/Users/jim/.local/bin/agy"

    if not os.path.isfile(resolved_bin) and not shutil.which(resolved_bin):
        logger.warning("agy binary not found at %s, using fallback models.", resolved_bin)
        return DEFAULT_STATIC_MODELS

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved_bin,
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout_bytes.decode("utf-8", errors="ignore")
        parsed = parse_agy_models(output)
        if parsed:
            _CACHED_MODELS = parsed
            _CACHED_MODELS_EXPIRY = now + 600.0  # 10 minutes cache
            logger.info("Successfully discovered %d models from agy CLI", len(parsed))
            return parsed
    except Exception as e:
        logger.warning("Failed to fetch models from agy CLI: %s", e)

    return _CACHED_MODELS or DEFAULT_STATIC_MODELS


def build_model_keyboard(
    current_model: str,
    current_effort: str,
    models: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Build model configuration message text and inline keyboard markup."""
    model_list = models or _CACHED_MODELS or DEFAULT_STATIC_MODELS

    lines = [
        "🤖 <b>Model & Reasoning Configuration:</b>\n",
        f"• <b>Active Model:</b> <code>{escape_html(current_model)}</code>",
        f"• <b>Active Effort:</b> <code>{escape_html(current_effort)}</code>\n",
        "<b>Available Models (Auto-detected from agy):</b>",
    ]

    buttons: List[List[Dict[str, str]]] = []
    for m in model_list:
        is_active = (
            current_model == m["id"]
            or current_model == m.get("raw_id")
            or current_model.startswith(m["id"])
        )
        marker = "🌟 <b>[ACTIVE]</b> " if is_active else "• "
        lines.append(f"{marker}{m['icon']} <b>{m['name']}</b> (<code>{m['id']}</code>)\n   <i>{m['desc']}</i>")
        btn_label = f"✓ {m['name']}" if is_active else f"{m['icon']} {m['name']}"
        buttons.append([{"text": btn_label, "callback_data": f"model:{m['id']}"}])

    buttons.append([
        {"text": "⚡ Adjust Effort", "callback_data": "effort:show"},
        {"text": "🔄 Refresh", "callback_data": "model:refresh"},
        {"text": "📊 Status", "callback_data": "status"},
    ])

    lines.append("\n💡 <i>Tap any model button above to switch instantly, or use /model &lt;name&gt;.</i>")
    return "\n".join(lines), {"inline_keyboard": buttons}


def build_effort_keyboard(
    current_model: str,
    current_effort: str,
) -> Tuple[str, Dict[str, Any]]:
    """Build reasoning effort configuration message text and inline keyboard markup."""
    efforts = [
        {"id": "high", "name": "High", "icon": "🔴", "desc": "Maximum reasoning budget & deep multi-step planning (Recommended)"},
        {"id": "medium", "name": "Medium", "icon": "🟡", "desc": "Balanced reasoning depth and generation speed"},
        {"id": "low", "name": "Low", "icon": "🟢", "desc": "Minimal reasoning latency for quick routine tasks"},
    ]

    lines = [
        "⚙️ <b>Reasoning Effort Setting:</b>\n",
        f"• <b>Active Model:</b> <code>{escape_html(current_model)}</code>",
        f"• <b>Active Effort:</b> <code>{escape_html(current_effort)}</code>\n",
        "<b>Available Effort Levels:</b>",
    ]

    effort_buttons: List[Dict[str, str]] = []
    for eff in efforts:
        is_active = current_effort == eff["id"]
        marker = "🌟 <b>[ACTIVE]</b> " if is_active else "• "
        lines.append(f"{marker}{eff['icon']} <b>{eff['name']}</b> (<code>{eff['id']}</code>)\n   <i>{eff['desc']}</i>")
        btn_label = f"✓ {eff['icon']} {eff['name']}" if is_active else f"{eff['icon']} {eff['name']}"
        effort_buttons.append({"text": btn_label, "callback_data": f"effort:{eff['id']}"})

    keyboard = [
        effort_buttons,
        [
            {"text": "🤖 Switch Model", "callback_data": "model:show"},
            {"text": "📊 Status", "callback_data": "status"},
        ],
    ]

    lines.append("\n💡 <i>Tap an option below to adjust reasoning effort instantly.</i>")
    return "\n".join(lines), {"inline_keyboard": keyboard}


def build_help_keyboard() -> Dict[str, Any]:
    """Build the master command center inline keyboard for /help."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "status"},
                {"text": "🪙 Quota & Usage", "callback_data": "usage:show"},
            ],
            [
                {"text": "📁 Projects", "callback_data": "projects:show"},
                {"text": "🧠 Model & Effort", "callback_data": "model:show"},
            ],
            [
                {"text": "🧩 Skills & Tools", "callback_data": "skills:show"},
                {"text": "📦 Artifacts", "callback_data": "artifacts:show"},
            ],
            [
                {"text": "📁 File Tree", "callback_data": "dir:show"},
                {"text": "📄 Export Report", "callback_data": "export:show"},
            ],
            [
                {"text": "🎙️ Voice Settings", "callback_data": "voice:show"},
            ],
        ]
    }


def build_usage_keyboard() -> Dict[str, Any]:
    """Build inline keyboard for /usage quota monitor."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh Quota", "callback_data": "usage:refresh"},
                {"text": "🧠 Switch Model", "callback_data": "model:show"},
            ],
            [
                {"text": "📊 Session Status", "callback_data": "status"},
                {"text": "📖 Help Menu", "callback_data": "help:show"},
            ],
        ]
    }


def build_skills_keyboard() -> Dict[str, Any]:
    """Build inline keyboard for /skills explorer."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh Skills", "callback_data": "skills:show"},
                {"text": "🪙 Check Quota", "callback_data": "usage:show"},
            ],
            [
                {"text": "📊 Status", "callback_data": "status"},
                {"text": "📖 Help Menu", "callback_data": "help:show"},
            ],
        ]
    }


def build_artifacts_keyboard(artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build inline keyboard for /artifacts list with one-tap download buttons."""
    buttons: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []

    for idx, art in enumerate(artifacts[:6]):
        name = art.get("name", f"item_{idx+1}")
        short_name = name[:16] + ("..." if len(name) > 16 else "")
        row.append({"text": f"📥 {short_name}", "callback_data": f"artifact:dl:{idx}"})
        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([
        {"text": "🔄 Refresh", "callback_data": "artifacts:show"},
        {"text": "📁 File Tree", "callback_data": "dir:show"},
    ])

    return {"inline_keyboard": buttons}


def build_decision_keyboard(
    options: List[str],
) -> Dict[str, Any]:
    """
    Build an interactive inline keyboard for ask_question choices.
    Buttons trigger callback_data 'dec:<index>'.
    """
    keyboard: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []

    for idx, opt in enumerate(options):
        btn_label = opt[:28] + ("..." if len(opt) > 28 else "")
        row.append({"text": f"👉 {btn_label}", "callback_data": f"dec:{idx}"})
        if len(row) == 2 or len(opt) > 16:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return {"inline_keyboard": keyboard}


def build_actions_keyboard(
    actions: List[Any],
) -> Dict[str, Any]:
    """
    Build an inline keyboard for [AGY_ACTIONS] suggested next steps.
    Buttons trigger callback_data 'act:<index>'.
    """
    keyboard: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []

    for idx, item in enumerate(actions[:6]):
        label = getattr(item, "label", str(item))
        btn_label = label[:26] + ("..." if len(label) > 26 else "")
        row.append({"text": btn_label, "callback_data": f"act:{idx}"})
        if len(row) == 2 or len(label) > 14:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return {"inline_keyboard": keyboard}


def build_reply_keyboard(
    placeholder: str = "💬 Message agy or type /help...",
    is_busy: bool = False,
) -> Dict[str, Any]:
    """
    Build a persistent reply keyboard with dynamic input field placeholder.
    Provides quick tap shortcuts without obscuring message history.
    """
    buttons = [
        [
            {"text": "📊 Status"},
            {"text": "🪙 Quota"},
            {"text": "📁 Projects"},
            {"text": "📖 Help"},
        ]
    ]
    return {
        "keyboard": buttons,
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": placeholder[:64],
    }


def build_pinned_banner_keyboard() -> Dict[str, Any]:
    """
    Build quick-access inline keyboard for the pinned status banner.
    """
    buttons = [
        [
            {"text": "📊 Status", "callback_data": "status:show"},
            {"text": "📁 Projects", "callback_data": "projects:list"},
        ],
        [
            {"text": "🪙 Quota", "callback_data": "usage:show"},
            {"text": "📖 Help", "callback_data": "help:show"},
        ],
    ]
    return {"inline_keyboard": buttons}


def build_webapp_viewer_button(
    url: str,
    title: str = "🌐 Open in Telegram",
) -> Dict[str, Any]:
    """
    Build an inline button that launches Telegram WebApp popup.
    Telegram API strictly requires HTTPS for web_app buttons; falls back to standard url for HTTP.
    """
    if url.startswith("https://"):
        return {"text": title, "web_app": {"url": url}}
    return {"text": title, "url": url}


def build_voice_keyboard(current_mode: str) -> Tuple[str, Dict[str, Any]]:
    """
    Build interactive inline keyboard for selecting TTS Voice Broadcast mode.
    Options:
      - disable: Disable voice feature completely (no script, no audio).
      - ondemand: Extract voice script, synthesize & send audio only upon clicking [ 🔊 Play Audio ].
      - autosend: Automatically synthesize and send native voice note directly with reply.
    """
    raw_mode = (current_mode.lower() if current_mode else "ondemand").strip()
    if raw_mode in ("autogen", "script", "manual"):
        mode = "ondemand"
    elif raw_mode in ("autoplay", "direct", "auto"):
        mode = "autosend"
    elif raw_mode in ("disable", "off"):
        mode = "disable"
    else:
        mode = "ondemand"

    modes = [
        ("disable", "🔴 Disabled (No Voice)", "Disable voice script and audio generation entirely"),
        ("ondemand", "📝 On-Demand (Synthesize on Click)", "Extract voice script; synthesize audio only upon clicking button"),
        ("autosend", "⚡ Auto-Send (Send Voice Directly)", "Automatically synthesize and send native voice note with reply"),
    ]
    buttons = []
    for m_id, label, _ in modes:
        prefix = "🔘 " if m_id == mode else "⚪ "
        buttons.append([{"text": f"{prefix}{label}", "callback_data": f"tts:{m_id}"}])

    buttons.append([{"text": "📖 Back to Help", "callback_data": "help:show"}])

    text = (
        "🎙️ <b>Voice Broadcast Settings</b>\n\n"
        f"• <b>Current Mode:</b> <code>{mode}</code>\n"
        "• <b>Synthesizer:</b> Edge-TTS (Neural Voices) + macOS Offline Fallback\n"
        "• <b>Language Adaptation:</b> Automatic (Xiaoxiao for Chinese / Jenny for English)\n\n"
        "<b>Available Modes:</b>\n"
        "• <b>🔴 Disabled:</b> Turn off voice scripts and audio synthesis completely.\n"
        "• <b>📝 On-Demand:</b> Extract voice script without synthesizing upfront; audio is generated and sent only when you click <code>[ 🔊 Send Audio ]</code>.\n"
        "• <b>⚡ Auto-Send:</b> Automatically synthesize and send a native Telegram voice note directly with each reply.\n\n"
        "💡 <i>Tap an option below or run <code>/voice &lt;disable|ondemand|autosend&gt;</code> in chat.</i>"
    )
    return text, {"inline_keyboard": buttons}


def build_voice_play_button(voice_id: str) -> Dict[str, Any]:
    """Build an inline button to generate/send the voice note to chat."""
    return {"text": "🔊 Send Audio", "callback_data": f"voice:{voice_id}"}


def build_ipc_accordion_keyboard(target_project: str) -> Dict[str, Any]:
    """Build an inline keyboard with an unpin button for background IPC cards."""
    return {
        "inline_keyboard": [
            [
                {"text": "📌 Unpin", "callback_data": f"unpin_ipc:{target_project}"}
            ]
        ]
    }


if __name__ == "__main__":
    msg, kb = build_model_keyboard("gemini-3.8-flash", "high")
    print("build_model_keyboard output preview:\n", msg)
    adaptive = build_adaptive_bot_commands(None, max_commands=8)
    print("\nDefault Adaptive Top Commands (8 items):")
    for cmd in adaptive:
        print(f"• /{cmd['command']} - {cmd['description']}")

