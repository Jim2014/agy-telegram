"""Telegram Bot orchestrator: handles long polling, slash commands, and streaming bridge."""

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
from gateway.message_formatter import (
    escape_html,
    format_final_result,
    format_streaming_message,
    format_tool_summary,
    split_text_chunks,
)
from gateway.session_manager import ChatSession, SessionManager
from gateway.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


class AgyTelegramBot:
    """Core bot runner managing Telegram communication and agy executions."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = TelegramClient(bot_token=config.bot_token)
        self.session_manager = SessionManager(persistence_file=config.sessions_file)
        self.active_bridges: Dict[int, AgyBridge] = {}
        self.running = False
        self.bot_info: Dict[str, Any] = {}

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
        except Exception as e:
            logger.error("Failed to connect to Telegram: %s", e)
            print(f"\n❌ Error connecting to Telegram API: {e}")
            return

        self.running = True
        print("🚀 agy-telegram gateway is running! Press Ctrl+C to stop.\n")

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
                    # Dispatch update asynchronously to prevent blocking long polling
                    asyncio.create_task(self.process_update(update))
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
        """Process a single Telegram update."""
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
            await self.client.send_message(
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
            elif cmd == "/dir":
                await self.handle_cmd_dir(chat_id, arg)
            elif cmd == "/steer":
                await self.handle_cmd_steer(chat_id, arg, message_id)
            elif cmd == "/stop":
                await self.handle_cmd_stop(chat_id)
            else:
                await self.client.send_message(
                    chat_id=chat_id,
                    text=f"❓ Unknown command <code>{escape_html(cmd)}</code>. Use /help for instructions.",
                )
            return

        # If a prompt is running and user sends a new message -> steer into active session!
        session = self.session_manager.get_session(chat_id)
        if session.is_busy and chat_id in self.active_bridges:
            steered = await self.active_bridges[chat_id].steer(text)
            if steered:
                await self.client.send_message(
                    chat_id=chat_id,
                    text=f"🎯 <b>Steered into active session:</b>\n<i>{escape_html(text)}</i>",
                    reply_to_message_id=message_id,
                )
                return

        # Regular prompt -> agy bridge execution
        await self.handle_prompt(chat_id, text, message_id)

    async def handle_cmd_help(self, chat_id: int) -> None:
        """Send help message."""
        session = self.session_manager.get_session(chat_id)
        active_proj = session.active_project or "(none)"
        conv_id = session.conversation_id
        conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"
        help_text = (
            "🤖 <b>agy-telegram Gateway</b>\n\n"
            "Direct Telegram interface to <b>agy</b> (Antigravity CLI) with project-based session management, real-time intermediate streaming, and mid-flight steering.\n\n"
            "<b>Project Management:</b>\n"
            "• <code>/new &lt;name&gt; &lt;path&gt;</code> — Create a new project workspace\n"
            "• <code>/open &lt;name&gt;</code> — Open and switch to an existing project\n"
            "• <code>/projects</code> — List all projects and active status\n"
            "• <code>/delete &lt;name&gt;</code> — Delete a project\n"
            "• <code>/reset</code> — Clear conversation history for active project\n"
            "• <code>/dir &lt;path&gt;</code> — Set workspace directory for active project\n\n"
            "<b>Configuration & Control:</b>\n"
            "• <code>/status</code> — Show active project, conversation, model, & effort\n"
            "• <code>/model &lt;name&gt;</code> — Set model (e.g. <code>/model gemini-2.5-pro</code>) or <code>/model reset</code>\n"
            "• <code>/effort &lt;low|medium|high&gt;</code> — Set reasoning effort\n"
            "• <code>/steer &lt;instruction&gt;</code> — Steer/guide currently running agy prompt\n"
            "• <code>/stop</code> — Interrupt currently executing agy prompt\n"
            "• <code>/help</code> — Show this manual\n\n"
            f"<b>Active Project:</b> <code>{escape_html(active_proj)}</code> ({conv_text})\n\n"
            "💡 <i>Tip: While agy is running, you can send any message or use /steer to seamlessly guide the session!</i>"
        )
        await self.client.send_message(chat_id=chat_id, text=help_text)

    async def handle_cmd_new(self, chat_id: int, arg: str) -> None:
        """Create a new project workspace (/new <name> <path>) or open if exists."""
        if self.session_manager.is_busy(chat_id):
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before switching projects.",
            )
            return

        parts = arg.split(maxsplit=1) if arg else []
        session = self.session_manager.get_session(chat_id)

        if len(parts) == 0:
            await self.client.send_message(
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
            await self.client.send_message(
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
            await self.client.send_message(
                chat_id=chat_id,
                text=f"⚠️ Usage: <code>/new {escape_html(name)} &lt;path&gt;</code>\nPlease provide the directory path for the new project.",
            )
            return

        path_str = parts[1].strip()
        target_path = str(Path(path_str).expanduser().resolve())
        self.session_manager.create_project(chat_id, name, target_path)

        await self.client.send_message(
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
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before switching projects.",
            )
            return

        if not arg:
            await self.client.send_message(
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
            await self.client.send_message(chat_id=chat_id, text=msg)
            return

        self.session_manager.open_project(chat_id, name)
        path_val = session.active_path() or ""
        conv_id = session.conversation_id
        conv_text = f"<code>{escape_html(conv_id[:8])}...</code>" if conv_id else "<i>(New session)</i>"

        await self.client.send_message(
            chat_id=chat_id,
            text=(
                f"📂 <b>Opened project:</b> <code>{escape_html(name)}</code>\n"
                f"• <b>Path:</b> <code>{escape_html(path_val)}</code>\n"
                f"• <b>Conversation:</b> {conv_text}"
            ),
        )

    async def handle_cmd_delete(self, chat_id: int, arg: str) -> None:
        """Delete a project (/delete <name>)."""
        if self.session_manager.is_busy(chat_id):
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first.",
            )
            return

        if not arg:
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/delete &lt;name&gt;</code>",
            )
            return

        name = arg.strip()
        deleted = self.session_manager.delete_project(chat_id, name)
        if deleted:
            await self.client.send_message(
                chat_id=chat_id,
                text=f"🗑 <b>Project deleted:</b> <code>{escape_html(name)}</code>",
            )
        else:
            await self.client.send_message(
                chat_id=chat_id,
                text=f"❌ Project <code>{escape_html(name)}</code> not found.",
            )

    async def handle_cmd_reset(self, chat_id: int) -> None:
        """Clear conversation_id for active project."""
        if self.session_manager.is_busy(chat_id):
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ A task is currently running. Please /stop it first before resetting conversation.",
            )
            return

        session = self.session_manager.get_session(chat_id)
        if not session.active_project or session.active_project not in session.projects:
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ No active project. Use <code>/new &lt;name&gt; &lt;path&gt;</code> or <code>/open &lt;name&gt;</code> first.",
            )
            return

        self.session_manager.reset_conversation(chat_id)
        await self.client.send_message(
            chat_id=chat_id,
            text=f"🔄 <b>Conversation reset</b> for project <code>{escape_html(session.active_project)}</code>.",
        )

    async def handle_cmd_projects(self, chat_id: int) -> None:
        """List all projects for this chat."""
        session = self.session_manager.get_session(chat_id)
        projects = self.session_manager.list_projects(chat_id)

        if not projects:
            await self.client.send_message(
                chat_id=chat_id,
                text="📁 <b>No projects found.</b>\nUse <code>/new &lt;name&gt; &lt;path&gt;</code> to create a project.",
            )
            return

        entries: List[str] = []
        for p_name, p_data in projects.items():
            is_active = (p_name == session.active_project)
            badge = " ⭐ <b>[ACTIVE]</b>" if is_active else ""
            p_path = p_data.get("path", "")
            conv = p_data.get("conversation_id")
            conv_disp = f"<code>{escape_html(conv[:8])}...</code>" if conv else "<i>(none)</i>"
            t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p_data.get("last_active_at", time.time())))
            entries.append(
                f"• <b>{escape_html(p_name)}</b>{badge}\n"
                f"  Path: <code>{escape_html(p_path)}</code>\n"
                f"  Conv: {conv_disp}\n"
                f"  Last active: <code>{t_str}</code>"
            )

        body = "\n\n".join(entries)
        await self.client.send_message(
            chat_id=chat_id,
            text=f"📁 <b>Projects ({len(projects)}):</b>\n\n{body}",
        )

    async def handle_cmd_status(self, chat_id: int) -> None:
        """Display current chat status."""
        session = self.session_manager.get_session(chat_id)
        active_name = session.active_project or "<i>(None)</i>"
        active_path = session.active_path() or "<i>(None)</i>"
        conv_id = session.conversation_id or "<i>(None - new session)</i>"
        model = session.model or self.config.default_model or "<i>(agy default)</i>"
        effort = session.effort or self.config.default_effort or "medium"
        busy = "🏃 <b>Running</b>" if session.is_busy else "💤 <b>Idle</b>"
        total_projects = len(session.projects)

        status_text = (
            "📊 <b>agy-telegram Status</b>\n\n"
            f"• <b>Status:</b> {busy}\n"
            f"• <b>Active Project:</b> <code>{escape_html(active_name)}</code>\n"
            f"• <b>Project Path:</b> <code>{escape_html(active_path)}</code>\n"
            f"• <b>Conversation ID:</b> <code>{escape_html(conv_id)}</code>\n"
            f"• <b>Total Projects:</b> <code>{total_projects}</code>\n"
            f"• <b>Model:</b> {escape_html(model)}\n"
            f"• <b>Effort:</b> <code>{escape_html(effort)}</code>\n"
            f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"• <b>agy Binary:</b> <code>{escape_html(self.config.get_resolved_agy_bin())}</code>"
        )
        await self.client.send_message(chat_id=chat_id, text=status_text)

    async def handle_cmd_model(self, chat_id: int, arg: str) -> None:
        """Configure model override."""
        if not arg:
            session = self.session_manager.get_session(chat_id)
            current = session.model or self.config.default_model or "agy default"
            await self.client.send_message(
                chat_id=chat_id,
                text=f"ℹ️ Current model: <code>{escape_html(current)}</code>\nUsage: <code>/model &lt;name&gt;</code> or <code>/model reset</code>",
            )
            return

        if arg.lower() in ("reset", "default", "none"):
            self.session_manager.set_model(chat_id, None)
            await self.client.send_message(chat_id=chat_id, text="🔄 Model reset to default.")
        else:
            self.session_manager.set_model(chat_id, arg)
            await self.client.send_message(chat_id=chat_id, text=f"✅ Model set to: <code>{escape_html(arg)}</code>")

    async def handle_cmd_effort(self, chat_id: int, arg: str) -> None:
        """Configure effort level."""
        if not arg:
            session = self.session_manager.get_session(chat_id)
            current = session.effort or self.config.default_effort or "medium"
            await self.client.send_message(
                chat_id=chat_id,
                text=f"ℹ️ Current effort: <code>{escape_html(current)}</code>\nUsage: <code>/effort &lt;low|medium|high&gt;</code>",
            )
            return

        arg_lower = arg.lower()
        if arg_lower in ("low", "medium", "high"):
            self.session_manager.set_effort(chat_id, arg_lower)
            await self.client.send_message(chat_id=chat_id, text=f"✅ Reasoning effort set to: <code>{arg_lower}</code>")
        elif arg_lower in ("reset", "default"):
            self.session_manager.set_effort(chat_id, None)
            await self.client.send_message(chat_id=chat_id, text="🔄 Effort reset to default.")
        else:
            await self.client.send_message(chat_id=chat_id, text="⚠️ Invalid effort. Choose: <code>low</code>, <code>medium</code>, or <code>high</code>.")

    async def handle_cmd_dir(self, chat_id: int, arg: str) -> None:
        """Configure workspace directory for current active project."""
        session = self.session_manager.get_session(chat_id)
        if not session.active_project or session.active_project not in session.projects:
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ No active project. Use <code>/new &lt;name&gt; &lt;path&gt;</code> or <code>/open &lt;name&gt;</code> first.",
            )
            return

        if not arg:
            current = session.active_path() or "(None)"
            await self.client.send_message(
                chat_id=chat_id,
                text=f"📁 Current workspace for <b>{escape_html(session.active_project)}</b>:\n<code>{escape_html(current)}</code>\n\nUsage: <code>/dir &lt;path&gt;</code>",
            )
            return

        target_dir = Path(arg).expanduser().resolve()
        if not target_dir.is_dir():
            await self.client.send_message(
                chat_id=chat_id,
                text=f"⚠️ Directory does not exist: <code>{escape_html(str(target_dir))}</code>",
            )
            return

        self.session_manager.set_project_path(chat_id, session.active_project, str(target_dir))
        await self.client.send_message(
            chat_id=chat_id,
            text=f"✅ Workspace directory for <b>{escape_html(session.active_project)}</b> set to:\n<code>{escape_html(str(target_dir))}</code>",
        )

    async def handle_cmd_steer(self, chat_id: int, arg: str, reply_to_id: Optional[int] = None) -> None:
        """Explicitly steer instructions into the currently running session."""
        if not arg:
            await self.client.send_message(
                chat_id=chat_id,
                text="⚠️ Usage: <code>/steer &lt;instruction&gt;</code>\nProvide mid-flight guidance to the running agent.",
                reply_to_message_id=reply_to_id,
            )
            return

        session = self.session_manager.get_session(chat_id)
        bridge = self.active_bridges.get(chat_id)

        if session.is_busy and bridge:
            steered = await bridge.steer(arg)
            if steered:
                await self.client.send_message(
                    chat_id=chat_id,
                    text=f"🎯 <b>Steering instruction sent:</b>\n<i>{escape_html(arg)}</i>\n\nThe agent will incorporate this guidance into the ongoing turn.",
                    reply_to_message_id=reply_to_id,
                )
            else:
                await self.client.send_message(
                    chat_id=chat_id,
                    text="⚠️ Could not steer: the current task is already concluding.",
                    reply_to_message_id=reply_to_id,
                )
        else:
            await self.client.send_message(
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
            await self.client.send_message(chat_id=chat_id, text="🛑 <b>Task interrupted by user.</b>")
        else:
            await self.client.send_message(chat_id=chat_id, text="ℹ️ No active task is currently running.")

    async def handle_prompt(self, chat_id: int, prompt: str, reply_to_id: Optional[int]) -> None:
        """Execute agy with prompt and stream real-time results back to Telegram."""
        session = self.session_manager.get_session(chat_id)

        if session.is_busy:
            # Attempt to steer into existing session
            bridge = self.active_bridges.get(chat_id)
            if bridge:
                steered = await bridge.steer(prompt)
                if steered:
                    await self.client.send_message(
                        chat_id=chat_id,
                        text=f"🎯 <b>Steered into active session:</b>\n<i>{escape_html(prompt)}</i>",
                        reply_to_message_id=reply_to_id,
                    )
                    return

            await self.client.send_message(
                chat_id=chat_id,
                text="⏳ A prompt is currently processing. Use /steer to guide it or /stop to cancel.",
                reply_to_message_id=reply_to_id,
            )
            return

        # Ensure active project exists or auto-initialize default
        if not session.active_project or session.active_project not in session.projects:
            if session.projects:
                await self.client.send_message(
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

        # Send initial placeholder message
        init_msg = await self.client.send_message(
            chat_id=chat_id,
            text="⏳ <i>Contacting agy...</i>",
            reply_to_message_id=reply_to_id,
        )
        msg_id = init_msg.get("message_id")
        if not msg_id:
            logger.error("Failed to obtain message_id for initial response")
            self.session_manager.set_busy(chat_id, False)
            return

        # Prepare bridge parameters
        effective_model = session.model or self.config.default_model or None
        effective_effort = session.effort or self.config.default_effort or None
        effective_dir = session.active_path() or self.config.agy_working_dir or None

        bridge = AgyBridge(
            agy_bin=self.config.get_resolved_agy_bin(),
            default_working_dir=effective_dir,
            timeout_seconds=self.config.agy_timeout_seconds,
        )
        self.active_bridges[chat_id] = bridge

        tool_events: List[str] = []
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
                    # If this is updating the most recent active tool, replace it
                    if event.tool_state in ("DONE", "ERROR") and tool_events:
                        tool_events[-1] = t_summary
                    else:
                        tool_events.append(t_summary)

                elif event.type == "text_delta":
                    accumulated_text += event.text_delta

                elif event.type == "turn_complete":
                    # Intermediate turn finished as part of multi-turn steer exchange
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
                    preview = format_streaming_message(
                        tool_events=tool_events,
                        current_text=accumulated_text,
                        cursor=True,
                    )
                    if preview != last_edited_content:
                        await self.client.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=preview,
                        )
                        last_edited_content = preview
                        last_edit_time = now

            # Process final message output
            if bridge._is_stopped:
                await self.client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🛑 <b>Task cancelled by /stop</b>",
                )
                return

            if final_event and final_event.type == "error":
                err_text = f"❌ <b>agy execution error:</b>\n{escape_html(final_event.response)}"
                await self.client.edit_message_text(chat_id=chat_id, message_id=msg_id, text=err_text)
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

            formatted_final = format_final_result(
                response_text=final_text,
                duration_seconds=duration,
                total_tokens=tokens_val,
                thinking_tokens=thinking_val,
                conversation_id=conv_id,
            )

            # Check if text fits in a single Telegram message
            chunks = split_text_chunks(formatted_final)
            if len(chunks) == 1:
                await self.client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=chunks[0],
                )
            else:
                # Update first message with chunk 1, send remaining chunks
                await self.client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=chunks[0],
                )
                for chunk in chunks[1:]:
                    await self.client.send_message(
                        chat_id=chat_id,
                        text=chunk,
                    )

        except asyncio.CancelledError:
            logger.info("handle_prompt cancelled for chat %s", chat_id)
            await self.client.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="🛑 <b>Task interrupted.</b>",
            )
        except Exception as e:
            logger.error("Error running agy prompt for chat %s: %s", chat_id, e, exc_info=True)
            await self.client.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"❌ <b>Internal Gateway Error:</b>\n{escape_html(str(e))}",
            )
        finally:
            self.active_bridges.pop(chat_id, None)
            self.session_manager.set_busy(chat_id, False)


if __name__ == "__main__":
    cfg = Config.load()
    bot = AgyTelegramBot(cfg)
    print("AgyTelegramBot class initialized successfully with steering support!")
