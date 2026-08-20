import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.dir_tree import generate_dir_tree_html, scan_directory_tree
from gateway.file_renderers import render_file_to_html
from gateway.message_formatter import (
    escape_html,
    format_duration,
    format_tokens,
    generate_standalone_html,
)
from gateway.ui_keyboards import (
    build_artifacts_keyboard,
    build_effort_keyboard,
    build_help_keyboard,
    build_model_keyboard,
    build_projects_keyboard,
    build_skills_keyboard,
    build_usage_keyboard,
)

logger = logging.getLogger(__name__)


async def handle_cmd_help(bot: Any, chat_id: int, edit_message_id: Optional[int] = None) -> None:
    """Send interactive help manual & master command dashboard."""
    session = bot.session_manager.get_session(chat_id)
    active_proj = session.active_project or "(none)"
    conv_id = session.conversation_id
    conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"
    help_text = (
        "🤖 <b>agy-telegram Command Center</b>\n\n"
        "Direct interface to <b>agy</b> (Antigravity CLI) with real-time streaming, project isolation, multi-agent tools, and HTML exports.\n\n"
        "<b>Core Commands:</b>\n"
        "• <code>/usage</code> / <code>/quota</code> — View real-time Google model limits & credits\n"
        "• <code>/status</code> — Show active project, model, tokens & metrics\n"
        "• <code>/projects</code> — List & switch workspaces (<code>/open &lt;name&gt;</code>, <code>/new</code>)\n"
        "• <code>/model</code> — Switch model (<code>/model gemini-3.7-flash</code> | <code>pro</code>)\n"
        "• <code>/effort</code> — Set reasoning effort (<code>low</code> | <code>medium</code> | <code>high</code>)\n"
        "• <code>/skills</code> — Explore active agent skills & plugins\n"
        "• <code>/artifacts</code> — View & download conversation artifacts/scripts\n"
        "• <code>/dir</code> — Browse workspace file tree & interactive HTML navigator\n"
        "• <code>/file &lt;path&gt;</code> — Send workspace file document to Telegram\n"
        "• <code>/export</code> — Export conversation report as standalone HTML\n"
        "• <code>/steer &lt;text&gt;</code> — Steer/guide currently running agy prompt\n"
        "• <code>/stop</code> — Interrupt currently executing agy prompt\n"
        "• <code>/reset</code> — Clear conversation history for active project\n\n"
        f"<b>Active Project:</b> <code>{escape_html(active_proj)}</code> ({conv_text})\n\n"
        "💡 <i>Tap any quick action button below or type slash commands directly.</i>"
    )
    reply_markup = build_help_keyboard()
    if edit_message_id:
        await bot._safe_edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=help_text,
            reply_markup=reply_markup,
        )
    else:
        await bot._safe_send_message(chat_id=chat_id, text=help_text, reply_markup=reply_markup)


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

    tok_str = format_tokens(cum_tokens, cum_thinking)
    dur_str = format_duration(cum_duration)
    session_age = max(0, int(time.time() - session_start))
    sess_age_str = format_duration(session_age)

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
        f"• <b>Total Turns:</b> <code>{total_turns} turn(s)</code>\n"
        f"• <b>Total Tokens:</b> <code>{tok_str}</code>\n"
        f"• <b>Cumulative Runtime:</b> <code>{dur_str}</code>\n\n"
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


