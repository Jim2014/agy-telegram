"""Telegram Bot orchestrator: handles long polling, message routing, streaming bridge, and task lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
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
    handle_cmd_busy as cmd_handle_busy,
    handle_cmd_delete as cmd_handle_delete,
    handle_cmd_dir as cmd_handle_dir,
    handle_cmd_effort as cmd_handle_effort,
    handle_cmd_export as cmd_handle_export,
    handle_cmd_file as cmd_handle_file,
    handle_cmd_help as cmd_handle_help,
    handle_cmd_html as cmd_handle_html,
    handle_cmd_logs as cmd_handle_logs,
    handle_cmd_model as cmd_handle_model,
    handle_cmd_new as cmd_handle_new,
    handle_cmd_open as cmd_handle_open,
    handle_cmd_permissions as cmd_handle_permissions,
    handle_cmd_projects as cmd_handle_projects,
    handle_cmd_reset as cmd_handle_reset,
    handle_cmd_schedule as cmd_handle_schedule,
    handle_cmd_skills as cmd_handle_skills,
    handle_cmd_status as cmd_handle_status,
    handle_cmd_steer as cmd_handle_steer,
    handle_cmd_stop as cmd_handle_stop,
    handle_cmd_usage as cmd_handle_usage,
    handle_cmd_voice as cmd_handle_voice,
)
from gateway.config import COMPACT_TURN_REMINDER, Config
from gateway.pairing import PairingManager
from gateway.scheduler import ScheduledTask, TaskScheduler, is_quiet_heartbeat_reply
from gateway.ipc_router import (
    DEFAULT_MAX_HOPS,
    IPCCall,
    IPCResult,
    extract_ipc_calls,
    format_incoming_envelope,
    format_pinned_accordion_card,
    format_return_envelope,
    generate_span_id,
    generate_trace_id,
    log_ipc_event,
    log_trace_span,
    validate_ipc_call,
)
from gateway.media_handlers import (
    handle_document_message as mh_handle_document_message,
    handle_voice_message as mh_handle_voice_message,
)
from gateway.message_formatter import (
    AgyActionItem,
    AgyFileBlock,
    AgyVoiceBlock,
    distill_spoken_summary,
    escape_html,
    extract_agy_action_blocks,
    extract_agy_file_blocks,
    extract_agy_voice_block,
    format_final_result,
    format_pinned_banner,
    format_streaming_message,
    format_tool_summary,
    generate_standalone_html,
    render_agy_file,
    should_export_as_file,
    split_text_chunks,
)
from gateway.session_manager import ChatSession, SessionManager
from gateway.telegram_client import TelegramAPIError, TelegramClient, format_exception_brief
from gateway.ui_keyboards import (
    COMMAND_ALIASES,
    build_actions_keyboard,
    build_adaptive_bot_commands,
    build_decision_keyboard,
    build_ipc_accordion_keyboard,
    build_pinned_banner_keyboard,
    build_reply_keyboard,
    build_voice_keyboard,
    build_voice_play_button,
)
from gateway.voice_tts import synthesize_voice_ogg

logger = logging.getLogger(__name__)


DEFAULT_BOT_COMMANDS = build_adaptive_bot_commands(None)
MAX_QUEUED_PROMPTS = 10


class AgyTelegramBot:
    """Core bot runner managing Telegram communication, agy executions, and document exports."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = TelegramClient(bot_token=config.bot_token)
        self.session_manager = SessionManager(persistence_file=config.sessions_file)
        self.pairing_manager = PairingManager(pairing_file=getattr(config, "pairing_file", "runtime/pairing.json"))
        self.scheduler = TaskScheduler(schedules_file=getattr(config, "schedules_file", "runtime/schedules.json"))
        self.active_bridges: Dict[int, AgyBridge] = {}
        self.active_progress_messages: Dict[int, int] = {}
        self.active_reply_to_ids: Dict[int, Optional[int]] = {}
        self._active_prompt_count: int = 0
        self._runtime_dir: Path = Path(config.sessions_file).parent if config.sessions_file else Path("runtime")
        self._turn_lock_file: Path = self._runtime_dir / "turn_active.lock"
        self._restart_flag_file: Path = self._runtime_dir / "restart.requested"
        self.running = False
        self.bot_info: Dict[str, Any] = {}
        self.started_at = time.time()
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._prompt_queues: Dict[int, List[Dict[str, Any]]] = {}

    def _get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        """Return per-chat asyncio lock serializing busy checks and prompt admission."""
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

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
            adaptive_cmds = build_adaptive_bot_commands(session)
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
            logger.warning("Telegram send_message failed for chat %s: %s", chat_id, format_exception_brief(e))
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
            logger.warning(
                "Telegram edit_message_text failed for chat %s, msg %s: %s",
                chat_id,
                message_id,
                format_exception_brief(e),
            )
            return None

    async def update_pinned_banner(
        self,
        chat_id: int,
        force_repin: bool = False,
        is_busy: bool = False,
    ) -> Optional[int]:
        """
        Update or create the pinned status banner message at the top of the chat.
        Always silent (disable_notification=True).
        """
        session = self.session_manager.get_session(chat_id)
        banner_text = format_pinned_banner(
            session=session,
            default_model=self.config.default_model,
            default_effort=self.config.default_effort,
            is_busy=is_busy,
        )
        banner_markup = build_pinned_banner_keyboard()

        # If already pinned and not forcing repin, try editing existing message
        if session.pinned_message_id and not force_repin:
            res = await self._safe_edit_message_text(
                chat_id=chat_id,
                message_id=session.pinned_message_id,
                text=banner_text,
                reply_markup=banner_markup,
            )
            if res is not None:
                return session.pinned_message_id

        # Send new banner message and pin it
        new_msg = await self._safe_send_message(
            chat_id=chat_id,
            text=banner_text,
            reply_markup=banner_markup,
        )
        if new_msg and "message_id" in new_msg:
            msg_id = new_msg["message_id"]
            session.pinned_message_id = msg_id
            self.session_manager.save()
            try:
                await self.client.pin_chat_message(chat_id=chat_id, message_id=msg_id, disable_notification=True)
            except Exception as e:
                logger.debug("Failed to pin banner in chat %s: %s", chat_id, e)
            return msg_id

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
            logger.warning("Telegram send_document failed for chat %s: %s", chat_id, format_exception_brief(e))
            return None

    async def _safe_send_voice(
        self,
        chat_id: int,
        voice: bytes | str | Path,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Send native voice note safely, catching all Telegram / network errors."""
        try:
            return await self.client.send_voice(
                chat_id=chat_id,
                voice=voice,
                **kwargs,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram send_voice failed for chat %s: %s", chat_id, format_exception_brief(e))
            return None

    async def _generate_and_send_voice(
        self,
        chat_id: int,
        text: str,
        lang: str = "zh",
        voice_id: Optional[str] = None,
        reply_to_id: Optional[int] = None,
    ) -> bool:
        """Synthesize text into speech and send native Telegram voice note with cache check."""
        try:
            session = self.session_manager.get_session(chat_id)
            cached_path = session.pending_voice_data.get(voice_id, {}).get("audio_path") if voice_id else None

            if cached_path and os.path.isfile(cached_path):
                voice_path = Path(cached_path)
            else:
                await self._safe_send_chat_action(chat_id, "record_voice")
                voice_path = await synthesize_voice_ogg(text=text, lang=lang, voice_id=voice_id)
                if voice_path and voice_id:
                    self.session_manager.update_voice_audio_path(chat_id, voice_id, str(voice_path))

            if voice_path and voice_path.is_file():
                res = await self._safe_send_voice(
                    chat_id=chat_id,
                    voice=voice_path,
                    reply_to_message_id=reply_to_id,
                )
                return bool(res)
        except Exception as e:
            logger.warning("Failed to generate and send voice note for chat %s: %s", chat_id, format_exception_brief(e))
        return False

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
            logger.warning("Telegram send_chat_action failed for chat %s: %s", chat_id, format_exception_brief(e))
            return False

    async def _safe_set_message_reaction(
        self,
        chat_id: int,
        message_id: Optional[int],
        reaction: str = "👀",
    ) -> bool:
        """Add emoji reaction safely, catching all Telegram / network errors."""
        if not message_id:
            return False
        try:
            return await self.client.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(
                "Telegram set_message_reaction failed for chat %s, msg %s: %s",
                chat_id,
                message_id,
                format_exception_brief(e),
            )
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
            logger.warning(
                "Telegram answer_callback_query failed for query %s: %s",
                callback_query_id,
                format_exception_brief(e),
            )
            return False

    async def _notify_restart(self) -> None:
        """Send a short 'gateway (re)started' notice to every known chat with a session."""
        started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at))
        sessions = getattr(self.session_manager, "_sessions", {})
        for chat_id, session in sessions.items():
            session.pinned_ipc_message_ids.clear()
        self.session_manager.save()

        for chat_id, session in sessions.items():
            if not self.is_chat_authorized(chat_id):
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
                await self.update_pinned_banner(chat_id)
                await self.update_chat_command_menu(chat_id)
            except Exception as e:
                logger.warning("Restart notification failed for chat %s: %s", chat_id, format_exception_brief(e))

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
        self._clear_stale_turn_markers()
        print("🚀 agy-telegram gateway is running! Press Ctrl+C to stop.\n")

        await self._notify_restart()

        if getattr(self.config, "scheduler_enabled", True):
            self.scheduler.start(self._execute_scheduled_task)

        offset: Optional[int] = None
        consecutive_errors = 0

        try:
            while self.running:
                try:
                    updates = await self.client.get_updates(offset=offset, timeout=30, raise_on_error=True)
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
                except TelegramAPIError as e:
                    consecutive_errors += 1
                    if e.error_code == 409:
                        wait_time = min(5 * consecutive_errors, 60)
                        logger.warning(
                            "Telegram 409 Conflict (another polling instance active? attempt %d): %s. Backing off %ds...",
                            consecutive_errors,
                            e,
                            wait_time,
                        )
                    else:
                        wait_time = min(2 ** consecutive_errors, 30)
                        logger.warning("Telegram API error fetching updates (attempt %d): %s. Retrying in %ds...", consecutive_errors, e, wait_time)
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    consecutive_errors += 1
                    wait_time = min(2 ** consecutive_errors, 30)
                    logger.warning("Error fetching Telegram updates (attempt %d): %s. Retrying in %ds...", consecutive_errors, e, wait_time)
                    await asyncio.sleep(wait_time)
        finally:
            await self.scheduler.stop()

    async def stop(self) -> None:
        """Gracefully stop the bot runner and interrupt active agy processes."""
        self.running = False
        await self.scheduler.stop()
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

    def _clear_stale_turn_markers(self) -> None:
        """Remove stale turn/restart lock files on startup."""
        for p in (self._turn_lock_file, self._restart_flag_file):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def _spawn_detached_restart(self) -> None:
        """Launch restart.sh --do-restart in a detached session after final response delivery."""
        restart_script = Path(__file__).resolve().parent.parent / "restart.sh"
        if not restart_script.is_file():
            logger.warning("Cannot execute deferred restart: %s not found.", restart_script)
            return
        logger.info("Executing deferred gateway restart now that final report has been delivered.")
        subprocess.Popen(
            [str(restart_script), "--do-restart"],
            cwd=str(restart_script.parent),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def is_chat_authorized(self, chat_id: int) -> bool:
        """Check if chat_id is in config allowlist or approved via DM pairing."""
        if self.config.is_chat_allowed(chat_id):
            return True
        if getattr(self.config, "dm_pairing_enabled", True) and self.pairing_manager.is_approved(chat_id):
            return True
        return False

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

            # Check authorization whitelist / DM pairing (deny-by-default)
            if not self.is_chat_authorized(chat_id):
                logger.warning("Unauthorized access attempt from chat_id %d", chat_id)
                if getattr(self.config, "dm_pairing_enabled", True):
                    sender = message.get("from") or {}
                    uname = sender.get("username") or message.get("chat", {}).get("username") or ""
                    fname = sender.get("first_name") or message.get("chat", {}).get("first_name") or ""
                    code, is_new = self.pairing_manager.create_or_get_pending(
                        chat_id, username=uname, first_name=fname
                    )
                    if is_new and self.config.allowed_chat_ids:
                        who = f"{escape_html(fname)} (@{escape_html(uname)})" if uname else (escape_html(fname) or "Telegram User")
                        owner_kb = {
                            "inline_keyboard": [
                                [
                                    {"text": "✅ Approve", "callback_data": f"pair:approve:{code}"},
                                    {"text": "❌ Reject", "callback_data": f"pair:reject:{code}"},
                                ]
                            ]
                        }
                        for owner_id in sorted(self.config.allowed_chat_ids):
                            await self._safe_send_message(
                                chat_id=owner_id,
                                text=(
                                    f"🔐 <b>New DM Pairing Request</b>\n"
                                    f"• <b>User:</b> {who}\n"
                                    f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
                                    f"• <b>Code:</b> <code>{code}</code>\n\n"
                                    f"Tap a button below or run:\n"
                                    f"<code>python -m gateway.pairing approve {code}</code>"
                                ),
                                reply_markup=owner_kb,
                            )
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=(
                            "⛔ <b>Access Denied</b> — Pairing required.\n\n"
                            f"• <b>Your Chat ID:</b> <code>{chat_id}</code>\n"
                            f"• <b>Pairing Code:</b> <code>{code}</code>\n\n"
                            f"Ask the owner to approve your request with:\n"
                            f"<code>python -m gateway.pairing approve {code}</code>"
                        ),
                    )
                else:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text="⛔ <b>Access Denied</b>\nThis bot is private and your chat ID is not authorized.",
                    )
                return

            # Voice or audio message handling
            if message.get("voice") or message.get("audio"):
                await self.handle_voice_message(chat_id, message, message_id)
                return

            # Document, photo, video, animation, or video_note file handling
            if (
                message.get("document")
                or message.get("photo")
                or message.get("video")
                or message.get("animation")
                or message.get("video_note")
            ):
                await self.handle_document_message(chat_id, message, message_id)
                return

            text = message.get("text", "").strip()
            if not text:
                return

            # Slash command routing
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                cmd = parts[0]
                arg = parts[1].strip() if len(parts) > 1 else ""
                await self.dispatch_slash_command(chat_id, cmd, arg, message_id)
                return

            # Regular prompt -> atomic busy/steering admission under per-chat lock
            await self.handle_prompt(chat_id, text, message_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error processing update: %s", e, exc_info=True)

    async def dispatch_slash_command(
        self,
        chat_id: int,
        cmd: str,
        arg: str = "",
        message_id: Optional[int] = None,
    ) -> None:
        """Route and execute slash command with usage tracking."""
        clean_cmd = cmd.lower().split("@")[0].lstrip("/")
        canonical_cmd = COMMAND_ALIASES.get(clean_cmd, clean_cmd)
        self.session_manager.record_command_usage(chat_id, canonical_cmd)
        asyncio.create_task(self.update_chat_command_menu(chat_id))

        if canonical_cmd == "help":
            await self.handle_cmd_help(chat_id)
        elif canonical_cmd == "usage":
            await self.handle_cmd_usage(chat_id, reply_to_id=message_id)
        elif canonical_cmd == "skills":
            await self.handle_cmd_skills(chat_id, reply_to_id=message_id)
        elif canonical_cmd == "artifacts":
            await self.handle_cmd_artifacts(chat_id, arg, reply_to_id=message_id)
        elif canonical_cmd == "new":
            await self.handle_cmd_new(chat_id, arg)
        elif canonical_cmd == "open":
            await self.handle_cmd_open(chat_id, arg)
        elif canonical_cmd == "projects":
            await self.handle_cmd_projects(chat_id)
        elif canonical_cmd == "delete":
            await self.handle_cmd_delete(chat_id, arg)
        elif canonical_cmd == "reset":
            await self.handle_cmd_reset(chat_id)
        elif canonical_cmd == "status":
            await self.handle_cmd_status(chat_id)
        elif canonical_cmd == "model":
            await self.handle_cmd_model(chat_id, arg)
        elif canonical_cmd == "effort":
            await self.handle_cmd_effort(chat_id, arg)
        elif canonical_cmd == "permissions":
            await self.handle_cmd_permissions(chat_id, arg)
        elif canonical_cmd == "dir":
            await self.handle_cmd_dir(chat_id, arg, message_id)
        elif canonical_cmd == "file":
            await self.handle_cmd_file(chat_id, arg, message_id)
        elif canonical_cmd == "export":
            await self.handle_cmd_export(chat_id, message_id)
        elif canonical_cmd == "steer":
            await self.handle_cmd_steer(chat_id, arg, message_id)
        elif canonical_cmd == "pin":
            await self.update_pinned_banner(chat_id, force_repin=True)
            await self._safe_send_message(
                chat_id=chat_id,
                text="📌 <b>Pinned workspace banner updated and pinned to top of chat.</b>",
                reply_to_message_id=message_id,
            )
        elif canonical_cmd == "voice":
            await self.handle_cmd_voice(chat_id, arg, message_id=message_id)
        elif canonical_cmd == "html":
            await self.handle_cmd_html(chat_id, arg, message_id=message_id)
        elif canonical_cmd == "busy":
            await self.handle_cmd_busy(chat_id, arg, message_id=message_id)
        elif canonical_cmd == "schedule":
            default_kind = "heartbeat" if clean_cmd == "heartbeat" else "cron"
            await self.handle_cmd_schedule(chat_id, arg, default_kind=default_kind)
        elif canonical_cmd == "logs":
            default_view = "traces" if clean_cmd in ("traces", "trace") else "errors"
            await self.handle_cmd_logs(chat_id, arg, default_view=default_view)
        elif canonical_cmd == "stop":
            await self.handle_cmd_stop(chat_id)
        else:
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"❓ Unknown command <code>/{escape_html(clean_cmd)}</code>. Use /help for instructions.",
            )

    # Backward-compatible command and media delegation wrappers
    async def handle_cmd_voice(self, chat_id: int, arg: str = "", message_id: Optional[int] = None, edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_voice(self, chat_id, arg, edit_message_id=edit_message_id)

    async def handle_cmd_html(self, chat_id: int, arg: str = "", message_id: Optional[int] = None, edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_html(self, chat_id, arg, edit_message_id=edit_message_id)

    async def handle_cmd_busy(self, chat_id: int, arg: str = "", message_id: Optional[int] = None, edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_busy(self, chat_id, arg, edit_message_id=edit_message_id)

    async def handle_cmd_schedule(
        self,
        chat_id: int,
        arg: str = "",
        edit_message_id: Optional[int] = None,
        default_kind: str = "cron",
    ) -> None:
        await cmd_handle_schedule(self, chat_id, arg, edit_message_id=edit_message_id, default_kind=default_kind)

    async def handle_cmd_logs(
        self,
        chat_id: int,
        arg: str = "",
        edit_message_id: Optional[int] = None,
        default_view: str = "errors",
    ) -> None:
        await cmd_handle_logs(self, chat_id, arg, edit_message_id=edit_message_id, default_view=default_view)

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

    async def handle_cmd_permissions(self, chat_id: int, arg: str = "", edit_message_id: Optional[int] = None) -> None:
        await cmd_handle_permissions(self, chat_id, arg, edit_message_id)

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

    async def _handle_ipc_calls(
        self,
        chat_id: int,
        ipc_calls: List[IPCCall],
        active_proj: str,
        bridge: AgyBridge,
        effective_model: Optional[str],
        effective_effort: Optional[str],
        effective_permission_preset: Optional[str] = None,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        visited_projects: Optional[Tuple[str, ...]] = None,
    ) -> Tuple[str, List[IPCResult]]:
        """Execute background cross-project IPC calls with cycle/hop guardrails, traces, and pinned cards."""
        session = self.session_manager.get_session(chat_id)
        results: List[IPCResult] = []
        return_envelopes: List[str] = []
        eff_trace_id = trace_id or generate_trace_id()
        base_visited = tuple(visited_projects) if visited_projects else (active_proj,)
        max_hops = getattr(self.config, "ipc_max_hops", DEFAULT_MAX_HOPS)
        trace_path = Path(getattr(self.config, "traces_file", "runtime/traces.jsonl"))

        for call in ipc_calls:
            target = call.target_project
            child_span_id = generate_span_id()
            effective_hop = max(int(call.hop_count or 1), len(base_visited))
            target_perm = session.get_permission_preset(
                project_name=target,
                default=getattr(self.config, "default_permission_preset", "auto"),
            )
            norm_call = IPCCall(
                target_project=target,
                content=call.content,
                source_project=call.source_project,
                hop_count=effective_hop,
                raw_block=call.raw_block,
                trace_id=call.trace_id,
                visited_projects=call.visited_projects,
            )

            violation = validate_ipc_call(norm_call, max_hops=max_hops, visited_projects=base_visited)
            if violation:
                code, err_msg = violation
                logger.warning("IPC call blocked (%s) [trace=%s]: %s", code, eff_trace_id[:8], err_msg)
                log_ipc_event(
                    active_proj,
                    target,
                    "BLOCKED",
                    f"{code}: {err_msg} | hop={effective_hop}",
                    trace_id=eff_trace_id,
                )
                log_trace_span(
                    trace_id=eff_trace_id,
                    span_id=child_span_id,
                    parent_span_id=parent_span_id,
                    operation="ipc_call",
                    status=code,
                    duration_ms=0.0,
                    source_project=active_proj,
                    target_project=target,
                    hop=effective_hop,
                    model=effective_model or "",
                    permission_preset=target_perm,
                    error=err_msg,
                    trace_file=trace_path,
                )
                results.append(
                    IPCResult(
                        target_project=target,
                        source_project=active_proj,
                        status="ERROR",
                        response_content="",
                        error_message=err_msg,
                        trace_id=eff_trace_id,
                        span_id=child_span_id,
                        hop_count=effective_hop,
                        blocked_reason=code,
                    )
                )
                return_envelopes.append(
                    format_return_envelope(
                        source=target,
                        target=active_proj,
                        body=f"Error ({code}): {err_msg}",
                        status="ERROR",
                        trace_id=eff_trace_id,
                    )
                )
                continue

            if target not in session.projects:
                avail = ", ".join(session.projects.keys()) or "none"
                err_msg = f"Target project '{target}' is not registered in session. Available projects: {avail}"
                logger.warning("IPC call failed [trace=%s]: %s", eff_trace_id[:8], err_msg)
                log_trace_span(
                    trace_id=eff_trace_id,
                    span_id=child_span_id,
                    parent_span_id=parent_span_id,
                    operation="ipc_call",
                    status="UNREGISTERED_PROJECT",
                    duration_ms=0.0,
                    source_project=active_proj,
                    target_project=target,
                    hop=effective_hop,
                    model=effective_model or "",
                    permission_preset=target_perm,
                    error=err_msg,
                    trace_file=trace_path,
                )
                results.append(
                    IPCResult(
                        target_project=target,
                        source_project=active_proj,
                        status="ERROR",
                        response_content="",
                        error_message=err_msg,
                        trace_id=eff_trace_id,
                        span_id=child_span_id,
                        hop_count=effective_hop,
                        blocked_reason="UNREGISTERED_PROJECT",
                    )
                )
                return_envelopes.append(
                    format_return_envelope(
                        source=target,
                        target=active_proj,
                        body=f"Error: {err_msg}",
                        status="ERROR",
                        trace_id=eff_trace_id,
                    )
                )
                continue

            target_proj_info = session.projects[target]
            target_path = target_proj_info.get("path") or self.config.agy_working_dir or os.getcwd()

            # 1. Unpin old card if present, send fresh card with inline unpin button and pin it
            log_ipc_event(
                active_proj,
                target,
                "DISPATCH",
                f"Task: {call.content[:100]} | hop={effective_hop}",
                trace_id=eff_trace_id,
            )
            card_text = format_pinned_accordion_card(
                target_project=target,
                source_project=active_proj,
                task_preview=call.content,
                tools_log=[],
                status_text="Starting background worker in workspace...",
                trace_id=eff_trace_id,
            )
            old_pin_id = session.pinned_ipc_message_ids.get(target)
            if old_pin_id:
                try:
                    await self.client.unpin_chat_message(chat_id=chat_id, message_id=old_pin_id)
                except Exception:
                    pass

            card_markup = build_ipc_accordion_keyboard(target)
            pin_msg = await self._safe_send_message(
                chat_id=chat_id,
                text=card_text,
                reply_markup=card_markup,
            )
            pin_msg_id = pin_msg.get("message_id") if pin_msg else None
            session.pinned_ipc_message_ids[target] = pin_msg_id
            self.session_manager.save()
            if pin_msg_id:
                try:
                    await self.client.pin_chat_message(chat_id=chat_id, message_id=pin_msg_id, disable_notification=True)
                except Exception:
                    pass

            # 2. Run ephemeral worker in target workspace with clean isolated conversation
            next_visited = base_visited + (target,)
            envelope_prompt = format_incoming_envelope(
                source=active_proj,
                target=target,
                body=call.content,
                hop_count=effective_hop,
                trace_id=eff_trace_id,
                visited_projects=next_visited,
            )

            tools_log: List[str] = []
            target_accumulated_text = ""
            target_start = time.time()
            last_card_edit = time.time()
            is_error = False
            error_reason = ""

            try:
                async for event in bridge.run_ephemeral_worker(
                    prompt=envelope_prompt,
                    working_dir=target_path,
                    conversation_id=None,
                    model=effective_model,
                    effort=effective_effort,
                    timeout_seconds=self.config.agy_timeout_seconds,
                    permission_preset=target_perm,
                ):
                    now = time.time()
                    if event.type == "init":
                        if event.conversation_id:
                            session.projects[target]["last_ipc_conversation_id"] = event.conversation_id
                            self.session_manager.save()
                            log_ipc_event(target, active_proj, "INIT", f"Ephemeral conv_id={event.conversation_id}", trace_id=eff_trace_id)

                    elif event.type == "tool":
                        t_sum = format_tool_summary(
                            tool_name=event.tool_name or "",
                            state=event.tool_state or "ACTIVE",
                            tool_info=event.tool_info or {},
                            duration_seconds=event.duration_seconds,
                        )
                        if t_sum:
                            if tools_log and tools_log[-1].endswith("..."):
                                tools_log[-1] = t_sum
                            else:
                                tools_log.append(t_sum)
                        log_ipc_event(target, active_proj, "TOOL", f"{event.tool_name} [{event.tool_state}]", trace_id=eff_trace_id)

                    elif event.type in ("text", "text_delta"):
                        target_accumulated_text += (event.text_delta or getattr(event, "content", "") or "")

                    elif event.type in ("result", "done"):
                        if event.response:
                            target_accumulated_text = event.response
                        if event.status and event.status.upper() in ("ERROR", "FAILED", "FAILURE"):
                            is_error = True
                            error_reason = event.response or "Worker failure"
                        break

                    elif event.type == "error":
                        is_error = True
                        error_reason = event.response or "Worker error encountered"
                        break

                    # Throttle live card edits
                    if pin_msg_id and (now - last_card_edit >= self.config.stream_throttle_seconds):
                        prog_card = format_pinned_accordion_card(
                            target_project=target,
                            source_project=active_proj,
                            task_preview=call.content,
                            tools_log=tools_log,
                            status_text="Executing tools in workspace...",
                            duration_seconds=now - target_start,
                            trace_id=eff_trace_id,
                        )
                        await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=pin_msg_id,
                            text=prog_card,
                            reply_markup=card_markup,
                        )
                        last_card_edit = now

            except Exception as e:
                is_error = True
                error_reason = str(e)
                logger.error("Error running IPC task for %s: %s", target, e, exc_info=True)

            dur = time.time() - target_start
            clean_target_text, _ = extract_agy_voice_block(target_accumulated_text)
            clean_target_text, _ = extract_agy_action_blocks(clean_target_text)
            clean_target_text, sub_calls = extract_ipc_calls(
                clean_target_text,
                default_source=target,
                default_trace_id=eff_trace_id,
                default_visited=next_visited,
            )
            reply_calls = [c for c in sub_calls if c.target_project == active_proj]
            forwarded_calls = [c for c in sub_calls if c.target_project != active_proj]
            final_body = reply_calls[0].content if reply_calls else clean_target_text
            if forwarded_calls and not is_error:
                for fc in forwarded_calls:
                    fc.hop_count = max(int(fc.hop_count or 1), len(next_visited))
                nested_ret, _ = await self._handle_ipc_calls(
                    chat_id=chat_id,
                    ipc_calls=forwarded_calls,
                    active_proj=target,
                    bridge=bridge,
                    effective_model=effective_model,
                    effective_effort=effective_effort,
                    trace_id=eff_trace_id,
                    parent_span_id=child_span_id,
                    visited_projects=next_visited,
                )
                if nested_ret:
                    final_body = f"{final_body}\n\n{nested_ret}".strip() if final_body else nested_ret

            log_ipc_event(
                target,
                active_proj,
                "COMPLETE" if not is_error else "FAILED",
                f"Status: {'SUCCESS' if not is_error else 'ERROR'} | Duration: {dur:.2f}s | Summary: {final_body[:120]}",
                trace_id=eff_trace_id,
            )
            log_trace_span(
                trace_id=eff_trace_id,
                span_id=child_span_id,
                parent_span_id=parent_span_id,
                operation="ipc_call",
                status="ERROR" if is_error else "SUCCESS",
                duration_ms=round(dur * 1000, 1),
                source_project=active_proj,
                target_project=target,
                hop=effective_hop,
                model=effective_model or "",
                permission_preset=target_perm,
                error=error_reason if is_error else "",
                trace_file=trace_path,
            )

            # 3. Update pinned card to final state (Completed 🟢 or Error 🔴)
            final_card = format_pinned_accordion_card(
                target_project=target,
                source_project=active_proj,
                task_preview=call.content,
                tools_log=tools_log,
                status_text=error_reason if is_error else "Completed",
                is_done=not is_error,
                is_error=is_error,
                duration_seconds=dur,
                final_reply=final_body,
                trace_id=eff_trace_id,
            )
            if pin_msg_id:
                await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=pin_msg_id,
                    text=final_card,
                    reply_markup=card_markup,
                )

            res = IPCResult(
                target_project=target,
                source_project=active_proj,
                status="ERROR" if is_error else "SUCCESS",
                response_content=final_body,
                duration_seconds=dur,
                error_message=error_reason if is_error else "",
                tools_executed=tools_log,
                trace_id=eff_trace_id,
                span_id=child_span_id,
                hop_count=effective_hop,
            )
            results.append(res)

            return_envelopes.append(
                format_return_envelope(
                    source=target,
                    target=active_proj,
                    body=final_body if not is_error else f"Error in {target}: {error_reason}\nLogs:\n" + "\n".join(tools_log),
                    status="ERROR" if is_error else "SUCCESS",
                    trace_id=eff_trace_id,
                )
            )

        combined_return_text = "\n\n".join(return_envelopes)
        return combined_return_text, results

    async def perform_steering(
        self,
        chat_id: int,
        steering_text: str,
        reply_to_id: Optional[int],
        is_voice: bool = False,
        duration: Optional[int] = None,
    ) -> bool:
        """Inject steering guidance into active session, rotate progress messages, and return True if successful."""
        bridge = self.active_bridges.get(chat_id)
        if not bridge:
            return False

        is_running_fn = getattr(bridge, "is_running", None)
        if callable(is_running_fn):
            res = is_running_fn()
            if asyncio.iscoroutine(res):
                res = await res
            if not res:
                return False

        steered = await bridge.steer(steering_text)
        if not steered:
            return False

        # 1. Finalize the old progress message so it stops updating
        old_msg_id = self.active_progress_messages.get(chat_id)
        if old_msg_id:
            summary_snippet = steering_text.replace("\n", " ")[:100]
            if len(steering_text) > 100:
                summary_snippet += "..."
            await self._safe_edit_message_text(
                chat_id=chat_id,
                message_id=old_msg_id,
                text=(
                    f"🔄 <b>Task Steered:</b>\n"
                    f"<i>\"{escape_html(summary_snippet)}\"</i>\n\n"
                    f"↳ <i>Progress continues in new reply below.</i>"
                ),
            )

        # 2. Add reaction to the steering message
        if reply_to_id:
            await self._safe_set_message_reaction(chat_id, reply_to_id, "👀")

        # 3. Create a new progress message replying to the steering message
        dur_text = f" ({duration}s)" if duration else ""
        prefix = (
            "🎯 <b>Steered into active session (Steering instruction sent):</b>"
            if not is_voice
            else f"🎙️ <b>Steered voice instruction{dur_text} into active session:</b>"
        )
        new_progress = await self._safe_send_message(
            chat_id=chat_id,
            text=(
                f"{prefix}\n"
                f"<i>\"{escape_html(steering_text)}\"</i>\n\n"
                f"⏳ <i>Incorporating guidance into ongoing session...</i>"
            ),
            reply_to_message_id=reply_to_id,
        )
        if new_progress and new_progress.get("message_id"):
            self.active_progress_messages[chat_id] = new_progress["message_id"]
        if reply_to_id:
            self.active_reply_to_ids[chat_id] = reply_to_id

        return True

    async def handle_prompt(
        self,
        chat_id: int,
        prompt: str,
        reply_to_id: Optional[int],
        is_voice_input: bool = False,
    ) -> None:
        """Execute agy with prompt and stream real-time results back to Telegram with document export support."""
        async with self._get_chat_lock(chat_id):
            session = self.session_manager.get_session(chat_id)

            if session.is_busy:
                effective_busy_mode = self.session_manager.normalize_busy_mode(
                    getattr(session, "busy_mode", None) or getattr(self.config, "busy_mode", "steer")
                )
                if effective_busy_mode == "queue":
                    q = self._prompt_queues.setdefault(chat_id, [])
                    if len(q) >= MAX_QUEUED_PROMPTS:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Prompt queue is full (max {MAX_QUEUED_PROMPTS}). Use <code>/busy clear</code> or <code>/stop</code>.",
                            reply_to_message_id=reply_to_id,
                        )
                        return
                    q.append({
                        "prompt": prompt,
                        "reply_to_id": reply_to_id,
                        "is_voice_input": is_voice_input,
                    })
                    if reply_to_id:
                        await self._safe_set_message_reaction(chat_id, reply_to_id, "👀")
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=(
                            f"📥 <b>Queued (#{len(q)})</b> — will run automatically once the current turn finishes.\n"
                            "<i>Use <code>/steer &lt;text&gt;</code> to inject mid-flight, or <code>/busy clear</code> to cancel queued items.</i>"
                        ),
                        reply_to_message_id=reply_to_id,
                    )
                    return

                bridge = self.active_bridges.get(chat_id)
                if bridge and bridge.is_running():
                    steered = await self.perform_steering(chat_id, prompt, reply_to_id, is_voice=is_voice_input)
                    if steered:
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
            self._active_prompt_count += 1

        try:
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            self._turn_lock_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
        turn_post_tasks: List[asyncio.Task] = []
        session.pending_decisions = []
        session.pending_actions = []
        asyncio.create_task(self.update_pinned_banner(chat_id, is_busy=True))

        if reply_to_id:
            await self._safe_set_message_reaction(chat_id, reply_to_id, "👀")

        init_msg = await self._safe_send_message(
            chat_id=chat_id,
            text="⏳ <i>Contacting agy...</i>",
            reply_to_message_id=reply_to_id,
        )
        msg_id = init_msg.get("message_id") if init_msg else None
        if msg_id:
            self.active_progress_messages[chat_id] = msg_id
        if reply_to_id:
            self.active_reply_to_ids[chat_id] = reply_to_id

        active_proj = session.active_project or "default"
        effective_model = session.model or self.config.default_model or None
        effective_effort = session.effort or self.config.default_effort or None
        effective_permission_preset = session.get_permission_preset(
            project_name=active_proj,
            default=getattr(self.config, "default_permission_preset", "auto"),
        )
        effective_dir = session.active_path() or self.config.agy_working_dir or None
        followup_prompt = (
            COMPACT_TURN_REMINDER
            if (self.config.system_prompt and self.config.system_prompt.strip())
            else None
        )

        bridge = self.active_bridges.get(chat_id)
        if bridge is None:
            bridge = AgyBridge(
                agy_bin=self.config.get_resolved_agy_bin(),
                default_working_dir=effective_dir,
                timeout_seconds=self.config.agy_timeout_seconds,
                system_prompt=self.config.system_prompt,
                followup_system_prompt=followup_prompt,
            )
            self.active_bridges[chat_id] = bridge
        else:
            bridge.followup_system_prompt = followup_prompt

        tool_events: List[str] = []
        created_files: List[Path] = []
        accumulated_text = ""
        start_time = time.time()
        turn_trace_id = generate_trace_id()
        turn_span_id = generate_span_id()
        turn_status = "SUCCESS"
        turn_error = ""
        turn_trace_logged = False
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
                permission_preset=effective_permission_preset,
            ):
                now = time.time()

                if event.type == "init":
                    if event.conversation_id:
                        conv_id = event.conversation_id
                        self.session_manager.set_conversation_id(chat_id, conv_id)

                elif event.type == "tool":
                    if reply_to_id:
                        await self._safe_set_message_reaction(chat_id, reply_to_id, "⚡")

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

                    if event.tool_name == "ask_question":
                        params = event.tool_info.get("parameters", {}) if isinstance(event.tool_info, dict) else {}
                        qs = params.get("questions", [])
                        if qs and isinstance(qs, list) and isinstance(qs[0], dict):
                            opts = qs[0].get("options", [])
                            if opts:
                                session.pending_decisions = [str(o) for o in opts]

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

                elif event.type == "turn_complete":
                    # Intermediate completion of a turn when follow-up steered turns are queued
                    if event.response:
                        accumulated_text = event.response
                    elif getattr(event, "content", None):
                        accumulated_text = event.content
                    continue

                elif event.type in ("result", "done"):
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
                    cur_msg_id = self.active_progress_messages.get(chat_id, msg_id)
                    if streaming_text != last_edited_content and cur_msg_id:
                        await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=cur_msg_id,
                            text=streaming_text,
                        )
                        last_edited_content = streaming_text
                        last_edit_time = now

            turn_duration = time.time() - start_time
            if not total_duration:
                total_duration = turn_duration

            if final_event is not None:
                ev_status = str(getattr(final_event, "status", "") or "").upper()
                if final_event.type == "error" or ev_status in ("ERROR", "FAILED", "FAILURE", "TIMEOUT"):
                    turn_status = ev_status or "ERROR"
                    turn_error = (getattr(final_event, "response", "") or "")[:500]

            metrics_data = {
                "total_tokens": total_tokens,
                "thinking_tokens": thinking_tokens,
                "duration_seconds": turn_duration,
            }

            self.session_manager.record_turn_stats(
                chat_id=chat_id,
                turn_tokens=total_tokens,
                turn_thinking=thinking_tokens,
                turn_duration=turn_duration,
            )

            # 1. First extract [AGY_VOICE] spoken summary from final output so it NEVER leaks into HTML files or visible text
            final_raw = accumulated_text or (getattr(final_event, "response", "") or getattr(final_event, "content", "") or "" if final_event else "")
            final_raw, voice_block = extract_agy_voice_block(final_raw)

            # 2. Check for [AGY_FILE] protocol blocks in final output
            clean_text, agy_files = extract_agy_file_blocks(final_raw)

            # 2.5 Process cross-project [AGY_IPC] communication calls
            clean_text, ipc_calls = extract_ipc_calls(
                clean_text,
                default_source=active_proj,
                default_trace_id=turn_trace_id,
                default_visited=(active_proj,),
            )
            if ipc_calls:
                combined_return_text, ipc_results = await self._handle_ipc_calls(
                    chat_id=chat_id,
                    ipc_calls=ipc_calls,
                    active_proj=active_proj,
                    bridge=bridge,
                    effective_model=effective_model,
                    effective_effort=effective_effort,
                    effective_permission_preset=effective_permission_preset,
                    trace_id=turn_trace_id,
                    parent_span_id=turn_span_id,
                    visited_projects=(active_proj,),
                )
                if combined_return_text:
                    followup_text = ""
                    async for f_event in bridge.run_streaming(
                        prompt=combined_return_text,
                        conversation_id=conv_id,
                        model=effective_model,
                        effort=effective_effort,
                        working_dir=effective_dir,
                        system_prompt=self.config.system_prompt,
                        permission_preset=effective_permission_preset,
                    ):
                        if f_event.type in ("text", "text_delta"):
                            followup_text += (f_event.text_delta or getattr(f_event, "content", "") or "")
                        elif f_event.type in ("result", "done"):
                            if f_event.response:
                                followup_text = f_event.response
                            break

                    if followup_text:
                        clean_text = followup_text
                        clean_text, voice_block_followup = extract_agy_voice_block(clean_text)
                        if voice_block_followup:
                            voice_block = voice_block_followup
                        clean_text, follow_files = extract_agy_file_blocks(clean_text)
                        if follow_files:
                            agy_files.extend(follow_files)
                    elif ipc_results:
                        blocks = []
                        for r in ipc_results:
                            if r.status == "SUCCESS" and r.response_content:
                                blocks.append(f"<b>📬 [{escape_html(r.target_project)}] Collaboration Result:</b>\n{r.response_content}")
                            elif r.status == "ERROR":
                                blocks.append(f"<b>⚠️ [{escape_html(r.target_project)}] Execution Failed:</b>\n<i>{escape_html(r.error_message)}</i>")
                        if blocks:
                            joined_blocks = "\n\n".join(blocks)
                            clean_text = f"{clean_text}\n\n{joined_blocks}".strip() if clean_text else joined_blocks

            # 3. Auto-export long responses as standalone HTML document (respecting per-chat html_mode)
            effective_html_mode = self.session_manager.normalize_html_mode(
                getattr(session, "html_mode", None) or getattr(self.config, "html_mode", "auto")
            )
            auto_export_content: Optional[str] = None
            auto_export_name = ""
            auto_export_title = ""

            if effective_html_mode == "auto" and not agy_files and should_export_as_file(prompt, clean_text):
                is_diff = bool(re.search(r"\bdiff\b", prompt.lower())) or "```diff" in clean_text or "diff --git" in clean_text or "--- a/" in clean_text
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
                    f"📄 _Full {prefix} generated and attached below: `{auto_export_name}`_\n\n"
                    + clean_text
                )

            # Check explicit agy_files and collect render errors (skipped only if html_mode == 'off')
            export_errors: List[str] = []
            valid_exports: List[Tuple[AgyFileBlock, str]] = []
            if effective_html_mode != "off":
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

            if valid_exports:
                if len(valid_exports) == 1:
                    notice = "📄 _Full report generated and attached below._"
                else:
                    notice = f"📄 _{len(valid_exports)} report file(s) generated and attached below._"
                clean_text = f"{clean_text}\n\n{notice}".strip() if clean_text else notice

            if export_errors:
                err_block = "\n".join([f"⚠️ <i>{escape_html(e)}</i>" for e in export_errors])
                clean_text = f"{clean_text}\n\n{err_block}".strip() if clean_text else err_block

            # 4. Process voice summary (autosend or ondemand button)
            effective_tts_mode = getattr(session, "tts_mode", None) or getattr(self.config, "tts_mode", "ondemand")

            # Fallback voice block if LLM omitted [AGY_VOICE]
            if not voice_block and clean_text and getattr(self.config, "tts_enabled", True) and effective_tts_mode not in ("disable", "off"):
                has_exports = bool(valid_exports or auto_export_content)
                voice_block = distill_spoken_summary(clean_text, has_exports=has_exports, max_chars=1200)

            target_msg_id = self.active_progress_messages.get(chat_id, msg_id)
            target_reply_id = self.active_reply_to_ids.get(chat_id, reply_to_id)

            voice_btn: Optional[Dict[str, Any]] = None
            voice_id: Optional[str] = None
            if voice_block and getattr(self.config, "tts_enabled", True):
                voice_id = f"v_{chat_id}_{int(time.time())}_{len(session.pending_voice_data)}"
                self.session_manager.record_voice_summary(
                    chat_id=chat_id,
                    voice_id=voice_id,
                    text=voice_block.text,
                    lang=voice_block.lang,
                )
                # If triggered by voice input, ALWAYS autosend the voice response regardless of configuration
                if is_voice_input or effective_tts_mode in ("autosend", "autoplay", "direct", "auto"):
                    v_task = asyncio.create_task(
                        self._generate_and_send_voice(
                            chat_id=chat_id,
                            text=voice_block.text,
                            lang=voice_block.lang,
                            voice_id=voice_id,
                            reply_to_id=target_reply_id,
                        )
                    )
                    turn_post_tasks.append(v_task)
                elif effective_tts_mode in ("ondemand", "autogen", "script", "manual"):
                    voice_btn = build_voice_play_button(voice_id)

            # 5. Extract [AGY_ACTIONS] suggested next steps from clean_text
            clean_text, action_items = extract_agy_action_blocks(clean_text)
            actions_markup: Optional[Dict[str, Any]] = None
            if action_items:
                session.pending_actions = [{"label": a.label, "action": a.action} for a in action_items]
                actions_markup = build_actions_keyboard(action_items)
            elif session.pending_decisions:
                actions_markup = build_decision_keyboard(session.pending_decisions)

            # Prepend voice play button if available
            if voice_btn:
                if actions_markup is None:
                    actions_markup = {"inline_keyboard": []}
                actions_markup["inline_keyboard"].insert(0, [voice_btn])

            final_formatted = format_final_result(
                response_text=clean_text,
                duration_seconds=turn_duration,
                total_tokens=total_tokens,
                thinking_tokens=thinking_tokens,
                conversation_id=conv_id,
                project_name=active_proj,
            )

            chunks = split_text_chunks(final_formatted, chunk_size=4000)

            if chunks:
                if len(chunks) == 1:
                    if target_msg_id:
                        await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=target_msg_id,
                            text=chunks[0],
                            reply_markup=actions_markup,
                        )
                    else:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunks[0],
                            reply_markup=actions_markup,
                            reply_to_message_id=target_reply_id,
                        )
                else:
                    if target_msg_id:
                        await self._safe_edit_message_text(
                            chat_id=chat_id,
                            message_id=target_msg_id,
                            text=chunks[0],
                        )
                    else:
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunks[0],
                            reply_to_message_id=target_reply_id,
                        )

                    for idx, chunk in enumerate(chunks[1:], 1):
                        is_last = (idx == len(chunks) - 1)
                        await self._safe_send_message(
                            chat_id=chat_id,
                            text=chunk,
                            reply_markup=actions_markup if is_last else None,
                            reply_to_message_id=target_reply_id,
                        )
            else:
                fallback = f"✅ <b>Done</b> <i>({total_duration:.1f}s)</i>"
                if target_msg_id:
                    await self._safe_edit_message_text(
                        chat_id=chat_id,
                        message_id=target_msg_id,
                        text=fallback,
                        reply_markup=actions_markup,
                    )
                else:
                    await self._safe_send_message(
                        chat_id=chat_id,
                        text=fallback,
                        reply_markup=actions_markup,
                        reply_to_message_id=target_reply_id,
                    )

            if target_reply_id:
                await self._safe_set_message_reaction(chat_id, target_reply_id, "✅")

            # Helper to build document reply markup (Voice Audio button if available)
            def _build_doc_reply_markup() -> Optional[Dict[str, Any]]:
                if voice_btn:
                    return {"inline_keyboard": [[voice_btn]]}
                return None

            # Send explicit [AGY_FILE] protocol exported documents
            for af, html_data in valid_exports:
                await self._safe_send_chat_action(chat_id, "upload_document")
                doc_kb = _build_doc_reply_markup()
                await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=html_data,
                    filename=af.filename,
                    caption=f"📄 <b>{escape_html(af.title or af.filename)}</b>",
                    reply_to_message_id=target_reply_id,
                    reply_markup=doc_kb,
                )

            # Send auto-exported document if generated
            if auto_export_content:
                await self._safe_send_chat_action(chat_id, "upload_document")
                doc_kb = _build_doc_reply_markup()
                await self._safe_send_document(
                    chat_id=chat_id,
                    document_content=auto_export_content,
                    filename=auto_export_name,
                    caption=f"📄 <b>{escape_html(auto_export_title)}</b>",
                    reply_to_message_id=target_reply_id,
                    reply_markup=doc_kb,
                )

        except asyncio.CancelledError:
            turn_status = "CANCELLED"
            turn_error = "Task cancelled"
            logger.info("Prompt task cancelled for chat %s", chat_id)
            target_msg_id = self.active_progress_messages.get(chat_id, msg_id)
            target_reply_id = self.active_reply_to_ids.get(chat_id, reply_to_id)
            if target_msg_id:
                await self._safe_edit_message_text(
                    chat_id=chat_id,
                    message_id=target_msg_id,
                    text="🛑 <i>Task stopped.</i>",
                )
            if target_reply_id:
                await self._safe_set_message_reaction(chat_id, target_reply_id, "⚠️")
            raise
        except Exception as e:
            turn_status = "ERROR"
            turn_error = str(e)[:500]
            logger.error("Error executing prompt for chat %s: %s", chat_id, e, exc_info=True)
            target_msg_id = self.active_progress_messages.get(chat_id, msg_id)
            target_reply_id = self.active_reply_to_ids.get(chat_id, reply_to_id)
            if target_reply_id:
                await self._safe_set_message_reaction(chat_id, target_reply_id, "⚠️")
            err_text = f"❌ <b>Execution Error:</b>\n<code>{escape_html(str(e))}</code>"
            if target_msg_id:
                await self._safe_edit_message_text(chat_id=chat_id, message_id=target_msg_id, text=err_text)
            else:
                await self._safe_send_message(chat_id=chat_id, text=err_text, reply_to_message_id=target_reply_id)
        finally:
            if not turn_trace_logged:
                turn_trace_logged = True
                log_trace_span(
                    trace_id=turn_trace_id,
                    span_id=turn_span_id,
                    operation="agent_turn",
                    status=turn_status,
                    duration_ms=round((time.time() - start_time) * 1000, 1),
                    source_project=active_proj,
                    tokens=total_tokens,
                    model=effective_model or "",
                    permission_preset=effective_permission_preset or "",
                    error=turn_error,
                    trace_file=Path(getattr(self.config, "traces_file", "runtime/traces.jsonl")),
                )
            if bridge and not bridge.is_running():
                self.active_bridges.pop(chat_id, None)
            self.active_progress_messages.pop(chat_id, None)
            self.active_reply_to_ids.pop(chat_id, None)
            self.session_manager.set_busy(chat_id, False)
            banner_task = asyncio.create_task(self.update_pinned_banner(chat_id, is_busy=False))
            turn_post_tasks.append(banner_task)

            restart_triggered = False
            self._active_prompt_count = max(0, self._active_prompt_count - 1)
            if self._active_prompt_count == 0:
                try:
                    self._turn_lock_file.unlink(missing_ok=True)
                except Exception:
                    pass
                if self._restart_flag_file.is_file():
                    try:
                        self._restart_flag_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if turn_post_tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*turn_post_tasks, return_exceptions=True),
                                timeout=15.0,
                            )
                        except Exception:
                            pass
                    self._spawn_detached_restart()
                    restart_triggered = True

            if not restart_triggered and turn_status != "CANCELLED":
                next_item: Optional[Dict[str, Any]] = None
                async with self._get_chat_lock(chat_id):
                    q = self._prompt_queues.get(chat_id)
                    if q:
                        next_item = q.pop(0)
                if next_item is not None:
                    q_task = asyncio.create_task(
                        self.handle_prompt(
                            chat_id=chat_id,
                            prompt=next_item["prompt"],
                            reply_to_id=next_item.get("reply_to_id"),
                            is_voice_input=bool(next_item.get("is_voice_input", False)),
                        )
                    )

                    def _on_queued_done(t: asyncio.Task) -> None:
                        if not t.cancelled() and t.exception():
                            logger.error("Unhandled exception running queued prompt for chat %s: %s", chat_id, t.exception())

                    q_task.add_done_callback(_on_queued_done)

    async def _execute_scheduled_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled cron or heartbeat task in an isolated ephemeral worker."""
        if not self.is_chat_authorized(task.chat_id):
            logger.warning("Skipping scheduled task %s for unauthorized chat %s", task.task_id, task.chat_id)
            return

        session = self.session_manager.get_session(task.chat_id)
        proj_name = task.project or session.active_project or "default"
        proj_info = session.projects.get(proj_name, {})
        target_path = proj_info.get("path") or self.config.agy_working_dir or os.getcwd()
        target_perm = session.get_permission_preset(
            project_name=proj_name,
            default=getattr(self.config, "default_permission_preset", "auto"),
        )
        effective_model = session.model or self.config.default_model or None
        effective_effort = session.effort or self.config.default_effort or None

        if task.kind == "heartbeat":
            worker_prompt = (
                f'[AGY_HEARTBEAT task_id="{task.task_id}" project="{proj_name}" schedule="{task.schedule}"]\n'
                f"{task.prompt}\n"
                "[END_AGY_HEARTBEAT]\n\n"
                "(System Directive: This is a periodic background heartbeat check. Inspect the workspace/state cleanly. "
                "If everything is healthy and requires no user notification, reply strictly with HEARTBEAT_OK. "
                "Otherwise provide a concise alert report.)"
            )
        else:
            worker_prompt = (
                f'[AGY_SCHEDULED task_id="{task.task_id}" project="{proj_name}" schedule="{task.schedule}"]\n'
                f"{task.prompt}\n"
                "[END_AGY_SCHEDULED]"
            )

        bridge = AgyBridge(
            agy_bin=self.config.get_resolved_agy_bin(),
            default_working_dir=target_path,
            timeout_seconds=self.config.agy_timeout_seconds,
            system_prompt=None,
            keep_alive=False,
        )

        trace_id = generate_trace_id()
        span_id = generate_span_id()
        start_ts = time.time()
        accumulated = ""
        is_error = False
        err_msg = ""

        try:
            async for event in bridge.run_ephemeral_worker(
                prompt=worker_prompt,
                working_dir=target_path,
                conversation_id=None,
                model=effective_model,
                effort=effective_effort,
                timeout_seconds=self.config.agy_timeout_seconds,
                permission_preset=target_perm,
            ):
                if event.type in ("text", "text_delta"):
                    accumulated += (event.text_delta or getattr(event, "content", "") or "")
                elif event.type in ("result", "done"):
                    if event.response:
                        accumulated = event.response
                    if event.status and str(event.status).upper() in ("ERROR", "FAILED", "FAILURE", "TIMEOUT"):
                        is_error = True
                        err_msg = event.response or str(event.status)
                    break
                elif event.type == "error":
                    is_error = True
                    err_msg = event.response or "Scheduled worker error"
                    break
        except Exception as e:
            is_error = True
            err_msg = str(e)
            logger.error("Scheduled task %s execution error: %s", task.task_id, e, exc_info=True)

        clean_text, _ = extract_agy_voice_block(accumulated)
        clean_text, _ = extract_agy_action_blocks(clean_text)
        clean_text = clean_text.strip()
        is_quiet = task.kind == "heartbeat" and not is_error and is_quiet_heartbeat_reply(clean_text)
        dur_ms = round((time.time() - start_ts) * 1000, 1)
        status_str = "ERROR" if is_error else "SUCCESS"

        log_trace_span(
            trace_id=trace_id,
            span_id=span_id,
            operation="scheduled_task",
            status=status_str,
            duration_ms=dur_ms,
            source_project=proj_name,
            model=effective_model or "",
            permission_preset=target_perm,
            error=err_msg[:500] if is_error else "",
            metadata={
                "task_id": task.task_id,
                "schedule": task.schedule,
                "kind": task.kind,
                "quiet_heartbeat": is_quiet,
            },
            trace_file=Path(getattr(self.config, "traces_file", "runtime/traces.jsonl")),
        )
        self.scheduler.mark_executed(task.task_id, status=status_str)

        if is_quiet:
            return

        if is_error:
            header = f"⚠️ <b>[{escape_html(task.kind.title())} Error · {escape_html(proj_name)}]</b> <code>{escape_html(task.task_id)}</code>\n\n"
            body_txt = f"<code>{escape_html(err_msg or 'Execution failed')}</code>"
        elif task.kind == "heartbeat":
            header = f"💓 <b>[Heartbeat Alert · {escape_html(proj_name)}]</b> <code>{escape_html(task.task_id)}</code>\n\n"
            body_txt = format_final_result(clean_text or "Check completed.", duration_seconds=dur_ms / 1000.0, project_name=proj_name)
        else:
            header = f"⏰ <b>[Scheduled · {escape_html(proj_name)}]</b> <code>{escape_html(task.task_id)}</code>\n\n"
            body_txt = format_final_result(clean_text or "Completed.", duration_seconds=dur_ms / 1000.0, project_name=proj_name)

        full_msg = header + body_txt
        for chunk in split_text_chunks(full_msg, chunk_size=4000):
            await self._safe_send_message(chat_id=task.chat_id, text=chunk)


if __name__ == "__main__":
    print("AgyTelegramBot initialized with modular architecture!")
