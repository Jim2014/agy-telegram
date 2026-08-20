"""Telegram Bot orchestrator: handles long polling, message routing, streaming bridge, and task lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is in sys.path when executed directly
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.agy_bridge import AgyBridge, AgyEvent
from gateway.commands import (
    handle_callback_query as cmd_handle_callback_query,
    handle_cmd_artifacts as cmd_handle_artifacts,
    handle_cmd_delete as cmd_handle_delete,
    handle_cmd_dir as cmd_handle_dir,
    handle_cmd_effort as cmd_handle_effort,
    handle_cmd_export as cmd_handle_export,
    handle_cmd_file as cmd_handle_file,
    handle_cmd_help as cmd_handle_help,
    handle_cmd_model as cmd_handle_model,
    handle_cmd_new as cmd_handle_new,
    handle_cmd_open as cmd_handle_open,
    handle_cmd_projects as cmd_handle_projects,
    handle_cmd_reset as cmd_handle_reset,
    handle_cmd_skills as cmd_handle_skills,
    handle_cmd_status as cmd_handle_status,
    handle_cmd_steer as cmd_handle_steer,
    handle_cmd_stop as cmd_handle_stop,
    handle_cmd_usage as cmd_handle_usage,
)
from gateway.config import Config
from gateway.media_handlers import (
    handle_document_message as mh_handle_document_message,
    handle_voice_message as mh_handle_voice_message,
)
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
from gateway.ui_keyboards import build_adaptive_bot_commands

logger = logging.getLogger(__name__)


DEFAULT_BOT_COMMANDS = build_adaptive_bot_commands(None, max_commands=8)


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
            await self.client.set_chat_menu_button(menu_button={"type": "commands"})
            return success
        except Exception as e:
            logger.warning("Error registering bot commands: %s", e)
            return False

    async def update_chat_command_menu(self, chat_id: int) -> bool:
        """Update adaptive LRU-sorted command menu for a specific chat."""
        try:
            session = self.session_manager.get_session(chat_id)
            adaptive_cmds = build_adaptive_bot_commands(session, max_commands=8)
            return await self.client.set_my_commands(
                commands=adaptive_cmds,
                scope={"type": "chat", "chat_id": chat_id},
            )
        except Exception as e:
            logger.debug("Failed to update adaptive commands for chat %s: %s", chat_id, e)
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
            await self.setup_bot_commands()
        except Exception as e:
            logger.error("Failed to connect to Telegram: %s", e)
            print(f"\n❌ Error connecting to Telegram API: {e}")
            return

        self.running = True
        print("🚀 agy-telegram gateway is running! Press Ctrl+C to stop.\n")

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
                wait_time = min(2 ** consecutive_errors, 30)
                logger.warning("Error fetching Telegram updates (attempt %d): %s. Retrying in %ds...", consecutive_errors, e, wait_time)
                await asyncio.sleep(wait_time)

    async def stop(self) -> None:
        """Gracefully stop the bot runner and interrupt active agy processes."""
        self.running = False
        for bridge in list(self.active_bridges.values()):
            try:
                await bridge.stop()
            except Exception as e:
                logger.warning("Error stopping active bridge: %s", e)
        self.active_bridges.clear()
        await self.client.close()

    async def shutdown(self) -> None:
        """Gracefully shutdown the bot (alias for stop)."""
        await self.stop()

    async def process_update(self, update: Dict[str, Any]) -> None:
        """Process a single Telegram update dict."""
        try:
            if "callback_query" in update:
                await self.handle_callback_query(update["callback_query"])
                return

            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if not chat_id:
                return

            # Check authorization whitelist
            if not self.config.is_chat_allowed(chat_id):
                logger.warning("Unauthorized access attempt from chat_id %d", chat_id)
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="⛔ <b>Access Denied</b>\nThis bot is private and your chat ID is not authorized.",
                )
                return

            # Voice or audio message handling
            if message.get("voice") or message.get("audio"):
                await self.handle_voice_message(chat_id, message, message_id)
                return

            # Document or photo file handling
            if message.get("document") or message.get("photo"):
                await self.handle_document_message(chat_id, message, message_id)
                return

            text = message.get("text", "").strip()
            if not text:
                return

            # Slash command routing
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                cmd = parts[0].lower().split("@")[0]
                arg = parts[1].strip() if len(parts) > 1 else ""

                # Record command usage for adaptive LRU menu and update in background
                self.session_manager.record_command_usage(chat_id, cmd)
                asyncio.create_task(self.update_chat_command_menu(chat_id))

                if cmd in ("/start", "/help"):
                    await self.handle_cmd_help(chat_id)
                elif cmd in ("/usage", "/quota"):
                    await self.handle_cmd_usage(chat_id, reply_to_id=message_id)
                elif cmd in ("/skills", "/plugins"):
                    await self.handle_cmd_skills(chat_id, reply_to_id=message_id)
                elif cmd in ("/artifacts", "/scratch"):
                    await self.handle_cmd_artifacts(chat_id, arg, reply_to_id=message_id)
                elif cmd == "/new":
                    await self.handle_cmd_new(chat_id, arg)
                elif cmd == "/open":
                    await self.handle_cmd_open(chat_id, arg)
                elif cmd == "/projects":
                    await self.handle_cmd_projects(chat_id)
                elif cmd == "/delete":
                    await self.handle_cmd_delete(chat_id, arg)
                elif cmd == "/reset":
                    await self.handle_cmd_reset(chat_id)
                elif cmd == "/status":
                    await self.handle_cmd_status(chat_id)
                elif cmd == "/model":
                    await self.handle_cmd_model(chat_id, arg)
                elif cmd == "/effort":
                    await self.handle_cmd_effort(chat_id, arg)
                elif cmd in ("/dir", "/ls"):
                    await self.handle_cmd_dir(chat_id, arg, message_id)
                elif cmd == "/file":
                    await self.handle_cmd_file(chat_id, arg, message_id)
                elif cmd == "/export":
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

    # Backward-compatible command and media delegation wrappers
    async def handle_cmd_help(self, chat_id: int) -> None:
        await cmd_handle_help(self, chat_id)

    async def handle_cmd_usage(
        self,
        chat_id: int,
        edit_message_id: Optional[int] = None,
        reply_to_id: Optional[int] = None,
    ) -> None:
        await cmd_handle_usage(self, chat_id, edit_message_id, reply_to_id)

    async def handle_cmd_skills(
        self,
        chat_id: int,
        edit_message_id: Optional[int] = None,
        reply_to_id: Optional[int] = None,
    ) -> None:
        await cmd_handle_skills(self, chat_id, edit_message_id, reply_to_id)

    async def handle_cmd_artifacts(
        self,
        chat_id: int,
        arg: str = "",
        edit_message_id: Optional[int] = None,
        reply_to_id: Optional[int] = None,
    ) -> None:
        await cmd_handle_artifacts(self, chat_id, arg, edit_message_id, reply_to_id)

    async def handle_cmd_new(self, chat_id: int, arg: str) -> None:
        await cmd_handle_new(self, chat_id, arg)

    async def handle_cmd_open(self, chat_id: int, arg: str) -> None:
        await cmd_handle_open(self, chat_id, arg)

    async def handle_cmd_projects(self, chat_id: int, edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_projects(self, chat_id, edit_message_id)

    async def handle_cmd_delete(self, chat_id: int, arg: str) -> None:
        await cmd_handle_delete(self, chat_id, arg)

    async def handle_cmd_reset(self, chat_id: int) -> None:
        await cmd_handle_reset(self, chat_id)

    async def handle_cmd_status(self, chat_id: int) -> None:
        await cmd_handle_status(self, chat_id)

    async def handle_cmd_model(self, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_model(self, chat_id, arg, edit_message_id)

    async def handle_cmd_effort(self, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_effort(self, chat_id, arg, edit_message_id)

    async def handle_cmd_dir(self, chat_id: int, arg: str = "", reply_to_id: Optional[int] = None) -> None:
        await cmd_handle_dir(self, chat_id, arg, reply_to_id)

    async def handle_cmd_file(self, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
        await cmd_handle_file(self, chat_id, arg, reply_to_id)

    async def handle_cmd_export(self, chat_id: int, reply_to_id: Optional[int]) -> None:
        await cmd_handle_export(self, chat_id, reply_to_id)

    async def handle_cmd_steer(self, chat_id: int, arg: str, reply_to_id: Optional[int]) -> None:
        await cmd_handle_steer(self, chat_id, arg, reply_to_id)

    async def handle_cmd_stop(self, chat_id: int) -> None:
        await cmd_handle_stop(self, chat_id)

    async def handle_callback_query(self, callback_query: Dict[str, Any]) -> None:
        await cmd_handle_callback_query(self, callback_query)

    async def handle_voice_message(self, chat_id: int, message: Dict[str, Any], message_id: Optional[int]) -> None:
        await mh_handle_voice_message(self, chat_id, message, message_id)

    async def handle_document_message(self, chat_id: int, message: Dict[str, Any], message_id: Optional[int]) -> None:
        await mh_handle_document_message(self, chat_id, message, message_id)

    async def handle_prompt(self, chat_id: int, prompt: str, reply_to_id: Optional[int]) -> None:
        """Execute agy with prompt and stream real-time results back to Telegram with document export support."""
        session = self.session_manager.get_session(chat_id)

        if session.is_busy:
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

        init_msg = await self._safe_send_message(
            chat_id=chat_id,
            text="⏳ <i>Contacting agy...</i>",
            reply_to_message_id=reply_to_id,
        )
        msg_id = init_msg.get("message_id") if init_msg else None

        active_proj = session.active_project or "default"
        effective_model = session.model or self.config.default_model or None
        effective_effort = session.effort or self.config.default_effort or None
        effective_dir = session.active_path() or self.config.agy_working_dir or None

        bridge = self.active_bridges.get(chat_id)
        if bridge is None:
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
        start_time = time.time()
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
                        tool_name=event.tool_name or "",
                        state=event.tool_state or "ACTIVE",
                        tool_info=event.tool_info or {},
                        duration_seconds=event.duration_seconds,
                    )
                    if t_summary:
                        if tool_events and tool_events[-1].endswith("..."):
                            tool_events[-1] = t_summary
                        else:
                            tool_events.append(t_summary)

                    if event.tool_name in ("write_to_file", "replace_file_content"):
                        params = event.tool_info.get("parameters", {}) if isinstance(event.tool_info, dict) else {}
                        t_path = params.get("TargetFile") or params.get("target_file")
                        if t_path:
                            try:
                                p = Path(t_path)
                                if p.is_file() and p not in created_files:
                                    created_files.append(p)
                            except Exception:
                                pass

                elif event.type in ("text", "text_delta"):
                    delta = event.text_delta or getattr(event, "content", "") or ""
                    accumulated_text += delta

                elif event.type in ("result", "turn_complete", "done"):
                    final_event = event
                    if event.response:
                        accumulated_text = event.response
                    elif getattr(event, "content", None):
                        accumulated_text = event.content
                    if event.conversation_id:
                        conv_id = event.conversation_id
                        self.session_manager.set_conversation_id(chat_id, conv_id)
                    if event.usage:
                        total_tokens = event.usage.get("total_tokens", total_tokens)
                        thinking_tokens = event.usage.get("thinking_tokens", thinking_tokens)
                    if event.duration_seconds:
                        total_duration = event.duration_seconds
                    break

                elif event.type == "error":
                    final_event = event
                    if event.response:
                        accumulated_text = event.response
                    break

                if now - last_edit_time >= self.config.stream_throttle_seconds:
                    streaming_text = format_streaming_message(
                        tool_events=tool_events,
                        accumulated_text=accumulated_text,
                        elapsed_seconds=now - start_time,
                        conversation_id=conv_id,
                        project_name=active_proj,
                    )
                    if streaming_text != last_edited_content and msg_id:
                        await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=streaming_text,
                        )
                        last_edited_content = streaming_text
                        last_edit_time = now

            if not total_duration:
                total_duration = time.time() - start_time

            metrics_data = {
                "total_tokens": total_tokens,
                "thinking_tokens": thinking_tokens,
                "duration_seconds": total_duration,
            }

            self.session_manager.record_turn_stats(
                chat_id=chat_id,
                turn_tokens=total_tokens,
                turn_thinking=thinking_tokens,
                turn_duration=total_duration,
            )

            # Check for [AGY_FILE] protocol blocks in final output
            final_raw = accumulated_text or (getattr(final_event, "response", "") or getattr(final_event, "content", "") or "" if final_event else "")
            clean_text, agy_files = extract_agy_file_blocks(final_raw)

            # Auto-export long responses as standalone HTML document
            auto_export_content: Optional[str] = None
            auto_export_name = ""
            auto_export_title = ""

            if not agy_files and should_export_as_file(prompt, clean_text):
                is_diff = "diff" in prompt.lower() or "```diff" in clean_text or "diff --git" in clean_text or "--- a/" in clean_text
                prefix = "diff" if is_diff else "report"
                auto_export_name = f"{prefix}_{active_proj}_{int(time.time())}.html"
                auto_export_title = f"{active_proj} {'Diff' if is_diff else 'Report'}"

                auto_export_content = generate_standalone_html(
                    title=auto_export_title,
                    markdown_content=clean_text,
                    metadata={
                        "model": effective_model or self.config.default_model,
                        "project": active_proj,
                        "conversation_id": conv_id,
                    },
                )
                clean_text = (
                    f"📄 <i>Full {prefix} generated and attached below: <code>{auto_export_name}</code></i>\n\n"
                    + (clean_text[:400].strip() + "\n..." if len(clean_text) > 400 else clean_text)
                )

            # Check explicit agy_files and collect render errors
            export_errors: List[str] = []
            valid_exports: List[Tuple[AgyFileBlock, str]] = []
            for af in agy_files:
                html_data, err_msg = render_agy_file(
                    af,
                    base_dir=effective_dir,
                    metadata={
                        "model": effective_model or self.config.default_model,
                        "project": active_proj,
                        "conversation_id": conv_id,
                    },
                )
                if html_data:
                    valid_exports.append((af, html_data))
                elif err_msg:
                    export_errors.append(f"Failed to export file {af.filename}: {err_msg}")

            if agy_files:
                if len(agy_files) == 1:
                    notice = "📄 <i>Full report generated and attached below.</i>"
                else:
                    notice = f"📄 <i>{len(agy_files)} report file(s) generated and attached below.</i>"
                clean_text = f"{clean_text}\n\n{notice}".strip() if clean_text else notice

            if export_errors:
                err_block = "\n".join([f"⚠️ <i>{escape_html(e)}</i>" for e in export_errors])
                clean_text = f"{clean_text}\n\n{err_block}".strip() if clean_text else err_block

            final_formatted = format_final_result(
                response_text=clean_text,
                duration_seconds=total_duration,
                total_tokens=total_tokens,
                thinking_tokens=thinking_tokens,
                conversation_id=conv_id,
                project_name=active_proj,
            )

            chunks = split_text_chunks(final_formatted, chunk_size=4000)

            if chunks:
                if msg_id:
                    await self._safe_edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=chunks[0],
                    )
                else:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=chunks[0],
                        reply_to_message_id=reply_to_id,
                    )

                for chunk in chunks[1:]:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=chunk,
                        reply_to_message_id=reply_to_id,
                    )
            else:
                fallback = f"✅ <b>Done</b> <i>({total_duration:.1f}s)</i>"
                if msg_id:
                    await self._safe_edit_message_text(chat_id=chat_id, message_id=msg_id, text=fallback)
                else:
                    await self._safe_send_message(chat_id=chat_id, text=fallback, reply_to_message_id=reply_to_id)

            # Send explicit [AGY_FILE] protocol exported documents
            for af, html_data in valid_exports:
                await self._safe_send_chat_action(chat_id, "upload_document")
                await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=html_data,
                    filename=af.filename,
                    caption=f"📄 <b>{escape_html(af.title or af.filename)}</b>",
                    reply_to_message_id=reply_to_id,
                )

            # Send auto-exported document if generated
            if auto_export_content:
                await self._safe_send_chat_action(chat_id, "upload_document")
                await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=auto_export_content,
                    filename=auto_export_name,
                    caption=f"📄 <b>{escape_html(auto_export_title)}</b>",
                    reply_to_message_id=reply_to_id,
                )

        except asyncio.CancelledError:
            logger.info("Prompt task cancelled for chat %s", chat_id)
            if msg_id:
                await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🛑 <i>Task stopped.</i>",
                )
            raise
        except Exception as e:
            logger.error("Error executing prompt for chat %s: %s", chat_id, e, exc_info=True)
            err_text = f"❌ <b>Execution Error:</b>\n<code>{escape_html(str(e))}</code>"
            if msg_id:
                await self._safe_edit_message_text(chat_id=chat_id, message_id=msg_id, text=err_text)
            else:
                await self._safe_send_message(chat_id=chat_id, text=err_text, reply_to_message_id=reply_to_id)
        finally:
            if bridge and not bridge.is_running():
                self.active_bridges.pop(chat_id, None)
            self.session_manager.set_busy(chat_id, False)


if __name__ == "__main__":
    print("AgyTelegramBot initialized with modular architecture!")