async def _run_agy_readonly_cmd(agy_bin: str, slash_cmd: str) -> Optional[Dict[str, Any]]:
    """Execute a read-only slash command via agy print mode and return parsed JSON data."""
    try:
        proc = await asyncio.create_subprocess_exec(
            agy_bin,
            "-p",
            slash_cmd,
            "--output-format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode == 0 and stdout:
            return json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("Error running read-only agy command %s: %s", slash_cmd, e)
    return None


def _format_progress_bar(fraction: float, length: int = 10) -> str:
    """Format a text progress bar e.g. [███████░░░]."""
    safe_f = max(0.0, min(1.0, float(fraction)))
    filled = int(round(safe_f * length))
    return f"[{'█' * filled}{'░' * (length - filled)}]"


async def handle_cmd_usage(
    bot: Any,
    chat_id: int,
    edit_message_id: Optional[int] = None,
    reply_to_id: Optional[int] = None,
) -> None:
    """Display real-time model quota and AI credits from Google Antigravity."""
    agy_bin = bot.config.get_resolved_agy_bin()
    usage_data = await _run_agy_readonly_cmd(agy_bin, "/usage")
    credits_data = await _run_agy_readonly_cmd(agy_bin, "/credits")

    lines = ["🪙 <b>Google Antigravity Quota Monitor</b>\n"]

    if usage_data and "command" in usage_data and "data" in usage_data["command"]:
        data_block = usage_data["command"]["data"]
        groups = data_block.get("groups", [])

        for group in groups:
            g_name = group.get("name", "Models")
            lines.append(f"<b>{escape_html(g_name)}:</b>")
            buckets = group.get("buckets", [])
            for b in buckets:
                b_name = b.get("name", "Limit")
                rem_frac = b.get("remaining_fraction", 1.0)
                pct_str = f"{rem_frac * 100:.1f}%"
                bar = _format_progress_bar(rem_frac)
                reset_t = b.get("reset_time", "")
                reset_hint = f" <i>(Resets: {escape_html(reset_t[:16].replace('T', ' '))})</i>" if reset_t else ""
                lines.append(f"• {escape_html(b_name)}: <code>{bar} {pct_str}</code>{reset_hint}")
            lines.append("")

    if credits_data and "command" in credits_data and "data" in credits_data["command"]:
        c_block = credits_data["command"]["data"]
        rem_credits = c_block.get("remaining_credits", 0)
        lines.append(f"💳 <b>G1 AI Credits Balance:</b> <code>{rem_credits} credits</code>\n")

    if not usage_data and not credits_data:
        lines.append("⚠️ <i>Could not fetch real-time quota from agy. Check CLI authentication.</i>\n")

    session = bot.session_manager.get_session(chat_id)
    proj_name = session.active_project or "default"
    proj = session.projects.get(proj_name, {})
    stats = proj.get("stats", {})
    if stats:
        tot_tok = stats.get("total_tokens", 0)
        tot_th = stats.get("thinking_tokens", 0)
        tot_dur = stats.get("total_duration_seconds", 0.0)
        tot_turns = stats.get("total_turns", 0)
        tok_formatted = format_tokens(tot_tok, tot_th)
        dur_formatted = format_duration(tot_dur)
        lines.append(
            f"📊 <b>Active Session Usage:</b>\n"
            f"• <b>Tokens:</b> <code>{tok_formatted}</code>\n"
            f"• <b>Runtime:</b> <code>{dur_formatted}</code> across <code>{tot_turns} turn(s)</code>"
        )

    lines.append("\n💡 <i>Limits refresh automatically based on your subscription tier.</i>")
    text = "\n".join(lines)
    reply_markup = build_usage_keyboard()

    if edit_message_id:
        await bot._safe_edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        await bot._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, reply_to_message_id=reply_to_id)


async def handle_cmd_skills(
    bot: Any,
    chat_id: int,
    edit_message_id: Optional[int] = None,
    reply_to_id: Optional[int] = None,
) -> None:
    """Explore and list available skills and plugins."""
    agy_bin = bot.config.get_resolved_agy_bin()
    skills_data = await _run_agy_readonly_cmd(agy_bin, "/skills")

    lines = ["🧩 <b>Antigravity Skills & Plugin Tools</b>\n"]

    skill_list = []
    if skills_data and "command" in skills_data and "data" in skills_data["command"]:
        skill_list = skills_data["command"]["data"].get("skills", [])

    if skill_list:
        builtin_skills = []
        plugin_skills: Dict[str, List[Dict[str, Any]]] = {}

        for s in skill_list:
            s_plugin = s.get("plugin")
            if s_plugin:
                plugin_skills.setdefault(s_plugin, []).append(s)
            else:
                builtin_skills.append(s)

        if builtin_skills:
            lines.append("<b>Built-in Core Skills:</b>")
            for bs in builtin_skills[:4]:
                s_name = bs.get("name", "")
                s_desc = bs.get("description", "")
                lines.append(f"• <b>{escape_html(s_name)}</b>: {escape_html(s_desc[:70])}{'...' if len(s_desc)>70 else ''}")
            lines.append("")

        if plugin_skills:
            lines.append("<b>Installed Plugin Packages:</b>")
            for p_name, p_skills in plugin_skills.items():
                s_names = ", ".join([sk.get("name", "").split(":")[-1] for sk in p_skills])
                lines.append(f"• 🔌 <b>{escape_html(p_name)}</b> ({len(p_skills)} skills): <code>{escape_html(s_names[:60])}{'...' if len(s_names)>60 else ''}</code>")
            lines.append("")

        lines.append(f"✨ <i>Total {len(skill_list)} skills registered and model-invocable.</i>")
    else:
        lines.append("ℹ️ <i>No custom skills discovered. Standard tools (bash, file, search) are active.</i>")

    text = "\n".join(lines)
    reply_markup = build_skills_keyboard()

    if edit_message_id:
        await bot._safe_edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        await bot._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, reply_to_message_id=reply_to_id)


