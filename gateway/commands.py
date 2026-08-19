from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.dir_tree import generate_dir_tree_html, scan_directory_tree
from gateway.file_renderers import render_file_to_html
from gateway.message_formatter import escape_html, generate_standalone_html
from gateway.ui_keyboards import (
    build_effort_keyboard,
    build_model_keyboard,
    build_projects_keyboard,
)

logger = logging.getLogger(__name__)


async def handle_cmd_help(bot: Any, chat_id: int) -> None:
    """Send help message."""
    session = bot.session_manager.get_session(chat_id)
    active_proj = session.active_project or "(none)"
    conv_id = session.conversation_id
    conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"
    help_text = (
        "🤖 <b>agy-telegram Gateway</b>\n\n"
        "Direct Telegram interface to <b>agy</b> (Antigravity CLI) with project-based session management, real-time streaming, mid-flight steering, and HTML document exports.\n\n"
        "<b>Project Management:</b>\n"
        "• <code>/new &lt;name&gt; &lt;path&gt;</code> — Create a new project workspace\n"
        "• <code>/open &lt;name&gt;</code> — Open and switch to an existing project\n"
        "• <code>/projects</code> — List all projects with interactive switch buttons\n"
        "• <code>/delete &lt;name&gt;</code> — Delete a project\n"
        "• <code>/reset</code> — Clear conversation history for active project\n\n"
        "<b>Files & Directory Tree:</b>\n"
        "• <code>/dir</code> or <code>/ls [path]</code> — Browse workspace file tree & interactive HTML navigator\n"
        "• <code>/file &lt;path&gt;</code> — Send workspace file document to Telegram\n"
        "• <code>/export</code> — Export current conversation report as standalone HTML\n\n"
        "<b>Configuration & Control:</b>\n"
        "• <code>/status</code> — Show active project, conversation, model, & effort\n"
        "• <code>/model</code> — Switch model (Gemini 3.7 Flash / Gemini 3.1 Pro)\n"
        "• <code>/effort</code> — Set reasoning effort (low | medium | high)\n"
        "• <code>/steer &lt;instruction&gt;</code> — Steer/guide currently running agy prompt\n"
        "• <code>/stop</code> — Interrupt currently executing agy prompt\n"
        "<b>Interaction:</b>\n"
        "• Send text messages to converse directly with agy\n"
        "• 🎙️ Send voice/audio messages — agy will inspect, transcribe, and execute voice instructions\n"
        "• Send text/voice while a task is running to <b>steer</b> mid-flight\n\n"
        f"<b>Active Project:</b> <code>{escape_html(active_proj)}</code> ({conv_text})\n\n"
        "💡 <i>Tip: Long responses, code diffs, and test reports are automatically generated and sent as formatted HTML file attachments!</i>"
    )
    await bot._safe_send_message(chat_id=chat_id, text=help_text)


async def handle_cmd_new(bot: Any, chat_id: int, arg: str) -> None:
    """Create a new project workspace (/new <name> <path>) or open if exists."""
    if bot.session_manager.is_busy(chat_id):
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ A task is currently running. Please /stop it first before switching projects.",
        )
        return

    parts = arg.split(maxsplit=1) if arg else []
    session = bot.session_manager.get_session(chat_id)

    if len(parts) == 0:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Usage: <code>/new &lt;name&gt; &lt;path&gt;</code>\nCreate and switch to a new project workspace.",
        )
        return

    name = parts[0].strip()

    # If project already exists, treat as /open
    if name in session.projects:
        bot.session_manager.open_project(chat_id, name)
        path_val = session.active_path() or ""
        conv_id = session.conversation_id
        conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"
        await bot._safe_send_message(
            chat_id=chat_id,
            text=(
                f"📂 Project <b>{escape_html(name)}</b> already exists. Switched to it!\n"
                f"• <b>Path:</b> <code>{escape_html(path_val)}</code>\n"
                f"• <b>Conversation:</b> {conv_text}"
            ),
        )
        return

    # Name does not exist yet; requires path
    if len(parts) < 2:
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"⚠️ Usage: <code>/new {escape_html(name)} &lt;path&gt;</code>\nPlease provide the directory path for the new project.",
        )
        return

    path_str = parts[1].strip()
    target_path = str(Path(path_str).expanduser().resolve())
    bot.session_manager.create_project(chat_id, name, target_path)

    await bot._safe_send_message(
        chat_id=chat_id,
        text=(
            f"✨ <b>Project created and activated!</b>\n"
            f"• <b>Project:</b> <code>{escape_html(name)}</code>\n"
            f"• <b>Path:</b> <code>{escape_html(target_path)}</code>\n"
            f"• <b>Conversation:</b> <i>(New session)</i>"
        ),
    )


