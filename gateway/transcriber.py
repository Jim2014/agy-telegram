"""Audio transcription engine for agy-telegram using faster-whisper."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

logger = logging.getLogger(__name__)

# Global model cache to avoid repeated initialization overhead
_whisper_models: Dict[str, Any] = {}


def _get_whisper_model_sync(model_size: str = "base", device: str = "cpu", compute_type: str = "int8") -> Any:
    """Load or retrieve cached WhisperModel instance synchronously."""
    key = f"{model_size}_{device}_{compute_type}"
    if key not in _whisper_models:
        from faster_whisper import WhisperModel

        logger.info("Initializing faster-whisper model '%s' (device=%s, compute_type=%s)...", model_size, device, compute_type)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            _whisper_models[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
        logger.info("faster-whisper model '%s' loaded successfully.", model_size)
    return _whisper_models[key]


def _transcribe_sync(
    audio_path: str,
    model_size: str = "base",
    beam_size: int = 5,
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Synchronous transcription worker executed inside a thread pool.
    Returns (transcribed_text, detected_language).
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = _get_whisper_model_sync(model_size=model_size)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        segments, info = model.transcribe(
            audio_path,
            beam_size=beam_size,
            language=language,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        text_parts = [s.text.strip() for s in segments if s.text.strip()]
        full_text = " ".join(text_parts).strip()
        detected_lang = info.language or "zh"

    return full_text, detected_lang


async def transcribe_voice(
    audio_path: str | Path,
    model_size: str = "base",
    beam_size: int = 5,
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Asynchronously transcribe an audio file into text using faster-whisper.
    Non-blocking to the asyncio event loop.
    Returns (transcribed_text, detected_language).
    """
    target_path = str(Path(audio_path).resolve())
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _transcribe_sync,
        target_path,
        model_size,
        beam_size,
        language,
    )


if __name__ == "__main__":
    import time

    print("=== Transcriber Module Test ===")
    test_files = [
        p for p in Path("runtime/voice").glob("*.oga")
    ] or [
        p for p in Path("runtime/voice").glob("*.ogg")
    ]
    if test_files:
        sample = str(test_files[-1])
        print(f"Testing transcription on: {sample}")
        t0 = time.time()
        text, lang = asyncio.run(transcribe_voice(sample, model_size="base"))
        t1 = time.time()
        print(f"Result ({t1-t0:.2f}s) [lang={lang}]: {text}")
    else:
        print("No voice files found in runtime/voice/ for testing.")