def _collect_conversation_artifacts(conversation_id: Optional[str], workspace_path: Optional[str]) -> List[Dict[str, Any]]:
    """Scan brain directory and workspace runtime/artifacts for artifacts."""
    artifacts: List[Dict[str, Any]] = []
    seen_paths = set()

    # 1. Brain directory
    if conversation_id:
        brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain" / conversation_id
        if brain_dir.is_dir():
            for p in sorted(brain_dir.rglob("*")):
                if p.is_file() and not p.name.startswith(".") and ".system_generated" not in p.parts:
                    if p not in seen_paths:
                        seen_paths.add(p)
                        artifacts.append({
                            "name": p.name,
                            "path": p,
                            "size": p.stat().st_size,
                            "mtime": p.stat().st_mtime,
                            "source": "brain",
                        })

    # 2. Workspace runtime/artifacts
    if workspace_path:
        w_art = Path(workspace_path) / "runtime" / "artifacts"
        if w_art.is_dir():
            for p in sorted(w_art.glob("*")):
                if p.is_file() and not p.name.startswith("."):
                    if p not in seen_paths:
                        seen_paths.add(p)
                        artifacts.append({
                            "name": p.name,
                            "path": p,
                            "size": p.stat().st_size,
                            "mtime": p.stat().st_mtime,
                            "source": "workspace",
                        })

    # Sort by mtime descending
    artifacts.sort(key=lambda x: x["mtime"], reverse=True)
    return artifacts