async def handle_cmd_open(bot: Any, chat_id: int, arg: str) -> None:
    """Open and switch to an existing project (/open <name>)."""
    if bot.session_manager.is_busy(chat_id):
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ A task is currently running. Please /stop it first before switching projects.",
        )
        return

    if not arg:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Usage: <code>/open &lt;name&gt;</code>\nUse <code>/projects</code> to see available projects.",
        )
        return

    name = arg.strip()
    session = bot.session_manager.get_session(chat_id)

    if name not in session.projects:
        projects = bot.session_manager.list_projects(chat_id)
        if projects:
            plist = "\n".join([f"• <code>{escape_html(p)}</code>" for p in projects.keys()])
            msg = f"❌ Project <code>{escape_html(name)}</code> not found.\n\n<b>Available projects:</b>\n{plist}\n\nUse <code>/projects</code> to view details."
        else:
            msg = f"❌ Project <code>{escape_html(name)}</code> not found. No projects exist yet.\nUse <code>/new &lt;name&gt; &lt;path&gt;</code> to create one."
        await bot._safe_send_message(chat_id=chat_id, text=msg)
        return

    bot.session_manager.open_project(chat_id, name)
    path_val = session.active_path() or ""
    conv_id = session.conversation_id
    conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"

    await bot._safe_send_message(
        chat_id=chat_id,
        text=(
            f"📂 <b>Opened project:</b> <code>{escape_html(name)}</code>\n"
            f"• <b>Path:</b> <code>{escape_html(path_val)}</code>\n"
            f"• <b>Conversation:</b> {conv_text}"
        ),
    )


async def handle_cmd_projects(bot: Any, chat_id: int, edit_message_id: Optional[int] = None) -> None:
    """List all projects in current session with interactive inline option buttons."""
    session = bot.session_manager.get_session(chat_id)
    projects = session.projects
    full_text, reply_markup = build_projects_keyboard(projects, session.active_project)

    if edit_message_id:
        await bot._safe_edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=full_text,
            reply_markup=reply_markup,
        )
    else:
        await bot._safe_send_message(
            chat_id=chat_id,
            text=full_text,
            reply_markup=reply_markup,
        )


async def handle_cmd_delete(bot: Any, chat_id: int, arg: str) -> None:
    """Delete a project workspace (/delete <name>)."""
    if bot.session_manager.is_busy(chat_id):
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ A task is currently running. Please /stop it first before deleting projects.",
        )
        return

    if not arg:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Usage: <code>/delete &lt;name&gt;</code>\nDelete a project workspace from session manager.",
        )
        return

    name = arg.strip()
    session = bot.session_manager.get_session(chat_id)

    if name not in session.projects:
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"❌ Project <code>{escape_html(name)}</code> does not exist.",
        )
        return

    bot.session_manager.delete_project(chat_id, name)
    await bot._safe_send_message(
        chat_id=chat_id,
        text=f"🗑️ Project <b>{escape_html(name)}</b> deleted successfully.",
    )


async def handle_cmd_reset(bot: Any, chat_id: int) -> None:
    """Reset conversation_id for active project."""
    if bot.session_manager.is_busy(chat_id):
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ A task is currently running. Please /stop it first before resetting.",
        )
        return

    session = bot.session_manager.get_session(chat_id)
    active = session.active_project
    bot.session_manager.reset_session(chat_id)

    proj_info = f" for project <b>{escape_html(active)}</b>" if active else ""
    await bot._safe_send_message(
        chat_id=chat_id,
        text=f"🔄 <b>Session reset{proj_info}.</b> Next message will start a fresh conversation.",
    )


