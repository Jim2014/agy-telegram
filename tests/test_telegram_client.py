"""Unit tests for gateway.telegram_client."""

import pytest
import httpx
from gateway.telegram_client import TelegramClient, TelegramAPIError


@pytest.mark.asyncio
async def test_telegram_client_get_me(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 999, "is_bot": True, "username": "TestBot"}},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    info = await client.get_me()
    assert info["id"] == 999
    assert info["username"] == "TestBot"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_send_message_html_fallback(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    attempts = []

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        payload = kwargs.get("json", {})
        attempts.append(payload)

        # Fail first attempt with parse error if parse_mode is set
        if payload.get("parse_mode") == "HTML":
            return httpx.Response(
                400,
                json={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"},
                request=req,
            )
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 42, "text": payload.get("text")}},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    res = await client.send_message(chat_id=100, text="<bad unclosed tag")
    assert res["message_id"] == 42
    # Should have retried without parse_mode
    assert len(attempts) == 2
    assert attempts[0].get("parse_mode") == "HTML"
    assert "parse_mode" not in attempts[1]
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_edit_message_not_modified(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message"},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    res = await client.edit_message_text(chat_id=100, message_id=42, text="Same text")
    assert res is None  # Gracefully swallowed
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_send_document(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_data = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_data["url"] = url
        captured_data["data"] = kwargs.get("data")
        captured_data["files"] = kwargs.get("files")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 77, "document": {"file_name": "test.html"}}},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    res = await client.send_document(
        chat_id=123,
        document_content="<html><body>Hello</body></html>",
        filename="report.html",
        caption="📄 Report",
        reply_markup={"inline_keyboard": [[{"text": "Btn", "url": "https://test"}]]},
    )
    assert res["message_id"] == 77
    assert captured_data["data"]["chat_id"] == "123"
    assert captured_data["data"]["caption"] == "📄 Report"
    assert "reply_markup" in captured_data["data"]
    assert "https://test" in captured_data["data"]["reply_markup"]
    assert "document" in captured_data["files"]
    assert captured_data["files"]["document"][0] == "report.html"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_set_my_commands(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_payload = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_payload.update(kwargs.get("json", {}))
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    commands = [{"command": "help", "description": "Show help"}]
    res = await client.set_my_commands(commands)
    assert res is True
    assert captured_payload["commands"] == commands

    menu_res = await client.set_chat_menu_button()
    assert menu_res is True

    # Test answer_callback_query
    captured_payload.clear()
    cb_res = await client.answer_callback_query("query_123", text="Action completed")
    assert cb_res is True
    assert captured_payload["callback_query_id"] == "query_123"
    assert captured_payload["text"] == "Action completed"

    # Test edit_message_reply_markup
    captured_payload.clear()
    markup_res = await client.edit_message_reply_markup(100, 42, reply_markup={"inline_keyboard": []})
    assert markup_res is True

    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_get_file(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_payload = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_payload.update(kwargs.get("json", {}))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "file_id": "v123",
                    "file_unique_id": "vu123",
                    "file_size": 1024,
                    "file_path": "voice/file_0.oga",
                },
            },
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    info = await client.get_file("v123")
    assert info["file_id"] == "v123"
    assert info["file_path"] == "voice/file_0.oga"
    assert captured_payload["file_id"] == "v123"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_download_file(monkeypatch, tmp_path):
    client = TelegramClient("12345:TESTTOKEN")

    async def mock_get(url, **kwargs):
        req = httpx.Request("GET", url)
        assert "voice/file_0.oga" in str(url)
        return httpx.Response(
            200,
            content=b"AUDIO_DATA_BYTES",
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "get", mock_get)
    client._client = mock_client

    dest = tmp_path / "voice" / "downloaded.oga"
    res_path = await client.download_file("voice/file_0.oga", dest)
    assert res_path.is_file()
    assert res_path.read_bytes() == b"AUDIO_DATA_BYTES"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_download_file_by_id(monkeypatch, tmp_path):
    client = TelegramClient("12345:TESTTOKEN")

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "file_id": "v456",
                    "file_unique_id": "uniq_v456",
                    "file_size": 2048,
                    "file_path": "voice/msg.oga",
                },
            },
            request=req,
        )

    async def mock_get(url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"OGG_CONTAINER_PAYLOAD",
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    monkeypatch.setattr(mock_client, "get", mock_get)
    client._client = mock_client

    dest_dir = tmp_path / "voice_storage"
    saved_file, file_info = await client.download_file_by_id("v456", dest_dir, filename_prefix="voice_100")
    assert saved_file.is_file()
    assert saved_file.suffix == ".oga"
    assert "voice_100_uniq_v456" in saved_file.name
    assert saved_file.read_bytes() == b"OGG_CONTAINER_PAYLOAD"
    assert file_info["file_id"] == "v456"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_set_message_reaction(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_payload = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_payload.update(kwargs.get("json", {}))
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    # Add emoji reaction
    res = await client.set_message_reaction(chat_id=123, message_id=456, reaction="👀")
    assert res is True
    assert captured_payload["chat_id"] == 123
    assert captured_payload["message_id"] == 456
    assert captured_payload["reaction"] == [{"type": "emoji", "emoji": "👀"}]

    # Clear reaction
    captured_payload.clear()
    res_clear = await client.set_message_reaction(chat_id=123, message_id=456, reaction=None)
    assert res_clear is True
    assert captured_payload["reaction"] == []

    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_pin_and_unpin_chat_message(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_payload = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_payload.update(kwargs.get("json", {}))
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    # 1. Test pinChatMessage
    pin_res = await client.pin_chat_message(chat_id=100, message_id=500, disable_notification=True)
    assert pin_res is True
    assert captured_payload["chat_id"] == 100
    assert captured_payload["message_id"] == 500
    assert captured_payload["disable_notification"] is True

    # 2. Test unpinChatMessage
    captured_payload.clear()
    unpin_res = await client.unpin_chat_message(chat_id=100, message_id=500)
    assert unpin_res is True
    assert captured_payload["chat_id"] == 100
    assert captured_payload["message_id"] == 500

    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_send_voice(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")
    captured_data = {}

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        captured_data["url"] = url
        captured_data["data"] = kwargs.get("data")
        captured_data["files"] = kwargs.get("files")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 888, "voice": {"duration": 5}}},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    res = await client.send_voice(
        chat_id=123,
        voice=b"OGG_OPUS_BYTES",
        filename="custom_voice.ogg",
        caption="🎙️ Voice note",
        reply_to_message_id=456,
    )
    assert res["message_id"] == 888
    assert captured_data["data"]["chat_id"] == "123"
    assert captured_data["data"]["caption"] == "🎙️ Voice note"
    assert captured_data["data"]["reply_to_message_id"] == "456"
    assert "voice" in captured_data["files"]
    assert captured_data["files"]["voice"][0] == "custom_voice.ogg"
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_get_updates_raise_on_error(monkeypatch):
    client = TelegramClient("12345:TESTTOKEN")

    async def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(
            409,
            json={"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"},
            request=req,
        )

    mock_client = httpx.AsyncClient()
    monkeypatch.setattr(mock_client, "post", mock_post)
    client._client = mock_client

    # Default backward-compatible mode swallows error and returns []
    assert await client.get_updates() == []

    # raise_on_error=True surfaces TelegramAPIError so caller backoff loop triggers
    with pytest.raises(TelegramAPIError) as exc_info:
        await client.get_updates(raise_on_error=True)
    assert exc_info.value.error_code == 409

    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_blank_httpx_error_logging_and_idempotent_retry(monkeypatch, caplog):
    import logging
    from unittest.mock import AsyncMock
    import gateway.telegram_client as tc_mod

    monkeypatch.setattr(tc_mod.asyncio, "sleep", AsyncMock())
    client = TelegramClient("12345:TESTTOKEN")
    mock_client = httpx.AsyncClient()
    client._client = mock_client

    # 1. Idempotent editMessageText retries once on blank-message ReadTimeout and succeeds
    edit_calls = 0

    async def mock_post_edit(url, **kwargs):
        nonlocal edit_calls
        edit_calls += 1
        req = httpx.Request("POST", url)
        if edit_calls == 1:
            raise httpx.ReadTimeout("", request=req)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 10}}, request=req)

    monkeypatch.setattr(mock_client, "post", mock_post_edit)
    with caplog.at_level(logging.WARNING, logger="gateway.telegram_client"):
        res = await client.edit_message_text(chat_id=100, message_id=10, text="Updated")
    assert res["message_id"] == 10
    assert edit_calls == 2
    assert any("Transient HTTP error calling editMessageText (ReadTimeout)" in r.message for r in caplog.records)

    # 2. Idempotent setMessageReaction retries once then logs non-blank RemoteProtocolError
    caplog.clear()
    react_calls = 0

    async def mock_post_react(url, **kwargs):
        nonlocal react_calls
        react_calls += 1
        req = httpx.Request("POST", url)
        raise httpx.RemoteProtocolError("", request=req)

    monkeypatch.setattr(mock_client, "post", mock_post_react)
    with caplog.at_level(logging.WARNING, logger="gateway.telegram_client"):
        ok = await client.set_message_reaction(chat_id=100, message_id=10, reaction="👀")
    assert ok is False
    assert react_calls == 2
    assert any("HTTP request error calling setMessageReaction: RemoteProtocolError" in r.message for r in caplog.records)

    # 3. Non-idempotent sendMessage does NOT retry and logs non-blank ReadTimeout
    caplog.clear()
    send_calls = 0

    async def mock_post_send(url, **kwargs):
        nonlocal send_calls
        send_calls += 1
        req = httpx.Request("POST", url)
        raise httpx.ReadTimeout("", request=req)

    monkeypatch.setattr(mock_client, "post", mock_post_send)
    with caplog.at_level(logging.ERROR, logger="gateway.telegram_client"):
        with pytest.raises(httpx.ReadTimeout):
            await client.send_message(chat_id=100, text="Hello")
    assert send_calls == 1
    assert any("HTTP request error calling sendMessage: ReadTimeout" in r.message for r in caplog.records)

    await client.close()






