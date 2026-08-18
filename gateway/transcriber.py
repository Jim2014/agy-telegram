"""Automatic speech recognition (ASR) module for transcribing voice messages using faster-whisper."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure repository root is in sys.path when executed directly
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

logger = logging.getLogger(__name__)


class VoiceTranscriber:
    """Lightweight async speech transcriber backed by faster-whisper with lazy model loading."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _get_model(self) -> Any:
        """Lazily initialize and cache the WhisperModel instance."""
        if self._model is None:
            try:
                from faster_whisper import WhisperModel

                logger.info(
                    "Loading faster-whisper model '%s' (device=%s, compute_type=%s)...",
                    self.model_size,
                    self.device,
                    self.compute_type,
                )
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                )
                logger.info("faster-whisper model '%s' loaded successfully.", self.model_size)
            except Exception as e:
                logger.warning("Could not load faster-whisper model '%s': %s", self.model_size, e)
                raise
        return self._model

    def _transcribe_sync(self, audio_path: str, language: Optional[str] = None) -> Optional[str]:
        """Synchronous transcription execution."""
        model = self._get_model()
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            language=language,
        )
        texts = [seg.text.strip() for seg in segments if seg.text.strip()]
        result_text = " ".join(texts).strip()
        logger.info(
            "Transcribed audio file %s in language '%s' (prob=%.2f): %s",
            audio_path,
            info.language,
            info.language_probability,
            result_text[:80],
        )
        return result_text if result_text else None

    async def transcribe(self, audio_path: str | Path, language: Optional[str] = None) -> Optional[str]:
        """Transcribe an audio file asynchronously without blocking the event loop."""
        p = Path(audio_path).resolve()
        if not p.is_file():
            logger.error("Audio file does not exist for transcription: %s", p)
            return None

        async with self._lock:
            try:
                t0 = time.time()
                text = await asyncio.to_thread(self._transcribe_sync, str(p), language=language)
                logger.debug("Transcription completed in %.2fs", time.time() - t0)
                return text
            except Exception as e:
                logger.warning("Transcription failed for audio %s: %s", p, e)
                return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    transcriber = VoiceTranscriber()
    print("VoiceTranscriber initialized (model_size: base)")