async def handle_cmd_status(bot: Any, chat_id: int) -> None:
    """Display current bot & session status with cumulative conversation statistics."""
    session = bot.session_manager.get_session(chat_id)
    conv_id = session.conversation_id or "<i>(None - new conversation)</i>"
    if session.conversation_id:
        conv_id = f"<code>{escape_html(session.conversation_id)}</code>"

    active_proj = session.active_project or "<i>(default / none)</i>"
    active_path = session.active_path() or bot.config.agy_working_dir or os.getcwd()
    model_str = session.model or bot.config.default_model or "gemini-3.7-flash"
    effort_str = session.effort or bot.config.default_effort or "high"
    busy_status = "🔴 Busy (Running prompt)" if session.is_busy else "🟢 Idle"

    # Startup time + uptime
    started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(bot.started_at))
    uptime = max(0, int(time.time() - bot.started_at))
    if uptime < 60:
        uptime_str = f"{uptime}s"
    elif uptime < 3600:
        uptime_str = f"{uptime // 60}m {uptime % 60}s"
    else:
        uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m"

    # Cumulative session statistics
    active_stats = bot.session_manager.get_active_stats(chat_id)
    total_turns = active_stats.get("total_turns", 0)
    cum_tokens = active_stats.get("total_tokens", 0)
    cum_thinking = active_stats.get("thinking_tokens", 0)
    cum_duration = active_stats.get("total_duration_seconds", 0.0)
    session_start = active_stats.get("session_started_at", session.created_at)

    if cum_tokens >= 1_000_000:
        tok_str = f"{cum_tokens / 1_000_000:.2f}M ({cum_tokens:,})"
    elif cum_tokens >= 1_000:
        tok_str = f"{cum_tokens / 1_000:.1f}k ({cum_tokens:,})"
    else:
        tok_str = f"{cum_tokens}"

    if cum_thinking:
        tok_str += f" (thinking: {cum_thinking:,})"

    session_age = max(0, int(time.time() - session_start))
    if session_age < 60:
        sess_age_str = f"{session_age}s"
    elif session_age < 3600:
        sess_age_str = f"{session_age // 60}m {session_age % 60}s"
    else:
        sess_age_str = f"{session_age // 3600}h {(session_age % 3600) // 60}m"

    status_text = (
        "📊 <b>Session & System Status:</b>\n\n"
        f"• <b>Status:</b> {busy_status}\n"
        f"• <b>Gateway Uptime:</b> <code>{uptime_str}</code> (started <code>{started_str}</code>)\n"
        f"• <b>Active Project:</b> <code>{escape_html(active_proj)}</code>\n"
        f"• <b>Working Directory:</b> <code>{escape_html(active_path)}</code>\n"
        f"• <b>Model:</b> <code>{escape_html(model_str)}</code>\n"
        f"• <b>Effort:</b> <code>{escape_html(effort_str)}</code>\n\n"
        "📈 <b>Active Session Cumulative Stats:</b>\n"
        f"• <b>Conversation ID:</b> {conv_id}\n"
        f"• <b>Session Age:</b> <code>{sess_age_str}</code>\n"
        f"• <b>Total Turns:</b> <code>{total_turns}</code>\n"
        f"• <b>Total Tokens:</b> <code>{tok_str}</code>\n"
        f"• <b>Cumulative Runtime:</b> <code>{cum_duration:.1f}s</code>\n\n"
        f"📁 <b>Configured Projects:</b> {len(session.projects)}\n"
    )
    await bot._safe_send_message(chat_id=chat_id, text=status_text)


