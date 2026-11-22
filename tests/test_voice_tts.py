"""Unit tests for voice TTS synthesizer."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from gateway.voice_tts import (
    DEFAULT_VOICE_EN,
    DEFAULT_VOICE_ZH,
    synthesize_voice_ogg,
)


@pytest.mark.asyncio
async def test_synthesize_voice_empty_text():
    res = await synthesize_voice_ogg("")
    assert res is None

    res_spaces = await synthesize_voice_ogg("   \n\t ")
    assert res_spaces is None


@pytest.mark.asyncio
async def test_synthesize_voice_edge_tts(tmp_path):
    out_file = tmp_path / "test_reply.ogg"

    # Mock edge_tts and ffmpeg
    with patch("edge_tts.Communicate") as mock_comm:
        async def mock_save(dest):
            Path(dest).write_bytes(b"MOCK_MP3_DATA")

        mock_instance = mock_comm.return_value
        mock_instance.save = mock_save

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            async def mock_communicate():
                out_file.write_bytes(b"MOCK_OGG_OPUS_DATA")
                return b"", b""

            mock_proc = AsyncMock()
            mock_proc.communicate = mock_communicate
            mock_exec.return_value = mock_proc

            with patch("gateway.voice_tts._resolve_ffmpeg_bin", return_value="/usr/bin/ffmpeg"):
                result = await synthesize_voice_ogg(
                    text="你好，这是测试语音摘要。",
                    lang="zh",
                    output_path=out_file,
                )
            assert result == out_file
            assert out_file.is_file()
            assert out_file.read_bytes() == b"MOCK_OGG_OPUS_DATA"
            # Verify companion script file
            script_file = out_file.with_suffix(".txt")
            assert script_file.is_file()
            assert script_file.read_text(encoding="utf-8") == "你好，这是测试语音摘要。"


@pytest.mark.asyncio
async def test_synthesize_voice_edge_tts_without_ffmpeg(tmp_path):
    out_file = tmp_path / "test_reply_no_ffmpeg.ogg"

    with patch("edge_tts.Communicate") as mock_comm:
        async def mock_save(dest):
            Path(dest).write_bytes(b"MOCK_MP3_DATA")

        mock_instance = mock_comm.return_value
        mock_instance.save = mock_save

        with patch("gateway.voice_tts._resolve_ffmpeg_bin", return_value=None):
            result = await synthesize_voice_ogg(
                text="Testing Edge-TTS without ffmpeg installed.",
                lang="en",
                output_path=out_file,
            )
            assert result == out_file
            assert out_file.is_file()
            assert out_file.read_bytes() == b"MOCK_MP3_DATA"
            script_file = out_file.with_suffix(".txt")
            assert script_file.is_file()
            assert script_file.read_text(encoding="utf-8") == "Testing Edge-TTS without ffmpeg installed."


@pytest.mark.asyncio
async def test_synthesize_voice_fallback_macos(tmp_path):
    out_file = tmp_path / "fallback_reply.ogg"

    # Simulate edge_tts failure
    with patch("edge_tts.Communicate", side_effect=RuntimeError("Network offline")):
        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            async def mock_exec_func(*args, **kwargs):
                cmd = args[0]
                target_out = args[-1]
                if "say" in cmd:
                    Path(target_out).write_bytes(b"MOCK_AIFF_BYTES")
                elif "ffmpeg" in cmd:
                    Path(target_out).write_bytes(b"MOCK_MACOS_OGG_DATA")

                mock_p = AsyncMock()
                mock_p.communicate = AsyncMock(return_value=(b"", b""))
                return mock_p

            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec_func):
                result = await synthesize_voice_ogg(
                    text="All 116 tests passed.",
                    lang="en",
                    output_path=out_file,
                )
                assert result == out_file
                assert out_file.is_file()
                assert out_file.read_bytes() == b"MOCK_MACOS_OGG_DATA"


def test_cleanup_old_voice_files(tmp_path):
    from gateway.voice_tts import cleanup_old_voice_files
    import time

    # Create old and new files (ogg, oga, and txt)
    old_ogg = tmp_path / "voice_20260101_120000_v1.ogg"
    old_ogg.write_bytes(b"OLD_OGG")
    os.utime(old_ogg, (time.time() - (2 * 86400), time.time() - (2 * 86400)))

    old_txt = tmp_path / "voice_20260101_120000_v1.txt"
    old_txt.write_bytes(b"OLD_TXT")
    os.utime(old_txt, (time.time() - (2 * 86400), time.time() - (2 * 86400)))

    old_oga = tmp_path / "voice_123_456_789.oga"
    old_oga.write_bytes(b"OLD_OGA")
    os.utime(old_oga, (time.time() - (2 * 86400), time.time() - (2 * 86400)))

    new_file = tmp_path / "voice_20260819_120000_v2.ogg"
    new_file.write_bytes(b"NEW_OGG")

    pruned = cleanup_old_voice_files(cache_dir=tmp_path, max_age_days=1)
    assert pruned == 3
    assert not old_ogg.exists()
    assert not old_txt.exists()
    assert not old_oga.exists()
    assert new_file.exists()



