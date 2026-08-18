"""Telegram Bot orchestrator: handles long polling, slash commands, streaming bridge, and HTML file exports."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.agy_bridge import AgyBridge, AgyEvent
from gateway.config import Config
from gateway.dir_tree import generate_dir_tree_html, scan_directory_tree
from gateway.file_renderers import render_file_to_html
from gateway.message_formatter import (
    AgyFileBlock,
    escape_html,
    extract_agy_file_blocks,
    format_final_result,
    format_streaming_message,
    format_tool_summary,
    generate_standalone_html,
    render_agy_file,
    should_export_as_file,
    split_text_chunks,
)
from gateway.session_manager import ChatSession, SessionManager
from gateway.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


DEFAULT_BOT_COMMANDS: List[Dict[str, str]] = [
    {"command": "help", "description": "📖 Show manual & slash command guide"},
    {"command": "status", "description": "📊 Show active project, model & status"},
    {"command": "projects", "description": "📁 List all projects & workspaces"},
    {"command": "open", "description": "📂 Open / switch project (/open <name>)"},
    {"command": "new", "description": "✨ Create new project (/new <name> <path>)"},
    {"command": "model", "description": "🧠 Set or reset model (/model <name>|reset)"},
    {"command": "effort", "description": "⚡ Set reasoning effort (low|medium|high)"},
    {"command": "export", "description": "📄 Export conversation as HTML report"},
    {"command": "file", "description": "📎 Send file or render HTML (/file [-h] <path>)"},
    {"command": "dir", "description": "📁 Browse workspace files & interactive tree"},
    {"command": "steer", "description": "🎯 Steer active task (/steer <text>)"},
    {"command": "stop", "description": "🛑 Interrupt running agy prompt"},
    {"command": "reset", "description": "🔄 Clear active project conversation"},
    {"command": "delete", "description": "🗑️ Delete a project (/delete <name>)"},
]


class AgyTelegramBot:
    """Core bot runner managing Telegram communication, agy executions, and document exports."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = TelegramClient(bot_token=config.bot_token)
        self.session_manager = SessionManager(persistence_file=config.sessions_file)
        self.active_bridges: Dict[int, AgyBridge] = {}
        self.running = False
        self.bot_info: Dict[str, Any] = {}
        self.started_at = time.time()

    async def setup_bot_commands(self) -> bool:
        """Register slash commands with Telegram Bot API so users see the command menu in chat."""
        try:
            success = await self.client.set_my_commands(DEFAULT_BOT_COMMANDS)
            if success:
                logger.info("Successfully registered %d slash commands with Telegram menu.", len(DEFAULT_BOT_COMMANDS))
            else:
                logger.warning("Telegram setMyCommands returned False.")
            # Ensure menu button is configured to show commands
            await self.client.set_chat_menu_button(menu_button={"type": "commands"})
            return success
        except Exception as e:
            logger.warning("Error registering bot commands: %s", e)
            return False

    async def _safe_send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Send message safely, catching all Telegram / network errors."""
        try:
            return await self.client.send_message(
                chat_id=chat_id,
                text=text,
                **kwargs,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram send_message failed for chat %s: %s", chat_id, e)
            return None

    async def _safe_edit_message_text(
        self,
        chat_id: int,
        message_id: Optional[int],
        text: str,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Edit message text safely, catching all Telegram / network errors."""
        if not message_id:
            return None
        try:
            return await self.client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                **kwargs,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram edit_message_text failed for chat %s, msg %s: %s", chat_id, message_id, e)
            return None

    async def _safe_send_document(
        self,
        chat_id: int,
        document_content: bytes | str | Path,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Send document safely, catching all Telegram / network errors."""
        try:
            return await self.client.send_document(
                chat_id=chat_id,
                document_content=document_content,
                **kwargs,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram send_document failed for chat %s: %s", chat_id, e)
            return None

    async def _safe_send_chat_action(
        self,
        chat_id: int,
        action: str = "typing",
        **kwargs: Any,
    ) -> bool:
        """Send chat action safely, catching all Telegram / network errors."""
        try:
            return await self.client.send_chat_action(chat_id=chat_id, action=action, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram send_chat_action failed for chat %s: %s", chat_id, e)
            return False

    async def _safe_answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Answer callback query safely, catching all Telegram / network errors."""
        try:
            return await self.client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                show_alert=show_alert,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram answer_callback_query failed for query %s: %s", callback_query_id, e)
            return False

    async def _notify_restart(self) -> None:
        """Send a short 'gateway (re)started' notice to every known chat with a session."""
        started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at))
        sessions = getattr(self.session_manager, "_sessions", {})
        for chat_id, session in sessions.items():
            if not self.config.is_chat_allowed(chat_id):
                continue
            proj = session.active_project or "<i>(none)</i>"
            n = len(getattr(session, "projects", {}))
            try:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text=(
                        "🔄 <b>Gateway restarted.</b>\n"
                        f"• Started: <code>{started_str}</code>\n"
                        f"• Active project: <code>{escape_html(str(proj))}</code>\n"
                        f"• {n} project(s) saved — use <code>/status</code> or <code>/open</code> to resume."
                    ),
                )
            except Exception as e:
                logger.warning("Restart notification failed for chat %s: %s", chat_id, e)

    async def start(self) -> None:
        """Initialize and run the Telegram long-polling loop."""
        if not self.config.is_token_valid():
            logger.error("Invalid or placeholder BOT_TOKEN provided.")
            print("\n❌ Error: BOT_TOKEN is missing or is a placeholder in .env!")
            print("Please create a bot with @BotFather on Telegram, copy the token to .env, and restart.\n")
            return

        try:
            self.bot_info = await self.client.get_me()
            username = self.bot_info.get("username", "UnknownBot")
            print(f"🤖 Connected to Telegram as @{username} (ID: {self.bot_info.get('id')})")
            logger.info("Bot authenticated as @%s (ID: %s)", username, self.bot_info.get("id"))
            # Auto-register slash commands for Telegram chat menu
            await self.setup_bot_commands()
        except Exception as e:
            logger.error("Failed to connect to Telegram: %s", e)
            print(f"\n❌ Error connecting to Telegram API: {e}")
            return

        self.running = True
        print("🚀 agy-telegram gateway is running! Press Ctrl+C to stop.\n")

        # Notify known chats that the gateway (re)started, so the user can see it came back up.
        await self._notify_restart()

        offset: Optional[int] = None
        consecutive_errors = 0

        while self.running:
            try:
                updates = await self.client.get_updates(offset=offset, timeout=30)
                consecutive_errors = 0
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = update_id + 1
                    task = asyncio.create_task(self.process_update(update))

                    def _on_task_done(t: asyncio.Task) -> None:
                        if not t.cancelled() and t.exception():
                            logger.error("Unhandled exception in update task: %s", t.exception())

                    task.add_done_callback(_on_task_done)
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(10, 2 ** consecutive_errors)
                logger.warning("Polling error (%s). Retrying in %ds...", e, backoff)
                await asyncio.sleep(backoff)

        await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully shut down active bridge processes and HTTP client."""
        logger.info("Shutting down bot gateway...")
        self.running = False
        for chat_id, bridge in list(self.active_bridges.items()):
            try:
                await bridge.stop()
            except Exception as e:
                logger.warning("Error stopping bridge for chat %s: %s", chat_id, e)
        self.active_bridges.clear()
        await self.client.close()
        logger.info("Shutdown complete.")

    async def process_update(self, update: Dict[str, Any]) -> None:
        """Process a single Telegram update safely."""
        try:
            # Handle inline keyboard callback queries
            callback_query = update.get("callback_query")
            if callback_query:
                await self.handle_callback_query(callback_query)
                return

            message = update.get("message")
            if not message:
                return

            chat = message.get("chat", {})
            chat_id = chat.get("id")
            text = message.get("text", "").strip()
            message_id = message.get("message_id")

            if not chat_id or not text:
                return

            # Check authorization whitelist
            if not self.config.is_chat_allowed(chat_id):
                logger.warning("Unauthorized access attempt from chat_id %d", chat_id)
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="⛔ <b>Access Denied</b>\nThis bot is private and your chat ID is not authorized.",
                )
                return

            # Slash command routing
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                cmd = parts[0].lower().split("@")[0]  # Strip bot @username if in group
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd in ("/start", "/help"):
                    await self.handle_cmd_help(chat_id)
                elif cmd == "/new":
                    await self.handle_cmd_new(chat_id, arg)
                elif cmd == "/open":
                    await self.handle_cmd_open(chat_id, arg)
                elif cmd == "/delete":
                    await self.handle_cmd_delete(chat_id, arg)
                elif cmd == "/reset":
                    await self.handle_cmd_reset(chat_id)
                elif cmd == "/projects":
                    await self.handle_cmd_projects(chat_id)
                elif cmd == "/status":
                    await self.handle_cmd_status(chat_id)
                elif cmd == "/model":
                    await self.handle_cmd_model(chat_id, arg)
                elif cmd == "/effort":
                    await self.handle_cmd_effort(chat_id, arg)
                elif cmd in ("/dir", "/ls"):
                    await self.handle_cmd_dir(chat_id, arg, message_id)
                elif cmd in ("/file", "/sendfile"):
                    await self.handle_cmd_file(chat_id, arg, message_id)
                elif cmd in ("/export", "/html"):
                    await self.handle_cmd_export(chat_id, message_id)
                elif cmd == "/steer":
                    await self.handle_cmd_steer(chat_id, arg, message_id)
                elif cmd == "/stop":
                    await self.handle_cmd_stop(chat_id)
                else:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=f"❓ Unknown command <code>{escape_html(cmd)}</code>. Use /help for instructions.",
                    )
                return

            # If a prompt is running and user sends a new message -> steer into active session!
            session = self.session_manager.get_session(chat_id)
            if session.is_busy and chat_id in self.active_bridges:
                steered = await self.active_bridges[chat_id].steer(text)
                if steered:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=f"🎯 <b>Steered into active session:</b>\n<i>{escape_html(text)}</i>",
                        reply_to_message_id=message_id,
                    )
                    return

            # Regular prompt -> agy bridge execution
            await self.handle_prompt(chat_id, text, message_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Unhandled error processing update %s: %s", update, e, exc_info=True)

    async def handle_cmd_help(self, chat_id: int) -> None:
        """Send help message."""
        session = self.session_manager.get_session(chat_id)
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
            "• <code>/help</code> — Show this manual\n\n"
            f"<b>Active Project:</b> <code>{escape_html(active_proj)}</code> ({conv_text})\n\n"
            "💡 <i>Tip: Long responses, code diffs, and test reports are automatically generated and sent as formatted HTML file attachments!</i>"
        )
        await self._safe_send_message(chat_id=chat_id, text=help_text)

    async def handle_cmd_new(self, chat_id: int, arg: str) -> None:
        """Create a new project workspace (/new <name> <path>) or open if exists."""
        if self.session_manager.is_busy(chat_id):
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before switching projects.",
            )
            return

        parts = arg.split(maxsplit=1) if arg else []
        session = self.session_manager.get_session(chat_id)

        if len(parts) == 0:
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/new &lt;name&gt; &lt;path&gt;</code>\nCreate and switch to a new project workspace.",
            )
            return

        name = parts[0].strip()

        # If project already exists, treat as /open
        if name in session.projects:
            self.session_manager.open_project(chat_id, name)
            path_val = session.active_path() or ""
            conv_id = session.conversation_id
            conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"
            await self._safe_send_message(
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
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"⚠️ Usage: <code>/new {escape_html(name)} &lt;path&gt;</code>\nPlease provide the directory path for the new project.",
            )
            return

        path_str = parts[1].strip()
        target_path = str(Path(path_str).expanduser().resolve())
        self.session_manager.create_project(chat_id, name, target_path)

        await self._safe_send_message(
            chat_id=chat_id,
            text=(
                f"✨ <b>Project created and activated!</b>\n"
                f"• <b>Project:</b> <code>{escape_html(name)}</code>\n"
                f"• <b>Path:</b> <code>{escape_html(target_path)}</code>\n"
                f"• <b>Conversation:</b> <i>(New session)</i>"
            ),
        )

    async def handle_cmd_open(self, chat_id: int, arg: str) -> None:
        """Open and switch to an existing project (/open <name>)."""
        if self.session_manager.is_busy(chat_id):
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before switching projects.",
            )
            return

        if not arg:
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/open &lt;name&gt;</code>\nUse <code>/projects</code> to see available projects.",
            )
            return

        name = arg.strip()
        session = self.session_manager.get_session(chat_id)

        if name not in session.projects:
            projects = self.session_manager.list_projects(chat_id)
            if projects:
                plist = "\n".join([f"• <code>{escape_html(p)}</code>" for p in projects.keys()])
                msg = f"❌ Project <code>{escape_html(name)}</code> not found.\n\n<b>Available projects:</b>\n{plist}\n\nUse <code>/projects</code> to view details."
            else:
                msg = f"❌ Project <code>{escape_html(name)}</code> not found. No projects exist yet.\nUse <code>/new &lt;name&gt; &lt;path&gt;</code> to create one."
            await self._safe_send_message(chat_id=chat_id, text=msg)
            return

        self.session_manager.open_project(chat_id, name)
        path_val = session.active_path() or ""
        conv_id = session.conversation_id
        conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"

        await self._safe_send_message(
            chat_id=chat_id,
            text=(
                f"📂 <b>Opened project:</b> <code>{escape_html(name)}</code>\n"
                f"• <b>Path:</b> <code>{escape_html(path_val)}</code>\n"
                f"• <b>Conversation:</b> {conv_text}"
            ),
        )

    async def handle_callback_query(self, callback_query: Dict[str, Any]) -> None:
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
            await self._safe_answer_callback_query(cq_id)
            return

        # Check authorization whitelist
        if not self.config.is_chat_allowed(chat_id):
            logger.warning("Unauthorized callback query from chat_id %d", chat_id)
            await self._safe_answer_callback_query(cq_id, text="⛔ Access Denied", show_alert=True)
            return

        session = self.session_manager.get_session(chat_id)

        if data.startswith("open:"):
            proj_name = data[5:].strip()
            if self.session_manager.is_busy(chat_id):
                await self._safe_answer_callback_query(
                    cq_id,
                    text="⚠️ A task is currently running. Please /stop it first before switching projects.",
                    show_alert=True,
                )
                return

            if proj_name in session.projects:
                self.session_manager.open_project(chat_id, proj_name)
                await self._safe_answer_callback_query(cq_id, text=f"Opened project: {proj_name}")
                await self.handle_cmd_projects(chat_id, edit_message_id=message_id)
            else:
                await self._safe_answer_callback_query(cq_id, text=f"Project '{proj_name}' not found", show_alert=True)

        elif data == "projects:refresh":
            await self._safe_answer_callback_query(cq_id, text="Projects refreshed")
            await self.handle_cmd_projects(chat_id, edit_message_id=message_id)

        elif data == "effort:show":
            await self._safe_answer_callback_query(cq_id)
            await self.handle_cmd_effort(chat_id, edit_message_id=message_id)

        elif data.startswith("effort:"):
            effort_val = data[7:].strip()
            await self.handle_cmd_effort(chat_id, arg=effort_val, edit_message_id=message_id)
            await self._safe_answer_callback_query(cq_id, text=f"Reasoning effort set to {effort_val}")

        elif data == "model:show":
            await self._safe_answer_callback_query(cq_id)
            await self.handle_cmd_model(chat_id, edit_message_id=message_id)

        elif data.startswith("model:"):
            model_val = data[6:].strip()
            await self.handle_cmd_model(chat_id, arg=model_val, edit_message_id=message_id)
            await self._safe_answer_callback_query(cq_id, text=f"Model set to {model_val}")

        elif data == "status":
            await self._safe_answer_callback_query(cq_id)
            await self.handle_cmd_status(chat_id)

        else:
            await self._safe_answer_callback_query(cq_id)

    async def handle_cmd_projects(self, chat_id: int, edit_message_id: Optional[int] = None) -> None:
        """List all projects in current session with interactive inline option buttons."""
        session = self.session_manager.get_session(chat_id)
        projects = session.projects

        if not projects:
            text = (
                "📂 <b>No projects configured yet.</b>\n\n"
                "Use <code>/new &lt;name&gt; &lt;path&gt;</code> to create your first project workspace!"
            )
            if edit_message_id:
                await self._safe_edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text)
            else:
                await self._safe_send_message(chat_id=chat_id, text=text)
            return

        lines = ["📁 <b>Available Projects:</b>\n"]
        keyboard: List[List[Dict[str, str]]] = []
        row: List[Dict[str, str]] = []

        for name, proj in projects.items():
            is_active = (name == session.active_project)
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
        reply_markup = {"inline_keyboard": keyboard}
        full_text = "\n".join(lines)

        if edit_message_id:
            await self._safe_edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=full_text,
                reply_markup=reply_markup,
            )
        else:
            await self._safe_send_message(
                chat_id=chat_id,
                text=full_text,
                reply_markup=reply_markup,
            )

    async def handle_cmd_delete(self, chat_id: int, arg: str) -> None:
        """Delete a project workspace (/delete <name>)."""
        if self.session_manager.is_busy(chat_id):
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before deleting projects.",
            )
            return

        if not arg:
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/delete &lt;name&gt;</code>\nDelete a project workspace from session manager.",
            )
            return

        name = arg.strip()
        session = self.session_manager.get_session(chat_id)

        if name not in session.projects:
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"❌ Project <code>{escape_html(name)}</code> does not exist.",
            )
            return

        self.session_manager.delete_project(chat_id, name)
        await self._safe_send_message(
            chat_id=chat_id,
            text=f"🗑️ Project <b>{escape_html(name)}</b> deleted successfully.",
        )

    async def handle_cmd_reset(self, chat_id: int) -> None:
        """Reset conversation_id for active project."""
        if self.session_manager.is_busy(chat_id):
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before resetting.",
            )
            return

        session = self.session_manager.get_session(chat_id)
        active = session.active_project
        self.session_manager.reset_session(chat_id)

        proj_info = f" for project <b>{escape_html(active)}</b>" if active else ""
        await self._safe_send_message(
            chat_id=chat_id,
            text=f"🔄 <b>Session reset{proj_info}.</b> Next message will start a fresh conversation.",
        )

    async def handle_cmd_status(self, chat_id: int) -> None:
        """Display current bot & session status."""
        session = self.session_manager.get_session(chat_id)
        conv_id = session.conversation_id or "<i>(None - new conversation)</i>"
        if session.conversation_id:
            conv_id = f"<code>{escape_html(session.conversation_id)}</code>"

        active_proj = session.active_project or "<i>(default / none)</i>"
        active_path = session.active_path() or self.config.agy_working_dir or os.getcwd()
        model_str = session.model or self.config.default_model or "gemini-3.7-flash"
        effort_str = session.effort or self.config.default_effort or "high"
        busy_status = "🔴 Busy (Running prompt)" if session.is_busy else "🟢 Idle"

        # Startup time + uptime so the user can tell this is a fresh process
        started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at))
        uptime = max(0, int(time.time() - self.started_at))
        if uptime < 60:
            uptime_str = f"{uptime}s"
        elif uptime < 3600:
            uptime_str = f"{uptime // 60}m {uptime % 60}s"
        else:
            uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m"

        status_text = (
            "📊 <b>Session & System Status:</b>\n\n"
            f"• <b>Status:</b> {busy_status}\n"
            f"• <b>Started:</b> <code>{started_str}</code> (<code>{uptime_str}</code> ago)\n"
            f"• <b>Active Project:</b> <code>{escape_html(active_proj)}</code>\n"
            f"• <b>Working Directory:</b> <code>{escape_html(active_path)}</code>\n"
            f"• <b>Conversation ID:</b> {conv_id}\n"
            f"• <b>Model:</b> <code>{escape_html(model_str)}</code>\n"
            f"• <b>Effort:</b> <code>{escape_html(effort_str)}</code>\n"
            f"• <b>Configured Projects:</b> {len(session.projects)}\n"
        )
        await self._safe_send_message(chat_id=chat_id, text=status_text)

    async def handle_cmd_model(self, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
        """Change or view model with interactive option buttons showing active status."""
        session = self.session_manager.get_session(chat_id)
        if arg and arg.lower() != "show":
            val = arg.strip()
            if val.lower() in ("reset", "default"):
                self.session_manager.set_model(chat_id, None)
            else:
                self.session_manager.set_model(chat_id, val)

        cur_model = session.model or self.config.default_model or "gemini-3.7-flash"
        cur_effort = session.effort or self.config.default_effort or "high"

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
            f"• <b>Active Model:</b> <code>{escape_html(cur_model)}</code>",
            f"• <b>Active Effort:</b> <code>{escape_html(cur_effort)}</code>\n",
            "<b>Available Models:</b>",
        ]

        buttons: List[List[Dict[str, str]]] = []
        for m in models:
            is_active = (cur_model == m["id"])
            marker = "🌟 <b>[ACTIVE]</b> " if is_active else "• "
            lines.append(f"{marker}{m['icon']} <b>{m['name']}</b> (<code>{m['id']}</code>)\n   <i>{m['desc']}</i>")
            btn_label = f"✓ {m['name']}" if is_active else f"{m['icon']} {m['name']}"
            buttons.append([{"text": btn_label, "callback_data": f"model:{m['id']}"}])

        buttons.append([
            {"text": "⚡ Adjust Effort", "callback_data": "effort:show"},
            {"text": "📊 Status", "callback_data": "status"},
        ])

        lines.append("\n💡 <i>Tap a button below to switch model instantly.</i>")
        reply_markup = {"inline_keyboard": buttons}
        text = "\n".join(lines)

        if edit_message_id:
            await self._safe_edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=reply_markup)
        else:
            await self._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def handle_cmd_effort(self, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
        """Change reasoning effort with interactive option buttons showing active status."""
        session = self.session_manager.get_session(chat_id)
        if arg and arg.lower() != "show":
            val = arg.strip().lower()
            if val not in ("low", "medium", "high"):
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="⚠️ Invalid effort value. Allowed: <code>low</code>, <code>medium</code>, <code>high</code>.",
                )
                return
            self.session_manager.set_effort(chat_id, val)

        cur_model = session.model or self.config.default_model or "gemini-3.7-flash"
        cur_effort = session.effort or self.config.default_effort or "high"

        efforts = [
            {"id": "high", "name": "High", "icon": "🔴", "desc": "Maximum reasoning budget & deep multi-step planning (Recommended)"},
            {"id": "medium", "name": "Medium", "icon": "🟡", "desc": "Balanced reasoning depth and generation speed"},
            {"id": "low", "name": "Low", "icon": "🟢", "desc": "Minimal reasoning latency for quick routine tasks"},
        ]

        lines = [
            "⚙️ <b>Reasoning Effort Setting:</b>\n",
            f"• <b>Active Model:</b> <code>{escape_html(cur_model)}</code>",
            f"• <b>Active Effort:</b> <code>{escape_html(cur_effort)}</code>\n",
            "<b>Available Effort Levels:</b>",
        ]

        effort_buttons: List[Dict[str, str]] = []
        for eff in efforts:
            is_active = (cur_effort == eff["id"])
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
        reply_markup = {"inline_keyboard": keyboard}
        text = "\n".join(lines)

        if edit_message_id:
            await self._safe_edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=reply_markup)
        else:
            await self._safe_send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def handle_cmd_dir(self, chat_id: int, arg: str = "", reply_to_id: Optional[int] = None) -> None:
        """Browse workspace directory and export interactive collapsible HTML file tree viewer."""
        session = self.session_manager.get_session(chat_id)
        active_proj = session.active_project or "default"
        base_dir = session.active_path() or self.config.agy_working_dir or os.getcwd()

        if arg:
            target_dir = (Path(base_dir) / arg.strip()).expanduser().resolve()
        else:
            target_dir = Path(base_dir).expanduser().resolve()

        if not target_dir.is_dir():
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"⚠️ Directory does not exist: <code>{escape_html(str(target_dir))}</code>",
                reply_to_message_id=reply_to_id,
            )
            return

        await self._safe_send_chat_action(chat_id, "upload_document")
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

        doc_res = await self._safe_send_document(
            chat_id=chat_id,
            document_content=html_doc,
            filename=filename,
            caption=caption,
            reply_to_message_id=reply_to_id,
        )

        if not doc_res:
            await self._safe_send_message(
                chat_id=chat_id,
                text=caption,
                reply_to_message_id=reply_to_id,
            )

    async def handle_cmd_file(self, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
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
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/file [-h] &lt;relative_or_abs_path&gt;</code>\nSend a workspace file directly to Telegram (use <code>-h</code> to render as HTML).",
                reply_to_message_id=reply_to_id,
            )
            return

        session = self.session_manager.get_session(chat_id)
        active_proj = session.active_project or "default"
        base_dir = Path(session.active_path() or self.config.agy_working_dir or os.getcwd())
        raw_path = Path(file_arg).expanduser()

        target_file = (base_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()

        if not target_file.is_file():
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"❌ File not found: <code>{escape_html(str(target_file))}</code>",
                reply_to_message_id=reply_to_id,
            )
            return

        size_kb = target_file.stat().st_size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{target_file.stat().st_size} bytes"

        await self._safe_send_chat_action(chat_id, "upload_document")

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
                res = await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=html_doc,
                    filename=out_filename,
                    caption=caption,
                    reply_to_message_id=reply_to_id,
                )
            else:
                # Binary or unsupported -> return original file directly
                caption = f"📄 <b>{escape_html(target_file.name)}</b> ({size_str})\n<i>(HTML conversion not supported for binary file, sent original)</i>"
                res = await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=target_file,
                    filename=target_file.name,
                    caption=caption,
                    reply_to_message_id=reply_to_id,
                )
        else:
            # Raw file
            res = await self._safe_send_document(
                chat_id=chat_id,
                document_content=target_file,
                filename=target_file.name,
                caption=f"📄 <b>{escape_html(target_file.name)}</b> ({size_str})",
                reply_to_message_id=reply_to_id,
            )

        if not res:
            logger.error("Failed to send file %s to chat %d", target_file, chat_id)
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"❌ Failed to send document: <code>{escape_html(target_file.name)}</code>",
                reply_to_message_id=reply_to_id,
            )

    async def handle_cmd_export(self, chat_id: int, reply_to_id: Optional[int]) -> None:
        """Export current session status and conversation report as standalone HTML document."""
        session = self.session_manager.get_session(chat_id)
        active_proj = session.active_project or "default"
        active_path = session.active_path() or self.config.agy_working_dir or os.getcwd()
        conv_id = session.conversation_id or "New session"

        report_md = f"""# Project Report: {active_proj}

