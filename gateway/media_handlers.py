from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.message_formatter import escape_html

logger = logging.getLogger(__name__)


async def handle_voice_message(
    bot: Any,
    chat_id: int,
    message: Dict[str, Any],
    message_id: Optional[int],
) -> None:
    """Handle incoming voice notes or audio files, download locally, and send file path to agy."""
    voice_obj = message.get("voice") or message.get("audio") or {}
    file_id = voice_obj.get("file_id")
    if not file_id:
        logger.warning("Voice/Audio message received without file_id in chat %s", chat_id)
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Unable to process audio: missing file ID.",
            reply_to_message_id=message_id,
        )
        return

    duration = int(voice_obj.get("duration", 0))
    mime_type = voice_obj.get("mime_type", "audio/ogg")
    file_size = voice_obj.get("file_size")
    caption = message.get("caption", "").strip()
    is_voice = "voice" in message

    logger.info(
        "Received %s in chat %s (duration=%ds, mime=%s, size=%s)",
        "voice message" if is_voice else "audio file",
        chat_id,
        duration,
        mime_type,
        file_size,
    )

    await bot._safe_send_chat_action(chat_id=chat_id, action="record_voice")

    # Download file to runtime/voice directory
    voice_dir = Path("runtime/voice")
    voice_dir.mkdir(parents=True, exist_ok=True)

    try:
        saved_path, _ = await bot.client.download_file_by_id(
            file_id=file_id,
            destination_dir=voice_dir,
            filename_prefix=f"voice_{chat_id}",
        )
    except Exception as e:
        logger.error("Failed to download voice message %s: %s", file_id, e)
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"⚠️ Failed to download voice message: <code>{escape_html(str(e))}</code>",
            reply_to_message_id=message_id,
        )
        return

    abs_path = str(saved_path.resolve())

    # Construct prompt for agy
    prompt_lines = [
        "🎙️ [User Voice Command]",
        "The user sent a voice instruction in Telegram.",
        f"• Local Audio File Path: {abs_path}",
        f"• Duration: {duration}s",
        f"• MIME Type: {mime_type}",
    ]
    if caption:
        prompt_lines.append(f"• Accompanying Caption: {caption}")
    prompt_lines.append("")
    prompt_lines.append(
        "Please inspect and transcribe/listen to this audio file to understand the user's instructions, and then execute the requested task."
    )
    prompt_text = "\n".join(prompt_lines)

    # Check if session is currently busy -> steer into active session
    session = bot.session_manager.get_session(chat_id)
    if session.is_busy and chat_id in bot.active_bridges:
        steered = await bot.active_bridges[chat_id].steer(prompt_text)
        if steered:
            dur_text = f" ({duration}s)" if duration else ""
            await bot._safe_send_message(
                chat_id=chat_id,
                text=(
                    f"🎯 <b>Steered voice instruction{dur_text} into active session!</b>\n"
                    f"• <b>Audio File:</b> <code>{escape_html(abs_path)}</code>"
                ),
                reply_to_message_id=message_id,
            )
            return

    # Execute prompt through agy
    await bot.handle_prompt(chat_id, prompt_text, message_id)


async def handle_document_message(
    bot: Any,
    chat_id: int,
    message: Dict[str, Any],
    message_id: Optional[int],
) -> None:
    """Handle incoming documents or photos, download locally, and send file path to agy."""
    file_id: Optional[str] = None
    file_name = "document"
    file_size = 0
    mime_type = "application/octet-stream"

    if "document" in message:
        doc = message["document"]
        file_id = doc.get("file_id")
        file_name = doc.get("file_name", "document")
        file_size = int(doc.get("file_size", 0))
        mime_type = doc.get("mime_type", "application/octet-stream")
    elif "photo" in message and isinstance(message["photo"], list) and message["photo"]:
        photo_obj = message["photo"][-1]
        file_id = photo_obj.get("file_id")
        file_unique_id = photo_obj.get("file_unique_id", "photo")
        file_name = f"photo_{file_unique_id}.jpg"
        file_size = int(photo_obj.get("file_size", 0))
        mime_type = "image/jpeg"

    if not file_id:
        logger.warning("Document/Photo message received without file_id in chat %s", chat_id)
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Unable to process file: missing file ID.",
            reply_to_message_id=message_id,
        )
        return

    caption = message.get("caption", "").strip()

    logger.info(
        "Received document/attachment '%s' in chat %s (size=%d, mime=%s)",
        file_name,
        chat_id,
        file_size,
        mime_type,
    )

    await bot._safe_send_chat_action(chat_id=chat_id, action="upload_document")

    # Download file to runtime/documents directory
    doc_dir = Path("runtime/documents")
    doc_dir.mkdir(parents=True, exist_ok=True)

    try:
        saved_path, _ = await bot.client.download_file_by_id(
            file_id=file_id,
            destination_dir=doc_dir,
            filename_prefix=f"doc_{chat_id}",
        )
    except Exception as e:
        logger.error("Failed to download document %s: %s", file_id, e)
        await bot._safe_send_message(
            chat_id=chat_id,
            text=f"⚠️ Failed to download file: <code>{escape_html(str(e))}</code>",
            reply_to_message_id=message_id,
        )
        return

    abs_path = str(saved_path.resolve())

    # Construct prompt for agy
    prompt_lines = [
        "📎 [User Uploaded File]",
        "The user sent a file in Telegram:",
        f"• Original File Name: {file_name}",
        f"• Local File Path: {abs_path}",
        f"• File Size: {file_size} bytes",
        f"• MIME Type: {mime_type}",
    ]
    if caption:
        prompt_lines.append(f"• User Instructions/Caption: {caption}")
    prompt_lines.append("")
    prompt_lines.append(
        "Please inspect this file and execute the requested task."
    )
    prompt_text = "\n".join(prompt_lines)

    # Check if session is currently busy -> steer into active session
    session = bot.session_manager.get_session(chat_id)
    if session.is_busy and chat_id in bot.active_bridges:
        steered = await bot.active_bridges[chat_id].steer(prompt_text)
        if steered:
            await bot._safe_send_message(
                chat_id=chat_id,
                text=(
                    f"📎 <b>Steered uploaded file into active session:</b>\n"
                    f"• <b>File:</b> <code>{escape_html(file_name)}</code>\n"
                    f"• <b>Path:</b> <code>{escape_html(abs_path)}</code>"
                ),
                reply_to_message_id=message_id,
            )
            return

    # Execute prompt through agy
    await bot.handle_prompt(chat_id, prompt_text, message_id)


if __name__ == "__main__":
    print("Media handlers module initialized.")
