"""Unit tests for gateway.bot command routing and streaming orchestration."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from gateway.agy_bridge import AgyEvent
from gateway.bot import AgyTelegramBot
from gateway.config import Config


@pytest.fixture
def mock_bot():
    cfg = Config(
        bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        allowed_chat_ids={100, 200},
        agy_working_dir="/tmp/test_ws",
        stream_throttle_seconds=0.01,
    )
    bot = AgyTelegramBot(cfg)
    bot.client.send_message = AsyncMock(return_value={"message_id": 999})
    bot.client.edit_message_text = AsyncMock(return_value={"message_id": 999})
    return bot


@pytest.mark.asyncio
async def test_bot_commands(mock_bot):
    # /help
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/help", "message_id": 1}})
    mock_bot.client.send_message.assert_called()
    assert "agy-telegram" in mock_bot.client.send_message.call_args[1]["text"]

    # /status
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/status", "message_id": 2}})
    assert "Status" in mock_bot.client.send_message.call_args[1]["text"]

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

    # /new
    mock_bot.client.send_message.reset_mock()
    mock_bot.session_manager.set_conversation_id(100, "old-conv-id")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/new", "message_id": 5}})
    assert "New conversation started" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).conversation_id is None


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
    # Mock AgyBridge streaming generator
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

    # Verify conversation ID persisted in session
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
