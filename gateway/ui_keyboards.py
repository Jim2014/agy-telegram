import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.message_formatter import escape_html


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


if __name__ == "__main__":
    msg, kb = build_model_keyboard("gemini-3.7-flash", "high")
    print("build_model_keyboard output preview:\n", msg)
