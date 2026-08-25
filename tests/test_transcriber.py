import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.transcriber import _get_whisper_model_sync, _transcribe_sync, transcribe_voice


def test_get_whisper_model_caching():
    with patch("faster_whisper.WhisperModel") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance

        m1 = _get_whisper_model_sync(model_size="test_size", device="cpu", compute_type="int8")
        m2 = _get_whisper_model_sync(model_size="test_size", device="cpu", compute_type="int8")

        assert m1 is instance
        assert m2 is instance
        mock_cls.assert_called_once_with("test_size", device="cpu", compute_type="int8")


def test_transcribe_sync_file_not_found():
    with pytest.raises(FileNotFoundError):
        _transcribe_sync("/non/existent/audio/file.oga")


def test_transcribe_sync_success(tmp_path):
    dummy_file = tmp_path / "test.oga"
    dummy_file.write_bytes(b"DUMMY_AUDIO")

    mock_segment1 = MagicMock(text="Hello ")
    mock_segment2 = MagicMock(text="world")
    mock_info = MagicMock(language="en")

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment1, mock_segment2], mock_info)

    with patch("gateway.transcriber._get_whisper_model_sync", return_value=mock_model):
        text, lang = _transcribe_sync(str(dummy_file), model_size="base")
        assert text == "Hello world"
        assert lang == "en"


@pytest.mark.asyncio
async def test_transcribe_voice_async(tmp_path):
    dummy_file = tmp_path / "voice_test.oga"
    dummy_file.write_bytes(b"DUMMY_AUDIO")

    with patch("gateway.transcriber._transcribe_sync", return_value=("测试语音识别", "zh")) as mock_sync:
        text, lang = await transcribe_voice(dummy_file, model_size="base")
        assert text == "测试语音识别"
        assert lang == "zh"
        mock_sync.assert_called_once()
