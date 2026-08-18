"""Unit tests for gateway.transcriber."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from gateway.transcriber import VoiceTranscriber


@pytest.mark.asyncio
async def test_transcriber_file_not_found(tmp_path):
    transcriber = VoiceTranscriber(model_size="base")
    non_existent = tmp_path / "does_not_exist.oga"
    res = await transcriber.transcribe(non_existent)
    assert res is None


@pytest.mark.asyncio
async def test_transcriber_transcribe_mock(tmp_path):
    fake_audio = tmp_path / "test.oga"
    fake_audio.write_bytes(b"FAKEDATA")

    transcriber = VoiceTranscriber(model_size="base")

    # Mock WhisperModel
    mock_seg = MagicMock()
    mock_seg.text = "Hello world from audio"
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.99

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_seg], mock_info)

    transcriber._model = mock_model

    text = await transcriber.transcribe(fake_audio)
    assert text == "Hello world from audio"
    mock_model.transcribe.assert_called_once_with(str(fake_audio.resolve()), beam_size=5, language=None)