async def handle_cmd_model(bot: Any, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
    """Change or view model with interactive option buttons showing active status."""
    session = bot.session_manager.get_session(chat_id)
    if arg and arg.lower() != "show":
        val = arg.strip()
        if val.lower() in ("reset", "default"):
            bot.session_manager.set_model(chat_id, None)
        else:
            bot.session_manager.set_model(chat_id, val)

    cur_model = session.model or bot.config.default_model or "gemini-3.7-flash"
    cur_effort = session.effort or bot.config.default_effort or "high"

    text, reply_markup = build_model_keyboard(cur_model, cur_effort)

    if edit_message_id:
        await bot._safe_edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=reply_markup)
    else:
        await bot._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def handle_cmd_effort(bot: Any, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
    """Change reasoning effort with interactive option buttons showing active status."""
    session = bot.session_manager.get_session(chat_id)
    if arg and arg.lower() != "show":
        val = arg.strip().lower()
        if val not in ("low", "medium", "high"):
            await bot._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Invalid effort value. Allowed: <code>low</code>, <code>medium</code>, <code>high</code>.",
            )
            return
        bot.session_manager.set_effort(chat_id, val)

    cur_model = session.model or bot.config.default_model or "gemini-3.7-flash"
    cur_effort = session.effort or bot.config.default_effort or "high"

    text, reply_markup = build_effort_keyboard(cur_model, cur_effort)

    if edit_message_id:
        await bot._safe_edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=reply_markup)
    else:
        await bot._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def handle_cmd_dir(bot: Any, chat_id: int, arg: str = "", reply_to_id: Optional[int] = None) -> None:
    """Browse workspace directory and export interactive collapsible HTML file tree viewer."""
    session = bot.session_manager.get_session(chat_id)
    active_proj = session.active_project or "default"
    base_dir = session.active_path() or bot.config.agy_working_dir or os.getcwd()

    if arg:
        target_dir = (Path(base_dir) / arg.strip()).expanduser().resolve()
    else:
        target_dir = Path(base_dir).expanduser().resolve()

    if not target_dir.is_dir():
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"⚠️ Directory does not exist: <code>{escape_html(str(target_dir))}</code>",
            reply_to_message_id=reply_to_id,
        )
        return

    await bot._safe_send_chat_action(chat_id, "upload_document")
    scan_data = scan_directory_tree(target_dir)

    html_doc = generate_dir_tree_html(
        root_path=target_dir,
        project_name=active_proj,
        scan_data=scan_data,
    )

    rel_title = f"{active_proj}_{target_dir.name}" if target_dir.name != active_proj else active_proj
    filename = f"tree_{rel_title}.html"

    total_files = scan_data.get("total_files", 0)
    total_dirs = scan_data.get("total_dirs", 0)
    total_size = scan_data.get("total_size_str", "0 B")

    # Top level preview (up to 8 items)
    top_items = scan_data.get("tree", [])[:8]
    preview_lines: List[str] = []
    for item in top_items:
        icon = "📁" if item.get("is_dir") else item.get("icon", "📄")
        sz_badge = f" ({item.get('size_str')})" if not item.get("is_dir") else ""
        preview_lines.append(f"  {icon} <code>{escape_html(item['name'])}</code>{sz_badge}")

    if len(scan_data.get("tree", [])) > 8:
        preview_lines.append(f"  <i>...and {len(scan_data['tree']) - 8} more items</i>")

    preview_text = "\n".join(preview_lines) if preview_lines else "  <i>(Empty directory)</i>"

    caption = (
        f"📁 <b>{escape_html(active_proj)} Workspace Tree</b>\n\n"
        f"• <b>Folders:</b> {total_dirs} | <b>Files:</b> {total_files} | <b>Total:</b> {total_size}\n"
        f"• <b>Path:</b> <code>{escape_html(str(target_dir))}</code>\n\n"
        f"<b>Top-Level Preview:</b>\n{preview_text}\n\n"
        f"💡 <i>Open the attached HTML to expand/collapse folders and tap any file to copy its <code>/file</code> command!</i>"
    )

    doc_res = await bot._safe_send_document(
        chat_id=chat_id,
        document_content=html_doc,
        filename=filename,
        caption=caption,
        reply_to_message_id=reply_to_id,
    )

    if not doc_res:
        await bot._safe_send_message(
            chat_id=chat_id,
            text=caption,
            reply_to_message_id=reply_to_id,
        )


