"""Text-To-Speech (TTS) synthesizer for agy-telegram voice broadcasts."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default neural voices for Edge-TTS
DEFAULT_VOICE_ZH = "zh-CN-XiaoxiaoNeural"
DEFAULT_VOICE_EN = "en-US-JennyNeural"

# Default fallback voices for macOS 'say'
MACOS_VOICE_ZH = "Tingting"
MACOS_VOICE_EN = "Samantha"


async def synthesize_voice_ogg(
    text: str,
    lang: str = "zh",
    output_path: Optional[Path] = None,
    voice: Optional[str] = None,
    voice_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Synthesize text into a Telegram-compliant OGG Opus audio file.
    Uses Edge-TTS with graceful offline fallback to macOS 'say' + ffmpeg.
    Stored in runtime/voice/ with timestamped filenames for easy lifecycle cleanup.
    """
    clean_text = text.strip()
    if not clean_text:
        return None

    if output_path is None:
        cache_dir = Path("runtime/voice")
        cache_dir.mkdir(parents=True, exist_ok=True)
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        v_tag = voice_id or f"{os.getpid()}_{hash(clean_text) % 10000}"
        filename = f"voice_{ts_str}_{v_tag}.ogg"
        output_path = cache_dir / filename
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    is_en = lang.lower().startswith("en")
    voice_name = voice or (DEFAULT_VOICE_EN if is_en else DEFAULT_VOICE_ZH)

    # 1. Primary engine: Edge-TTS
    try:
        import edge_tts

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            temp_mp3 = Path(tf.name)

        communicate = edge_tts.Communicate(clean_text, voice_name)
        await communicate.save(str(temp_mp3))

        if temp_mp3.is_file() and temp_mp3.stat().st_size > 0:
            # Convert MP3 to Telegram native Opus OGG using ffmpeg
            ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg" or "/usr/local/bin/ffmpeg"
            if shutil.which("ffmpeg") or Path(ffmpeg_bin).is_file():
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(temp_mp3),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "32k",
                    "-ar",
                    "48000",
                    str(output_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()
            else:
                shutil.copy(temp_mp3, output_path)

            try:
                temp_mp3.unlink(missing_ok=True)
            except Exception:
                pass

            if output_path.is_file() and output_path.stat().st_size > 0:
                logger.info("Synthesized voice note (%d bytes) with Edge-TTS voice '%s'", output_path.stat().st_size, voice_name)
                return output_path
    except Exception as e:
        logger.warning("Edge-TTS synthesis failed (%s), attempting macOS offline fallback...", e)

    # 2. Offline Fallback: macOS native 'say' + ffmpeg
    say_bin = shutil.which("say") or "/usr/bin/say"
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg" or "/usr/local/bin/ffmpeg"

    if (shutil.which("say") or Path(say_bin).is_file()) and (shutil.which("ffmpeg") or Path(ffmpeg_bin).is_file()):
        try:
            with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tf:
                temp_aiff = Path(tf.name)

            macos_voice = MACOS_VOICE_EN if is_en else MACOS_VOICE_ZH

            say_proc = await asyncio.create_subprocess_exec(
                say_bin,
                "-v",
                macos_voice,
                clean_text,
                "-o",
                str(temp_aiff),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await say_proc.communicate()

            if temp_aiff.is_file() and temp_aiff.stat().st_size > 0:
                ffmpeg_proc = await asyncio.create_subprocess_exec(
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(temp_aiff),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "32k",
                    "-ar",
                    "48000",
                    str(output_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await ffmpeg_proc.communicate()

                try:
                    temp_aiff.unlink(missing_ok=True)
                except Exception:
                    pass

                if output_path.is_file() and output_path.stat().st_size > 0:
                    logger.info("Synthesized voice note via macOS 'say' fallback (%s)", macos_voice)
                    return output_path
        except Exception as fallback_err:
            logger.error("macOS fallback TTS also failed: %s", fallback_err)

    logger.error("Failed to synthesize voice summary audio.")
    return None


def cleanup_old_voice_files(cache_dir: Optional[Path] = None, max_age_days: int = 1) -> int:
    """
    Remove synthesized voice notes older than max_age_days from runtime/voice.
    Returns number of pruned files.
    """
    target_dir = cache_dir or Path("runtime/voice")
    if not target_dir.is_dir():
        return 0

    now = time.time()
    cutoff = now - (max_age_days * 86400)
    deleted_count = 0

    for ext in ("voice_*.ogg", "voice_*.oga"):
        for file_path in target_dir.glob(ext):
            try:
                if file_path.stat().st_mtime < cutoff:
                    file_path.unlink(missing_ok=True)
                    deleted_count += 1
            except Exception as e:
                logger.warning("Failed to delete old voice file %s: %s", file_path, e)

    if deleted_count > 0:
        logger.info("Cleaned up %d old voice audio files (>%d days old)", deleted_count, max_age_days)
    return deleted_count
