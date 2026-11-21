"""Unit tests for gateway.setup_helper."""

from pathlib import Path
from unittest.mock import MagicMock

from gateway.setup_helper import (
    detect_chat_id_via_polling,
    render_env_content,
    run_interactive_setup,
    verify_bot_token,
    write_env_file,
)


def test_verify_bot_token_invalid_format():
    assert verify_bot_token("") is None
    assert verify_bot_token("not-a-valid-token") is None


def test_verify_bot_token_success_and_failure():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {"id": 12345, "username": "my_agy_bot", "first_name": "AGY Bot"},
    }
    mock_client.get.return_value = mock_resp

    info = verify_bot_token("123456789:ABCdefGHIjklMNOpqrsTUVwxyz", http_client=mock_client)
    assert info is not None
    assert info["username"] == "my_agy_bot"

    mock_resp.status_code = 401
    assert verify_bot_token("123456789:ABCdefGHIjklMNOpqrsTUVwxyz", http_client=mock_client) is None


def test_detect_chat_id_via_polling():
    mock_client = MagicMock()
    # First check returns empty, second poll returns a /start update
    empty_resp = MagicMock(status_code=200)
    empty_resp.json.return_value = {"ok": True, "result": []}

    msg_resp = MagicMock(status_code=200)
    msg_resp.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 1001,
                "message": {
                    "chat": {"id": 987654321, "type": "private", "username": "alice"},
                    "from": {"id": 987654321, "first_name": "Alice", "username": "alice"},
                    "text": "/start",
                },
            }
        ],
    }
    mock_client.get.side_effect = [empty_resp, msg_resp]

    result = detect_chat_id_via_polling(
        "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        timeout_seconds=5,
        poll_interval=0.0,
        http_client=mock_client,
        sleep_fn=lambda _: None,
    )
    assert result == (987654321, "Alice (@alice)")


def test_render_and_write_env_file(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(
        "# Comment line\n"
        "BOT_TOKEN=your_bot_token_here\n"
        "ALLOWED_CHAT_IDS=\n"
        "# HTML_MODE=auto\n",
        encoding="utf-8",
    )
    target = tmp_path / ".env"

    write_env_file(
        target,
        updates={
            "BOT_TOKEN": "111222333:SecReTtOkEn123456",
            "ALLOWED_CHAT_IDS": "555666",
            "HTML_MODE": "explicit",
            "CUSTOM_VAR": "hello world",
        },
        example_path=example,
    )

    content = target.read_text(encoding="utf-8")
    assert "# Comment line" in content
    assert "BOT_TOKEN=111222333:SecReTtOkEn123456" in content
    assert "ALLOWED_CHAT_IDS=555666" in content
    assert "HTML_MODE=explicit" in content
    assert 'CUSTOM_VAR="hello world"' in content
    assert (target.stat().st_mode & 0o777) == 0o600


def test_run_interactive_setup_skip_network(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("BOT_TOKEN=\nALLOWED_CHAT_IDS=\n", encoding="utf-8")
    target = tmp_path / ".env"

    answers = iter([
        "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",  # BOT_TOKEN
        "777888",                               # ALLOWED_CHAT_IDS
        "/usr/local/bin/agy",                   # AGY_BIN_PATH
        str(tmp_path),                          # AGY_WORKING_DIR
        "explicit",                             # HTML_MODE
        "autosend",                             # TTS_MODE
    ])

    rc = run_interactive_setup(
        env_path=target,
        example_path=example,
        input_fn=lambda _: next(answers),
        skip_network=True,
    )
    assert rc == 0
    saved = target.read_text(encoding="utf-8")
    assert "BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz" in saved
    assert "ALLOWED_CHAT_IDS=777888" in saved
    assert "AGY_BIN_PATH=/usr/local/bin/agy" in saved
    assert "HTML_MODE=explicit" in saved
    assert "TTS_MODE=autosend" in saved