async def handle_cmd_file(bot: Any, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
    """Send a project workspace file as document (/file [-h] <relative_or_abs_path>)."""
    parts = arg.strip().split() if arg else []
    as_html = False
    path_parts: List[str] = []
    for p in parts:
        if p in ("-h", "--html", "-H"):
            as_html = True
        else:
            path_parts.append(p)

    file_arg = " ".join(path_parts).strip()
    if not file_arg:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Usage: <code>/file [-h] &lt;relative_or_abs_path&gt;</code>\nSend a workspace file directly to Telegram (use <code>-h</code> to render as HTML).",
            reply_to_message_id=reply_to_id,
        )
        return

    session = bot.session_manager.get_session(chat_id)
    active_proj = session.active_project or "default"
    base_dir = Path(session.active_path() or bot.config.agy_working_dir or os.getcwd())
    raw_path = Path(file_arg).expanduser()

    target_file = (base_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()

    if not target_file.is_file():
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"❌ File not found: <code>{escape_html(str(target_file))}</code>",
            reply_to_message_id=reply_to_id,
        )
        return

    size_kb = target_file.stat().st_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{target_file.stat().st_size} bytes"

    await bot._safe_send_chat_action(chat_id, "upload_document")

    if as_html:
        try:
            rel_path = str(target_file.relative_to(base_dir))
        except ValueError:
            rel_path = target_file.name

        is_converted, html_doc, out_filename = render_file_to_html(
            target_file=target_file,
            project_name=active_proj,
            rel_path=rel_path,
        )

        if is_converted:
            caption = f"🌐 <b>{escape_html(out_filename)}</b>\nHTML view of <code>{escape_html(target_file.name)}</code> ({size_str})"
            res = await bot._safe_send_document(
                chat_id=chat_id,
                document_content=html_doc,
                filename=out_filename,
                caption=caption,
                reply_to_message_id=reply_to_id,
            )
        else:
            # Binary or unsupported -> return original file directly
            caption = f"📄 <b>{escape_html(target_file.name)}</b> ({size_str})\n<i>(HTML conversion not supported for binary file, sent original)</i>"
            res = await bot._safe_send_document(
                chat_id=chat_id,
                document_content=target_file,
                filename=target_file.name,
                caption=caption,
                reply_to_message_id=reply_to_id,
            )
    else:
        # Raw file
        res = await bot._safe_send_document(
            chat_id=chat_id,
            document_content=target_file,
            filename=target_file.name,
            caption=f"📄 <b>{escape_html(target_file.name)}</b> ({size_str})",
            reply_to_message_id=reply_to_id,
        )

    if not res:
        logger.error("Failed to send file %s to chat %d", target_file, chat_id)
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"❌ Failed to send document: <code>{escape_html(target_file.name)}</code>",
            reply_to_message_id=reply_to_id,
        )