async def handle_cmd_artifacts(
    bot: Any,
    chat_id: int,
    arg: str = "",
    edit_message_id: Optional[int] = None,
    reply_to_id: Optional[int] = None,
) -> None:
    """List or download conversation artifacts and reports."""
    session = bot.session_manager.get_session(chat_id)
    conv_id = session.conversation_id
    w_path = session.active_path() or bot.config.agy_working_dir

    artifacts = _collect_conversation_artifacts(conv_id, w_path)

    # If argument provided, attempt direct download
    if arg.strip():
        arg_clean = arg.strip()
        target_art: Optional[Dict[str, Any]] = None
        if arg_clean.isdigit():
            idx = int(arg_clean) - 1
            if 0 <= idx < len(artifacts):
                target_art = artifacts[idx]
        else:
            for art in artifacts:
                if art["name"].lower() == arg_clean.lower() or arg_clean.lower() in art["name"].lower():
                    target_art = art
                    break

        if target_art:
            p = target_art["path"]
            sz_kb = target_art["size"] / 1024
            sz_str = f"{sz_kb:.1f} KB" if sz_kb >= 1 else f"{target_art['size']} B"
            await bot._safe_send_chat_action(chat_id, "upload_document")
            await bot._safe_send_document(
                chat_id=chat_id,
                document_content=p,
                filename=p.name,
                caption=f"📦 <b>{escape_html(p.name)}</b> ({sz_str})\nSource: <code>{escape_html(target_art['source'])}</code>",
                reply_to_message_id=reply_to_id,
            )
            return
        else:
            await bot._safe_send_message(
                chat_id=chat_id,
                text=f"❌ Artifact <code>{escape_html(arg_clean)}</code> not found.",
                reply_to_message_id=reply_to_id,
            )
            return

    # List view
    lines = ["📦 <b>Conversation Artifacts & Reports:</b>\n"]
    if not artifacts:
        conv_str = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(None)</i>"
        lines.append(
            f"ℹ️ No artifacts found for active session ({conv_str}).\n\n"
            "Artifacts (HTML reports, code diffs, diagrams) generated during conversations will automatically appear here."
        )
    else:
        for idx, art in enumerate(artifacts[:10], 1):
            sz_kb = art["size"] / 1024
            sz_str = f"{sz_kb:.1f} KB" if sz_kb >= 1 else f"{art['size']} B"
            t_str = time.strftime("%m-%d %H:%M", time.localtime(art["mtime"]))
            lines.append(
                f"<b>{idx}.</b> <code>{escape_html(art['name'])}</code> ({sz_str}, {t_str})\n"
                f"    Download: <code>/artifacts {idx}</code>"
            )
        lines.append(f"\n💡 <i>Total {len(artifacts)} artifact(s). Tap a button below to download directly.</i>")

    text = "\n".join(lines)
    reply_markup = build_artifacts_keyboard(artifacts)

    if edit_message_id:
        await bot._safe_edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        await bot._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, reply_to_message_id=reply_to_id)


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

    elif data in ("projects:refresh", "projects:show"):
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

    elif data in ("usage:show", "usage:refresh"):
        await bot._safe_answer_callback_query(cq_id, text="Refreshing quota...")
        await handle_cmd_usage(bot, chat_id, edit_message_id=message_id)

    elif data == "skills:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_skills(bot, chat_id, edit_message_id=message_id)

    elif data == "artifacts:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_artifacts(bot, chat_id, edit_message_id=message_id)

    elif data.startswith("artifact:dl:"):
        idx_str = data[12:].strip()
        await bot._safe_answer_callback_query(cq_id, text="Downloading artifact...")
        await handle_cmd_artifacts(bot, chat_id, arg=str(int(idx_str) + 1))

    elif data == "help:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_help(bot, chat_id, edit_message_id=message_id)

    elif data == "dir:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_dir(bot, chat_id)

    elif data == "export:show":
        await bot._safe_answer_callback_query(cq_id)
        await handle_cmd_export(bot, chat_id)

    elif data.startswith("dec:"):
        idx_str = data[4:].strip()
        opt_text = ""
        try:
            idx = int(idx_str)
            if 0 <= idx < len(session.pending_decisions):
                opt_text = session.pending_decisions[idx]
        except Exception:
            pass

        if opt_text:
            await bot._safe_answer_callback_query(cq_id, text=f"Selected: {opt_text[:30]}")
            session.pending_decisions = []
            await bot.handle_prompt(chat_id=chat_id, prompt=opt_text, reply_to_id=None)
        else:
            await bot._safe_answer_callback_query(cq_id, text="Option expired or unavailable", show_alert=True)

    elif data.startswith("act:"):
        idx_str = data[4:].strip()
        action_item = None
        try:
            idx = int(idx_str)
            if 0 <= idx < len(session.pending_actions):
                action_item = session.pending_actions[idx]
        except Exception:
            pass

        if action_item:
            act_prompt = action_item.get("action", "")
            act_label = action_item.get("label", "Action")
            await bot._safe_answer_callback_query(cq_id, text=f"Running: {act_label[:30]}")
            if act_prompt.startswith("/"):
                parts = act_prompt.split(None, 1)
                cmd = parts[0]
                arg = parts[1] if len(parts) > 1 else ""
                await bot.dispatch_slash_command(chat_id=chat_id, cmd=cmd, arg=arg, message_id=None)
            else:
                await bot.handle_prompt(chat_id=chat_id, prompt=act_prompt, reply_to_id=None)
        else:
            await bot._safe_answer_callback_query(cq_id, text="Action expired or unavailable", show_alert=True)

    else:
        await bot._safe_answer_callback_query(cq_id)


if __name__ == "__main__":
    print("Commands module initialized.")
