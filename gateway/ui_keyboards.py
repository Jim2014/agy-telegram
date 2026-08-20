import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.message_formatter import escape_html


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
    {"command": "stop", "description": "🛑 Interrupt running agy prompt", "default_priority": 25},
    {"command": "reset", "description": "🔄 Clear active conversation", "default_priority": 20},
    {"command": "delete", "description": "🗑️ Delete a project (/delete <name>)", "default_priority": 10},
]


def build_adaptive_bot_commands(
    session: Optional[Any] = None,
    max_commands: int = 8,
) -> List[Dict[str, str]]:
    """
    Generate an adaptive, LRU-sorted command list for the Telegram slash menu.
    - /help is always pinned at index 0.
    - Remaining commands are sorted by (usage_frequency * 1000 + last_used_timestamp, default_priority).
    - Returns top `max_commands` items to keep the mobile menu clean and uncluttered.
    """
    stats = getattr(session, "command_stats", {}) if session else {}
    last_used = getattr(session, "command_last_used", {}) if session else {}

    pinned: List[Dict[str, str]] = []
    others: List[Tuple[float, Dict[str, str]]] = []

    for spec in ALL_BOT_COMMAND_SPECS:
        cmd_name = spec["command"]
        desc = spec["description"]
        entry = {"command": cmd_name, "description": desc}

        if spec.get("pinned"):
            pinned.append(entry)
            continue

        count = stats.get(cmd_name, 0)
        ts = last_used.get(cmd_name, 0.0)
        default_prio = spec.get("default_priority", 0)

        # Recency & frequency score
        score = (count * 1000.0) + (ts / 1e8 if ts > 0 else 0) + (default_prio * 0.1)
        others.append((score, entry))

    # Sort others by score descending
    others.sort(key=lambda x: x[0], reverse=True)

    result = list(pinned)
    for _, item in others:
        if len(result) >= max_commands:
            break
        result.append(item)

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


def build_model_keyboard(
    current_model: str,
    current_effort: str,
) -> Tuple[str, Dict[str, Any]]:
    """Build model configuration message text and inline keyboard markup."""
    models = [
        {
            "id": "gemini-3.7-flash",
            "name": "Gemini 3.7 Flash",
            "icon": "⚡",
            "desc": "Fast multimodal model with dynamic reasoning",
        },
        {
            "id": "gemini-3.1-pro",
            "name": "Gemini 3.1 Pro",
            "icon": "🧠",
            "desc": "State-of-the-art coding and deep reasoning",
        },
    ]

    lines = [
        "🤖 <b>Model & Reasoning Configuration:</b>\n",
        f"• <b>Active Model:</b> <code>{escape_html(current_model)}</code>",
        f"• <b>Active Effort:</b> <code>{escape_html(current_effort)}</code>\n",
        "<b>Available Models:</b>",
    ]

    buttons: List[List[Dict[str, str]]] = []
    for m in models:
        is_active = current_model == m["id"]
        marker = "🌟 <b>[ACTIVE]</b> " if is_active else "• "
        lines.append(f"{marker}{m['icon']} <b>{m['name']}</b> (<code>{m['id']}</code>)\n   <i>{m['desc']}</i>")
        btn_label = f"✓ {m['name']}" if is_active else f"{m['icon']} {m['name']}"
        buttons.append([{"text": btn_label, "callback_data": f"model:{m['id']}"}])

    buttons.append([
        {"text": "⚡ Adjust Effort", "callback_data": "effort:show"},
        {"text": "📊 Status", "callback_data": "status"},
    ])

    lines.append("\n💡 <i>Tap a button below to switch model instantly.</i>")
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


if __name__ == "__main__":
    msg, kb = build_model_keyboard("gemini-3.7-flash", "high")
    print("build_model_keyboard output preview:\n", msg)
    adaptive = build_adaptive_bot_commands(None, max_commands=8)
    print("\nDefault Adaptive Top Commands (8 items):")
    for cmd in adaptive:
        print(f"• /{cmd['command']} - {cmd['description']}")

