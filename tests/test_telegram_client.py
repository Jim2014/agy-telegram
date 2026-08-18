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
