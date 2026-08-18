"""Unit tests for gateway.bot command routing and streaming orchestration."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from gateway.agy_bridge import AgyEvent
from gateway.bot import AgyTelegramBot
from gateway.config import Config


@pytest.fixture
def mock_bot(tmp_path):
    cfg = Config(
        bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        allowed_chat_ids={100, 200},
        agy_working_dir="/tmp/test_ws",
        stream_throttle_seconds=0.01,
        sessions_file=str(tmp_path / "bot_sessions.json"),
    )
    bot = AgyTelegramBot(cfg)
    bot.client.send_message = AsyncMock(return_value={"message_id": 999})
    bot.client.edit_message_text = AsyncMock(return_value={"message_id": 999})
    return bot


@pytest.mark.asyncio
async def test_bot_help_and_status(mock_bot):
    # /help
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/help", "message_id": 1}})
    mock_bot.client.send_message.assert_called()
    assert "agy-telegram" in mock_bot.client.send_message.call_args[1]["text"]
    assert "/new" in mock_bot.client.send_message.call_args[1]["text"]
    assert "/open" in mock_bot.client.send_message.call_args[1]["text"]
    assert "/projects" in mock_bot.client.send_message.call_args[1]["text"]

    # /status
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/status", "message_id": 2}})
    assert "Status" in mock_bot.client.send_message.call_args[1]["text"]
    assert "Chat ID" in mock_bot.client.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_model_and_effort(mock_bot):
    # /model
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/model gemini-2.5-flash", "message_id": 3}})
    assert "gemini-2.5-flash" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).model == "gemini-2.5-flash"

    # /effort
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/effort high", "message_id": 4}})
    assert "high" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).effort == "high"


@pytest.mark.asyncio
async def test_bot_new_project_command(mock_bot):
    # /new without args -> usage
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new", "message_id": 10}})
    assert "Usage" in mock_bot.client.send_message.call_args[1]["text"]

    # /new proj1 without path -> usage warning
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new proj1", "message_id": 11}})
    assert "Usage" in mock_bot.client.send_message.call_args[1]["text"]

    # /new proj1 /tmp/proj1 -> created
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new proj1 /tmp/proj1", "message_id": 12}})
    assert "created and activated" in mock_bot.client.send_message.call_args[1]["text"]
    session = mock_bot.session_manager.get_session(100)
    assert session.active_project == "proj1"
    assert session.active_path() == str(Path("/tmp/proj1").resolve())

    # /new proj1 again -> treated as /open
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new proj1 /tmp/proj1_other", "message_id": 13}})
    assert "already exists" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).active_project == "proj1"


@pytest.mark.asyncio
async def test_bot_open_and_projects_command(mock_bot):
    # Create two projects
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new app_a /tmp/app_a", "message_id": 20}})
    mock_bot.session_manager.set_conversation_id(100, "conv-app-a")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new app_b /tmp/app_b", "message_id": 21}})
    mock_bot.session_manager.set_conversation_id(100, "conv-app-b")

    # /projects
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/projects", "message_id": 22}})
    projects_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "app_a" in projects_text
    assert "app_b" in projects_text
    assert "[ACTIVE]" in projects_text

    # /open app_a -> restores conversation
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/open app_a", "message_id": 23}})
    assert "Opened project" in mock_bot.client.send_message.call_args[1]["text"]
    session = mock_bot.session_manager.get_session(100)
    assert session.active_project == "app_a"
    assert session.conversation_id == "conv-app-a"

    # /open nonexistent -> error and lists projects
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/open app_c", "message_id": 24}})
    err_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "not found" in err_text
    assert "Available projects" in err_text


@pytest.mark.asyncio
async def test_bot_delete_and_reset_command(mock_bot):
    # Setup project
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new app_del /tmp/del", "message_id": 30}})
    mock_bot.session_manager.set_conversation_id(100, "conv-del-123")

    # /reset active project
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/reset", "message_id": 31}})
    assert "Conversation reset" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).conversation_id is None

    # /delete app_del
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/delete app_del", "message_id": 32}})
    assert "Project deleted" in mock_bot.client.send_message.call_args[1]["text"]
    session = mock_bot.session_manager.get_session(100)
    assert session.active_project is None
    assert "app_del" not in session.projects

    # /reset when no active project -> warning
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/reset", "message_id": 33}})
    assert "No active project" in mock_bot.client.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_dir_command(mock_bot, tmp_path):
    valid_dir = tmp_path / "valid_dir"
    valid_dir.mkdir()

    # /dir without active project -> warning
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/dir {valid_dir}", "message_id": 40}})
    assert "No active project" in mock_bot.client.send_message.call_args[1]["text"]

    # Create project
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new dir_proj /tmp", "message_id": 41}})

    # /dir with invalid path -> warning
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/dir /tmp/nonexistent_xyz_123", "message_id": 42}})
    assert "does not exist" in mock_bot.client.send_message.call_args[1]["text"]

    # /dir with valid path -> updated
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/dir {valid_dir}", "message_id": 43}})
    assert "Workspace directory" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).active_path() == str(valid_dir.resolve())


@pytest.mark.asyncio
async def test_bot_unauthorized_chat(mock_bot):
    # Chat 999 is not in allowed_chat_ids {100, 200}
    await mock_bot.process_update({"message": {"chat": {"id": 999}, "text": "Hello", "message_id": 1}})
    mock_bot.client.send_message.assert_called_with(
        chat_id=999,
        text="⛔ <b>Access Denied</b>\nThis bot is private and your chat ID is not authorized.",
    )


@pytest.mark.asyncio
async def test_bot_prompt_streaming(mock_bot, monkeypatch):
    # Setup active project
    mock_bot.session_manager.create_project(100, "test_proj", "/tmp")

    async def mock_run_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-streaming-123")
        yield AgyEvent(type="tool", tool_name="list_dir", tool_state="ACTIVE", tool_info={"parameters": {"DirectoryPath": "/tmp"}})
        yield AgyEvent(type="tool", tool_name="list_dir", tool_state="DONE", tool_info={"parameters": {"DirectoryPath": "/tmp"}})
        yield AgyEvent(type="text_delta", text_delta="Calculating...")
        yield AgyEvent(type="text_delta", text_delta=" Answer is 42.")
        yield AgyEvent(
            type="result",
            status="SUCCESS",
            response="Calculating... Answer is 42.",
            duration_seconds=1.5,
            usage={"total_tokens": 500, "thinking_tokens": 50},
            conversation_id="conv-streaming-123",
        )

    from gateway.agy_bridge import AgyBridge
    monkeypatch.setattr(AgyBridge, "run_streaming", mock_run_streaming)

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "What is 6x7?", "message_id": 10}})

    # Verify initial placeholder sent
    mock_bot.client.send_message.assert_called()
    # Verify final message edited
    assert mock_bot.client.edit_message_text.called
    final_call_args = mock_bot.client.edit_message_text.call_args[1]
    assert "Answer is 42" in final_call_args["text"]
    assert "conv-str..." in final_call_args["text"]

    # Verify conversation ID persisted in active project
    session = mock_bot.session_manager.get_session(100)
    assert session.conversation_id == "conv-streaming-123"
    assert session.is_busy is False


@pytest.mark.asyncio
async def test_bot_stop_command(mock_bot):
    # When no task is running
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/stop", "message_id": 20}})
    mock_bot.client.send_message.assert_called_with(
        chat_id=100,
        text="ℹ️ No active task is currently running.",
    )
