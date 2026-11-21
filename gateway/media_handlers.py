from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.message_formatter import escape_html
from gateway.transcriber import transcribe_voice

logger = logging.getLogger(__name__)

TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram Bot API getFile limit (20 MB)

_MEDIA_ATTACHMENT_SPECS = (
    ("document", "document", "application/octet-stream", None),
    ("video", "video", "video/mp4", ".mp4"),
    ("animation", "animation", "video/mp4", ".mp4"),
    ("video_note", "video_note", "video/mp4", ".mp4"),
)


def _extract_attachment_metadata(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract normalized attachment metadata from document, photo, video, animation, or video_note."""
    for field, default_prefix, default_mime, default_ext in _MEDIA_ATTACHMENT_SPECS:
        obj = message.get(field)
        if not isinstance(obj, dict):
            continue
        file_id = obj.get("file_id")
        file_unique_id = obj.get("file_unique_id", default_prefix)
        fallback_name = f"{default_prefix}_{file_unique_id}{default_ext}" if default_ext else default_prefix
        return {
            "kind": field,
            "file_id": file_id,
            "file_name": obj.get("file_name") or fallback_name,
            "file_size": int(obj.get("file_size", 0) or 0),
            "mime_type": obj.get("mime_type") or default_mime,
            "duration": int(obj["duration"]) if obj.get("duration") is not None else None,
        }

    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        photo_obj = photos[-1]
        file_unique_id = photo_obj.get("file_unique_id", "photo")
        return {
            "kind": "photo",
            "file_id": photo_obj.get("file_id"),
            "file_name": f"photo_{file_unique_id}.jpg",
            "file_size": int(photo_obj.get("file_size", 0) or 0),
            "mime_type": "image/jpeg",
            "duration": None,
        }

    return None


async def handle_voice_message(
    bot: Any,
    chat_id: int,
    message: Dict[str, Any],
    message_id: Optional[int],
) -> None:
    """Handle incoming voice notes or audio files, download locally, transcribe via faster-whisper, and send recognized text to agy."""
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

    # Transcribe audio using faster-whisper
    transcribed_text = ""
    detected_lang = "zh"
    whisper_model_size = getattr(bot.config, "whisper_model_size", "base") if hasattr(bot, "config") else "base"
    try:
        transcribed_text, detected_lang = await transcribe_voice(
            audio_path=abs_path,
            model_size=whisper_model_size,
        )
    except Exception as e:
        logger.warning("Transcription failed for audio %s: %s", abs_path, e)

    # Combine transcribed text with caption if present
    final_prompt = ""
    if transcribed_text and caption:
        final_prompt = f"{transcribed_text}\n\n[Caption]: {caption}"
    elif transcribed_text:
        final_prompt = transcribed_text
    elif caption:
        final_prompt = caption

    if not final_prompt:
        logger.warning("Voice message could not be transcribed (empty audio) in chat %s", chat_id)
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Could not recognize any speech from the voice message. Please try speaking closer to the microphone or send text.",
            reply_to_message_id=message_id,
        )
        return

    logger.info("Transcribed voice message for chat %s [lang=%s]: '%s'", chat_id, detected_lang, final_prompt)

    # Check if session is currently busy -> steer into active session
    session = bot.session_manager.get_session(chat_id)
    if session.is_busy:
        bridge = bot.active_bridges.get(chat_id)
        if bridge:
            steered = await bot.perform_steering(chat_id, final_prompt, message_id, is_voice=True, duration=duration)
            if steered:
                return
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⏳ A prompt is currently processing. Use /steer to guide it or /stop to cancel.",
            reply_to_message_id=message_id,
        )
        return

    # Execute prompt through agy
    await bot.handle_prompt(chat_id, final_prompt, message_id, is_voice_input=True)


async def handle_document_message(
    bot: Any,
    chat_id: int,
    message: Dict[str, Any],
    message_id: Optional[int],
) -> None:
    """Handle incoming documents, photos, videos, animations, or video notes."""
    meta = _extract_attachment_metadata(message)
    file_id = meta.get("file_id") if meta else None

    if not meta or not file_id:
        logger.warning("Media/Document message received without file_id in chat %s", chat_id)
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⚠️ Unable to process file: missing file ID.",
            reply_to_message_id=message_id,
        )
        return

    file_name = meta["file_name"]
    file_size = meta["file_size"]
    mime_type = meta["mime_type"]
    duration = meta["duration"]

    if file_size > TELEGRAM_MAX_DOWNLOAD_BYTES:
        size_mb = file_size / (1024 * 1024)
        logger.warning(
            "Attachment '%s' in chat %s exceeds 20 MB Bot API limit (%d bytes)",
            file_name,
            chat_id,
            file_size,
        )
        await bot._safe_send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ File <code>{escape_html(file_name)}</code> is too large ({size_mb:.1f} MB). "
                "Telegram Bot API only allows downloading files up to 20 MB. "
                "Please trim, compress, or send a shorter clip."
            ),
            reply_to_message_id=message_id,
        )
        return

    caption = message.get("caption", "").strip()

    logger.info(
        "Received %s attachment '%s' in chat %s (size=%d, mime=%s)",
        meta["kind"],
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
    if duration is not None:
        prompt_lines.append(f"• Duration: {duration}s")
    if caption:
        prompt_lines.append(f"• User Instructions/Caption: {caption}")
    prompt_lines.append("")
    prompt_lines.append(
        "Please inspect this file and execute the requested task."
    )
    prompt_text = "\n".join(prompt_lines)

    # Check if session is currently busy -> steer into active session
    session = bot.session_manager.get_session(chat_id)
    if session.is_busy:
        bridge = bot.active_bridges.get(chat_id)
        if bridge and bridge.is_running():
            steered = await bot.perform_steering(chat_id, prompt_text, message_id)
            if steered:
                return
        await bot._safe_send_message(
            chat_id=chat_id,
            text="⏳ A prompt is currently processing. Use /steer to guide it or /stop to cancel.",
            reply_to_message_id=message_id,
        )
        return

    # Execute prompt through agy
    await bot.handle_prompt(chat_id, prompt_text, message_id)


if __name__ == "__main__":
    sample_video = {
        "video": {
            "file_id": "v1",
            "file_unique_id": "vu1",
            "file_size": 1024,
            "duration": 8,
        }
    }
    assert _extract_attachment_metadata(sample_video)["file_name"] == "video_vu1.mp4"
    print("Media handlers module self-test passed.")
