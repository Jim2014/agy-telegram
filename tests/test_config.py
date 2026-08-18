"""Unit tests for gateway.config."""

import os
from pathlib import Path
import pytest
from gateway.config import Config


def test_default_config():
    cfg = Config()
    assert cfg.default_model == "gemini-3.7-flash"
    assert cfg.default_effort == "high"
    assert cfg.stream_throttle_seconds == 1.0
    assert cfg.agy_timeout_seconds == 300
    assert cfg.log_file == "runtime/gateway.log"
    assert cfg.sessions_file == "runtime/sessions.json"
    assert cfg.is_chat_allowed(123456) is True


def test_token_validation():
    cfg = Config(bot_token="")
    assert cfg.is_token_valid() is False

    cfg = Config(bot_token="your_bot_token_here")
    assert cfg.is_token_valid() is False

    cfg = Config(bot_token="invalid_token_no_colon")
    assert cfg.is_token_valid() is False

    cfg = Config(bot_token="abc:def123")
    assert cfg.is_token_valid() is False

    cfg = Config(bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    assert cfg.is_token_valid() is True


def test_chat_whitelist():
    cfg = Config(allowed_chat_ids={111, 222})
    assert cfg.is_chat_allowed(111) is True
    assert cfg.is_chat_allowed(222) is True
    assert cfg.is_chat_allowed(333) is False

    # Empty whitelist allows all
    cfg_empty = Config(allowed_chat_ids=set())
    assert cfg_empty.is_chat_allowed(999) is True


def test_load_from_env_file(tmp_path):
    env_file = tmp_path / ".test.env"
    env_file.write_text(
        "BOT_TOKEN=999999:SecReTtOkEn\n"
        "ALLOWED_CHAT_IDS=123,456\n"
        "DEFAULT_MODEL=gemini-2.5-pro\n"
        "DEFAULT_EFFORT=high\n"
        "STREAM_THROTTLE_SECONDS=2.5\n"
        "AGY_TIMEOUT_SECONDS=180\n"
    )

    cfg = Config.load(env_path=env_file)
    assert cfg.bot_token == "999999:SecReTtOkEn"
    assert cfg.is_token_valid() is True
    assert cfg.allowed_chat_ids == {123, 456}
    assert cfg.default_model == "gemini-2.5-pro"
    assert cfg.default_effort == "high"
    assert cfg.stream_throttle_seconds == 2.5
    assert cfg.agy_timeout_seconds == 180


def test_default_system_prompt():
    cfg = Config.load(env_path=Path("/non_existent_env_file"))
    assert "[AGY_FILE]" in cfg.system_prompt
    assert "[END_AGY_FILE]" in cfg.system_prompt
    assert "mobile" in cfg.system_prompt
    assert "Markdown" in cfg.system_prompt
    assert "Raw HTML" in cfg.system_prompt
    assert "File Path" in cfg.system_prompt
    assert "agy-telegram" in cfg.system_prompt


def test_system_prompt_env_override(tmp_path):
    env_file = tmp_path / ".test.prompt.env"
    env_file.write_text(
        "BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz\n"
        "AGY_SYSTEM_PROMPT=Custom instruction line 1\\nCustom instruction line 2\n"
    )
    cfg = Config.load(env_path=env_file)
    assert "Custom instruction line 1\nCustom instruction line 2" == cfg.system_prompt


def test_system_prompt_file_override(tmp_path):
    prompt_file = tmp_path / "custom_prompt.md"
    prompt_file.write_text("# Custom Instructions\nFollow strict rules for output.", encoding="utf-8")

    env_file = tmp_path / ".test.prompt_file.env"
    env_file.write_text(
        f"BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz\n"
        f"AGY_SYSTEM_PROMPT_FILE={prompt_file}\n"
        f"AGY_SYSTEM_PROMPT=Should be overridden by file\n"
    )
    cfg = Config.load(env_path=env_file)
    assert cfg.system_prompt == "# Custom Instructions\nFollow strict rules for output."