### Workspace Details
- **Project Name:** `{active_proj}`
- **Working Directory:** `{active_path}`
- **Conversation ID:** `{conv_id}`
- **Model:** `{session.model or self.config.default_model or 'Default'}`
- **Effort:** `{session.effort or self.config.default_effort or 'medium'}`

### Recent Status
Session is currently {'busy' if session.is_busy else 'idle and ready for tasks'}.
"""
        html_doc = generate_standalone_html(
            title=f"Session Report - {active_proj}",
            markdown_content=report_md,
            metadata={
                "model": session.model or self.config.default_model,
                "conversation_id": session.conversation_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        filename = f"session_report_{active_proj}_{int(time.time())}.html"
        await self._safe_send_chat_action(chat_id, "upload_document")
        await self._safe_send_document(
            chat_id=chat_id,
            document_content=html_doc,
            filename=filename,
            caption=f"📄 <b>{escape_html(filename)}</b>",
            reply_to_message_id=reply_to_id,
        )

    async def handle_cmd_steer(self, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
        """Steer/guide the currently executing agy prompt with new instruction."""
        if not arg:
            await self._safe_send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/steer &lt;instruction&gt;</code>\nInjects instruction into ongoing agy execution.",
                reply_to_message_id=reply_to_id,
            )
            return

        bridge = self.active_bridges.get(chat_id)
        if bridge and bridge.is_running():
            steered = await bridge.steer(arg)
            if steered:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text=f"🎯 <b>Steering instruction sent:</b>\n<i>{escape_html(arg)}</i>\n\nThe agent will incorporate this guidance into the ongoing turn.",
                    reply_to_message_id=reply_to_id,
                )
            else:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="⚠️ Could not steer: the current task is already concluding.",
                    reply_to_message_id=reply_to_id,
                )
        else:
            await self._safe_send_message(
                chat_id=chat_id,
                text="ℹ️ No task is currently running to steer. Send a regular prompt to start.",
                reply_to_message_id=reply_to_id,
            )

    async def handle_cmd_stop(self, chat_id: int) -> None:
        """Interrupt active agy prompt execution immediately."""
        bridge = self.active_bridges.get(chat_id)
        if bridge:
            await bridge.stop()
            self.active_bridges.pop(chat_id, None)
            self.session_manager.set_busy(chat_id, False)
            await self._safe_send_message(chat_id=chat_id, text="🛑 <b>Task interrupted by user.</b>")
        else:
            await self._safe_send_message(chat_id=chat_id, text="ℹ️ No active task is currently running.")

    async def handle_prompt(self, chat_id: int, prompt: str, reply_to_id: Optional[int]) -> None:
        """Execute agy with prompt and stream real-time results back to Telegram with document export support."""
        session = self.session_manager.get_session(chat_id)

        if session.is_busy:
            # Attempt to steer into existing session
            bridge = self.active_bridges.get(chat_id)
            if bridge:
                steered = await bridge.steer(prompt)
                if steered:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=f"🎯 <b>Steered into active session:</b>\n<i>{escape_html(prompt)}</i>",
                        reply_to_message_id=reply_to_id,
                    )
                    return

            await self._safe_send_message(
                chat_id=chat_id,
                text="⏳ A prompt is currently processing. Use /steer to guide it or /stop to cancel.",
                reply_to_message_id=reply_to_id,
            )
            return

        # Ensure active project exists or auto-initialize default
        if not session.active_project or session.active_project not in session.projects:
            if session.projects:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="⚠️ No active project selected. Use <code>/open &lt;name&gt;</code> to select a project or <code>/new &lt;name&gt; &lt;path&gt;</code> to create one.",
                    reply_to_message_id=reply_to_id,
                )
                return
            else:
                default_dir = self.config.agy_working_dir or os.getcwd()
                self.session_manager.create_project(chat_id, "default", default_dir)
                session = self.session_manager.get_session(chat_id)

        self.session_manager.set_busy(chat_id, True)

        # Send initial placeholder message safely
        init_msg = await self._safe_send_message(
            chat_id=chat_id,
            text="⏳ <i>Contacting agy...</i>",
            reply_to_message_id=reply_to_id,
        )
        msg_id = init_msg.get("message_id") if init_msg else None

        # Prepare bridge parameters
        effective_model = session.model or self.config.default_model or None
        effective_effort = session.effort or self.config.default_effort or None
        effective_dir = session.active_path() or self.config.agy_working_dir or None

        bridge = AgyBridge(
            agy_bin=self.config.get_resolved_agy_bin(),
            default_working_dir=effective_dir,
            timeout_seconds=self.config.agy_timeout_seconds,
            system_prompt=self.config.system_prompt,
        )
        self.active_bridges[chat_id] = bridge

        tool_events: List[str] = []
        created_files: List[Path] = []
        accumulated_text = ""
        last_edit_time = time.time()
        last_edited_content = ""
        final_event: Optional[AgyEvent] = None
        conv_id = session.conversation_id
        total_tokens = 0
        thinking_tokens = 0
        total_duration = 0.0

        try:
            async for event in bridge.run_streaming(
                prompt=prompt,
                conversation_id=session.conversation_id,
                model=effective_model,
                effort=effective_effort,
                working_dir=effective_dir,
                system_prompt=self.config.system_prompt,
            ):
                now = time.time()

                if event.type == "init":
                    if event.conversation_id:
                        conv_id = event.conversation_id
                        self.session_manager.set_conversation_id(chat_id, conv_id)

                elif event.type == "tool":
                    t_summary = format_tool_summary(
                        event.tool_name or "unknown",
                        event.tool_state or "ACTIVE",
                        event.tool_info,
                    )
                    # Track files generated/written by tools
                    if event.tool_name in ("write_to_file", "replace_file_content") and event.tool_state == "DONE":
                        params = event.tool_info.get("parameters", {}) if isinstance(event.tool_info, dict) else {}
                        file_path_str = params.get("TargetFile") or params.get("AbsolutePath")
                        if file_path_str:
                            p = Path(file_path_str)
                            if p.is_file() and p not in created_files:
                                created_files.append(p)

                    if event.tool_state in ("DONE", "ERROR") and tool_events:
                        tool_events[-1] = t_summary
                    else:
                        tool_events.append(t_summary)

                elif event.type == "text_delta":
                    accumulated_text += event.text_delta

                elif event.type == "turn_complete":
                    if event.usage:
                        total_tokens += event.usage.get("total_tokens", 0)
                        thinking_tokens += event.usage.get("thinking_tokens", 0)
                    if event.duration_seconds:
                        total_duration += event.duration_seconds
                    accumulated_text += "\n\n🎯 <i>[Steered turn in progress...]</i>\n"

                elif event.type in ("result", "error"):
                    final_event = event
                    if event.usage:
                        total_tokens += event.usage.get("total_tokens", 0)
                        thinking_tokens += event.usage.get("thinking_tokens", 0)
                    if event.duration_seconds:
                        total_duration += event.duration_seconds

                # Throttled live update
                if (now - last_edit_time) >= self.config.stream_throttle_seconds:
                    clean_stream_text, stream_blocks = extract_agy_file_blocks(accumulated_text)
                    display_stream_text = clean_stream_text
                    if stream_blocks:
                        files_label = ", ".join(b.filename for b in stream_blocks)
                        if display_stream_text:
                            display_stream_text += f"\n\n📄 <i>[Generating standalone report: {files_label}...]</i>"
                        else:
                            display_stream_text = f"📄 <i>[Generating standalone report: {files_label}...]</i>"

                    preview = format_streaming_message(
                        tool_events=tool_events,
                        current_text=display_stream_text,
                        cursor=True,
                    )
                    if preview != last_edited_content:
                        edit_res = await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=preview,
                        )
                        if edit_res:
                            last_edited_content = preview
                            last_edit_time = now

            # Process final message output
            if bridge._is_stopped:
                res = await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🛑 <b>Task cancelled by /stop</b>",
                )
                if res is None and not msg_id:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text="🛑 <b>Task cancelled by /stop</b>",
                        reply_to_message_id=reply_to_id,
                    )
                return

            if final_event and final_event.type == "error":
                err_text = f"❌ <b>agy execution error:</b>\n{escape_html(final_event.response)}"
                res = await self._safe_edit_message_text(chat_id=chat_id, message_id=msg_id, text=err_text)
                if res is None and not msg_id:
                    await self._safe_send_message(chat_id=chat_id, text=err_text, reply_to_message_id=reply_to_id)
                return

            # Normal success outcome
            final_text = ""
            if final_event and final_event.response:
                final_text = final_event.response
            elif accumulated_text:
                final_text = accumulated_text
            else:
                final_text = "(Done, no textual response)"

            duration = total_duration or (final_event.duration_seconds if final_event else None)
            tokens_val = total_tokens or (final_event.usage.get("total_tokens") if final_event else None)
            thinking_val = thinking_tokens or (final_event.usage.get("thinking_tokens") if final_event else None)

            # Check for explicit [AGY_FILE] protocol blocks first
            cleaned_text, file_blocks = extract_agy_file_blocks(final_text)

            if file_blocks:
                effective_dir = session.active_path() or self.config.agy_working_dir or os.getcwd()
                sent_count = 0
                file_errors: List[str] = []

                for block in file_blocks:
                    metadata = {
                        "time": f"{duration:.1f}s" if duration else None,
                        "tokens": f"{tokens_val/1000:.1f}k" if tokens_val and tokens_val >= 1000 else tokens_val,
                        "model": effective_model,
                        "conversation_id": conv_id,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    html_doc, err_msg = render_agy_file(block, base_dir=effective_dir, metadata=metadata)
                    if err_msg:
                        logger.warning("Failed to render [AGY_FILE] %s: %s", block.filename, err_msg)
                        file_errors.append(f"❌ Failed to export file (<code>{escape_html(block.filename)}</code>): {escape_html(err_msg)}")
                        continue

                    await self._safe_send_chat_action(chat_id, "upload_document")
                    doc_res = await self._safe_send_document(
                        chat_id=chat_id,
                        document_content=html_doc,
                        filename=block.filename,
                        caption=f"📄 <b>{escape_html(block.filename)}</b>\n💡 Open in browser or Telegram viewer for full formatting and syntax highlighting",
                        reply_to_message_id=reply_to_id or msg_id,
                    )
                    if doc_res:
                        sent_count += 1
                    else:
                        file_errors.append(f"❌ Failed to send file: <code>{escape_html(block.filename)}</code>")

                # Update text message with summary/preview and file notice
                preview_length = 1500
                visible_body = cleaned_text.strip()
                if not visible_body:
                    visible_body = "📄 <b>Task report generated</b>"
                elif len(visible_body) > preview_length:
                    visible_body = visible_body[:preview_length].strip() + "\n\n<i>[...Full report attached as HTML document below...]</i>"

                formatted_summary = format_final_result(
                    response_text=visible_body,
                    duration_seconds=duration,
                    total_tokens=tokens_val,
                    thinking_tokens=thinking_val,
                    conversation_id=conv_id,
                )

                notices: List[str] = []
                if sent_count > 0:
                    if sent_count == 1:
                        notices.append("📄 <b>Full report generated and attached below ⬇️</b>")
                    else:
                        notices.append(f"📄 <b>{sent_count} report file(s) generated and attached below ⬇️</b>")
                if file_errors:
                    notices.extend(file_errors)

                notice_text = ("\n\n" + "\n\n".join(notices)) if notices else ""
                full_text = formatted_summary + notice_text

                res = await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=full_text,
                )
                if res is None and not msg_id:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=full_text,
                        reply_to_message_id=reply_to_id,
                    )

            elif should_export_as_file(prompt=prompt, response_text=final_text):
                # Fallback heuristic file export
                preview_length = 1200
                summary_snippet = final_text[:preview_length].strip()
                if len(final_text) > preview_length:
                    summary_snippet += "\n\n<i>[...Full report attached as HTML document below...]</i>"

                formatted_summary = format_final_result(
                    response_text=summary_snippet,
                    duration_seconds=duration,
                    total_tokens=tokens_val,
                    thinking_tokens=thinking_val,
                    conversation_id=conv_id,
                )
                notice = "\n\n📄 <b>Full HTML report automatically generated and attached below ⬇️</b>"
                res = await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=formatted_summary + notice,
                )
                if res is None and not msg_id:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=formatted_summary + notice,
                        reply_to_message_id=reply_to_id,
                    )

                # Generate and upload standalone HTML document
                is_diff = "```diff" in final_text or "diff --git" in final_text
                doc_title = "Diff Report" if is_diff else f"agy Response: {prompt[:40]}"
                html_doc = generate_standalone_html(
                    title=doc_title,
                    markdown_content=final_text,
                    metadata={
                        "time": f"{duration:.1f}s" if duration else None,
                        "tokens": f"{tokens_val/1000:.1f}k" if tokens_val and tokens_val >= 1000 else tokens_val,
                        "model": effective_model,
                        "conversation_id": conv_id,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                prefix = "diff" if is_diff else "report"
                filename = f"{prefix}_{int(time.time())}.html"

                await self._safe_send_chat_action(chat_id, "upload_document")
                await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=html_doc,
                    filename=filename,
                    caption=f"📄 <b>{escape_html(filename)}</b>\n💡 Open in browser or Telegram viewer for full formatting and syntax highlighting",
                    reply_to_message_id=reply_to_id or msg_id,
                )
            else:
                # Normal formatted message output
                formatted_final = format_final_result(
                    response_text=final_text,
                    duration_seconds=duration,
                    total_tokens=tokens_val,
                    thinking_tokens=thinking_val,
                    conversation_id=conv_id,
                )

                chunks = split_text_chunks(formatted_final)
                if len(chunks) == 1:
                    res = await self._safe_edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=chunks[0],
                    )
                    if res is None and not msg_id:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunks[0],
                            reply_to_message_id=reply_to_id,
                        )
                else:
                    res = await self._safe_edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=chunks[0],
                    )
                    if res is None and not msg_id:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunks[0],
                            reply_to_message_id=reply_to_id,
                        )
                    for chunk in chunks[1:]:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunk,
                        )

            # If tools created specific new files and user might want them
            if created_files:
                for created_p in created_files[:2]:
                    try:
                        await self._safe_send_document(
                            chat_id=chat_id,
                            document_content=created_p,
                            filename=created_p.name,
                            caption=f"📁 Generated: <code>{escape_html(created_p.name)}</code>",
                            reply_to_message_id=reply_to_id or msg_id,
                        )
                    except Exception as e:
                        logger.debug("Failed sending tool created file: %s", e)

        except asyncio.CancelledError:
            logger.info("handle_prompt cancelled for chat %s", chat_id)
            res = await self._safe_edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="🛑 <b>Task interrupted.</b>",
            )
            if res is None and not msg_id:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="🛑 <b>Task interrupted.</b>",
                    reply_to_message_id=reply_to_id,
                )
        except Exception as e:
            logger.error("Error running agy prompt for chat %s: %s", chat_id, e, exc_info=True)
            err_msg = f"❌ <b>Internal Gateway Error:</b>\n{escape_html(str(e))}"
            res = await self._safe_edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=err_msg,
            )
            if res is None and not msg_id:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text=err_msg,
                    reply_to_message_id=reply_to_id,
                )
        finally:
            self.active_bridges.pop(chat_id, None)
            self.session_manager.set_busy(chat_id, False)


if __name__ == "__main__":
    cfg = Config.load()
    bot = AgyTelegramBot(cfg)
    print("AgyTelegramBot initialized with file export and steering support!")