async def handle_cmd_export(bot: Any, chat_id: int, reply_to_id: Optional[int] = None) -> None:
    """Export current session status and conversation report as standalone HTML document."""
    session = bot.session_manager.get_session(chat_id)
    active_proj = session.active_project or "default"
    active_path = session.active_path() or bot.config.agy_working_dir or os.getcwd()
    conv_id = session.conversation_id or "New session"

    report_md = f"""# Project Report: {active_proj}

### Workspace Details
- **Project Name:** `{active_proj}`
- **Working Directory:** `{active_path}`
- **Conversation ID:** `{conv_id}`
- **Model:** `{session.model or bot.config.default_model or 'Default'}`
- **Effort:** `{session.effort or bot.config.default_effort or 'medium'}`

### Recent Status
Session is currently {'busy' if session.is_busy else 'idle and ready for tasks'}.
"""
    html_doc = generate_standalone_html(
        title=f"Session Report - {active_proj}",
        markdown_content=report_md,
        metadata={
            "model": session.model or bot.config.default_model,
            "conversation_id": session.conversation_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    filename = f"session_report_{active_proj}_{int(time.time())}.html"
    await bot._safe_send_chat_action(chat_id, "upload_document")
    await bot._safe_send_document(
        chat_id=chat_id,
        document_content=html_doc,
        filename=filename,
        caption=f"📄 <b>{escape_html(filename)}</b>",
        reply_to_message_id=reply_to_id,
    )


async def handle_cmd_steer(bot: Any, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
    """Steer/guide the currently executing agy prompt with new instruction."""
    if not arg:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Usage: <code>/steer &lt;instruction&gt;</code>\nInjects instruction into ongoing agy execution.",
            reply_to_message_id=reply_to_id,
        )
        return

    bridge = bot.active_bridges.get(chat_id)
    if bridge and bridge.is_running():
        steered = await bridge.steer(arg)
        if steered:
            await bot._safe_send_message(
                chat_id=chat_id,
                text=f"🎯 <b>Steering instruction sent:</b>\n<i>{escape_html(arg)}</i>\n\nThe agent will incorporate this guidance into the ongoing turn.",
                reply_to_message_id=reply_to_id,
            )
        else:
            await bot._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Could not steer: the current task is already concluding.",
                reply_to_message_id=reply_to_id,
            )
    else:
        await bot._safe_send_message(
            chat_id=chat_id,
            text="ℹ️ No task is currently running to steer. Send a regular prompt to start.",
            reply_to_message_id=reply_to_id,
        )


async def handle_cmd_stop(bot: Any, chat_id: int) -> None:
    """Interrupt active agy prompt execution immediately."""
    bridge = bot.active_bridges.get(chat_id)
    if bridge:
        await bridge.stop()
        bot.active_bridges.pop(chat_id, None)
        bot.session_manager.set_busy(chat_id, False)
        await bot._safe_send_message(chat_id=chat_id, text="🛑 <b>Task interrupted by user.</b>")
    else:
        await bot._safe_send_message(chat_id=chat_id, text="ℹ️ No active task is currently running.")


async def handle_callback_query(bot: Any, callback_query: Dict[str, Any]) -> None:
    """Handle Telegram inline keyboard button presses."""
    cq_id = callback_query.get("id")
    if not cq_id:
        return

    message = callback_query.get("message", {})
    from_user = callback_query.get("from", {})
    chat_id = message.get("chat", {}).get("id") or from_user.get("id")
    message_id = message.get("message_id")
    data = callback_query.get("data", "")

    if not chat_id:
        await bot._safe_answer_callback_query(cq_id)
        return

    # Check authorization whitelist
    if not bot.config.is_chat_allowed(chat_id):
        logger.warning("Unauthorized callback query from chat_id %d", chat_id)
        await bot._safe_answer_callback_query(cq_id, text="⛔ Access Denied", show_alert=True)
        return

    session = bot.session_manager.get_session(chat_id)

    if data.startswith("open:"):
        proj_name = data[5:].strip()
        if bot.session_manager.is_busy(chat_id):
            await bot._safe_answer_callback_query(
                cq_id,
                text="⚠️ A task is currently running. Please /stop it first before switching projects.",
                show_alert=True,
            )
            return

        if proj_name in session.projects:
            bot.session_manager.open_project(chat_id, proj_name)
            await bot._safe_answer_callback_query(cq_id, text=f"Opened project: {proj_name}")
            await handle_cmd_projects(bot, chat_id, edit_message_id=message_id)
        else:
            await bot._safe_answer_callback_query(cq_id, text=f"Project '{proj_name}' not found", show_alert=True)

    elif data == "projects:refresh":
        await bot._safe_answer_callback_query(cq_id, text="Projects refreshed")
        await handle_cmd_projects(bot, chat_id, edit_message_id=message_id)

    elif data == "effort:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_effort(bot, chat_id, edit_message_id=message_id)

    elif data.startswith("effort:"):
        effort_val = data[7:].strip()
        await handle_cmd_effort(bot, chat_id, arg=effort_val, edit_message_id=message_id)
        await bot._safe_answer_callback_query(cq_id, text=f"Reasoning effort set to {effort_val}")

    elif data == "model:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_model(bot, chat_id, edit_message_id=message_id)

    elif data.startswith("model:"):
        model_val = data[6:].strip()
        await handle_cmd_model(bot, chat_id, arg=model_val, edit_message_id=message_id)
        await bot._safe_answer_callback_query(cq_id, text=f"Model set to {model_val}")

    elif data == "status":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_status(bot, chat_id)

    else:
        await bot._safe_answer_callback_query(cq_id)


if __name__ == "__main__":
    print("Commands module initialized.")
