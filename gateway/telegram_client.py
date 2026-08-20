"""Async Telegram Bot API client using httpx."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class TelegramAPIError(Exception):
    """Exception raised when Telegram API returns an error."""

    def __init__(self, error_code: int, description: str) -> None:
        self.error_code = error_code
        self.description = description
        super().__init__(f"Telegram API Error {error_code}: {description}")


class TelegramClient:
    """Lightweight async Telegram Bot API client."""

    def __init__(self, bot_token: str, base_url: str = "https://api.telegram.org") -> None:
        self.bot_token = bot_token
        self.base_url = f"{base_url.rstrip('/')}/bot{bot_token}"
        self.file_base_url = f"{base_url.rstrip('/')}/file/bot{bot_token}"
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create shared httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))
        return self._client

    async def close(self) -> None:
        """Close underlying HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _post(self, method: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Execute a POST request to Telegram Bot API with error handling."""
        client = await self.get_client()
        url = f"{self.base_url}/{method}"
        kwargs: Dict[str, Any] = {"json": payload}
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            resp = await client.post(url, **kwargs)
            data = resp.json()
        except httpx.RequestError as e:
            logger.error("HTTP request error calling %s: %s", method, e)
            raise

        if not data.get("ok"):
            err_code = data.get("error_code", resp.status_code)
            desc = data.get("description", "Unknown error")

            # Handle rate limits
            if err_code == 429:
                params = data.get("parameters", {})
                retry_after = params.get("retry_after", 1)
                logger.warning("Telegram rate limit hit (429). Retry after %ds", retry_after)
                await asyncio.sleep(retry_after)
                # Retry once
                resp = await client.post(url, **kwargs)
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", {})

            raise TelegramAPIError(err_code, desc)

        return data.get("result", {})

    async def get_me(self) -> Dict[str, Any]:
        """Test bot token and get bot metadata."""
        return await self._post("getMe", {})

    async def get_file(self, file_id: str) -> Dict[str, Any]:
        """Get file metadata from Telegram (including file_path)."""
        return await self._post("getFile", {"file_id": file_id})

    async def download_file(
        self,
        file_path: str,
        destination: Path | str,
        timeout: float = 60.0,
    ) -> Path:
        """Download a file from Telegram by its file_path and save locally."""
        client = await self.get_client()
        url = f"{self.file_base_url}/{file_path.lstrip('/')}"
        dest_path = Path(destination).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)
            return dest_path
        except httpx.RequestError as e:
            logger.error("HTTP error downloading file %s: %s", file_path, e)
            raise

    async def download_file_by_id(
        self,
        file_id: str,
        destination_dir: Path | str,
        filename_prefix: str = "voice",
    ) -> Tuple[Path, Dict[str, Any]]:
        """Fetch file metadata and download file to destination directory."""
        file_info = await self.get_file(file_id)
        remote_path = file_info.get("file_path")
        if not remote_path:
            raise ValueError(f"No file_path returned by Telegram for file_id {file_id}")

        ext = Path(remote_path).suffix or ".oga"
        file_unique_id = file_info.get("file_unique_id", "file")
        filename = f"{filename_prefix}_{file_unique_id}_{int(time.time())}{ext}"
        dest = Path(destination_dir) / filename
        saved_path = await self.download_file(remote_path, dest)
        return saved_path, file_info

    async def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 30,
    ) -> List[Dict[str, Any]]:
        """Long poll updates from Telegram."""
        payload: Dict[str, Any] = {
            "timeout": timeout,
            "limit": limit,
            "allowed_updates": ["message", "edited_message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset

        # Set HTTP read timeout slightly higher than long poll timeout
        http_timeout = float(timeout + 15)
        try:
            result = await self._post("getUpdates", payload, timeout=http_timeout)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.warning("getUpdates failed: %s", e)
            return []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send message with automatic fallback to plain text if formatting fails."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            return await self._post("sendMessage", payload)
        except TelegramAPIError as e:
            # If parse error occurred, retry as plain text without parse_mode
            if parse_mode and ("can't parse entities" in e.description.lower() or "tag" in e.description.lower()):
                logger.debug("HTML parse failed for sendMessage, retrying plain text: %s", e.description)
                plain_payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                return await self._post("sendMessage", plain_payload)
            raise

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Edit message text with no-op handling for unchanged content and plain-text fallback."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            return await self._post("editMessageText", payload)
        except TelegramAPIError as e:
            # Telegram returns 400 if text is exactly identical; safe to ignore
            if "message is not modified" in e.description.lower():
                return None
            # If entity parse failed, retry with plain text
            if parse_mode and ("can't parse entities" in e.description.lower() or "tag" in e.description.lower()):
                logger.debug("HTML parse failed for editMessageText, retrying plain text: %s", e.description)
                plain_payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                try:
                    return await self._post("editMessageText", plain_payload)
                except TelegramAPIError as e2:
                    if "message is not modified" in e2.description.lower():
                        return None
                    raise e2
            raise

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Edit only the reply markup of a message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            return await self._post("editMessageReplyMarkup", payload)
        except TelegramAPIError as e:
            if "message is not modified" in e.description.lower():
                return None
            raise

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge a callback query from an inline button press."""
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        try:
            res = await self._post("answerCallbackQuery", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to answer callback query %s: %s", callback_query_id, e)
            return False

    async def send_document(
        self,
        chat_id: int,
        document_content: bytes | str | Path,
        filename: str = "document.html",
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send document file to Telegram via multipart/form-data upload."""
        import json

        client = await self.get_client()
        url = f"{self.base_url}/sendDocument"

        if isinstance(document_content, str):
            file_bytes = document_content.encode("utf-8")
        elif isinstance(document_content, Path):
            file_bytes = document_content.read_bytes()
            if not filename or filename == "document.html":
                filename = document_content.name
        else:
            file_bytes = document_content

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_to_message_id:
            data["reply_to_message_id"] = str(reply_to_message_id)
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        mime_type = "text/html"
        if filename.endswith(".diff") or filename.endswith(".patch"):
            mime_type = "text/x-diff"
        elif filename.endswith(".json"):
            mime_type = "application/json"
        elif filename.endswith(".txt") or filename.endswith(".md"):
            mime_type = "text/plain"

        files = {"document": (filename, file_bytes, mime_type)}

        try:
            resp = await client.post(url, data=data, files=files, timeout=60.0)
            res_json = resp.json()
        except httpx.RequestError as e:
            logger.error("HTTP request error calling sendDocument: %s", e)
            raise

        if not res_json.get("ok"):
            err_code = res_json.get("error_code", resp.status_code)
            desc = res_json.get("description", "Unknown error")
            if parse_mode and ("can't parse entities" in desc.lower() or "tag" in desc.lower()):
                logger.debug("HTML parse failed for sendDocument, retrying plain text: %s", desc)
                data.pop("parse_mode", None)
                resp = await client.post(url, data=data, files=files, timeout=60.0)
                res_json = resp.json()
                if res_json.get("ok"):
                    return res_json.get("result", {})
            raise TelegramAPIError(err_code, desc)

        return res_json.get("result", {})

    async def send_voice(
        self,
        chat_id: int,
        voice: bytes | str | Path,
        filename: str = "voice.ogg",
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
        duration: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send native Telegram voice note (.ogg opus format) via multipart/form-data upload."""
        import json

        client = await self.get_client()
        url = f"{self.base_url}/sendVoice"

        if isinstance(voice, Path):
            with open(voice, "rb") as f:
                content_bytes = f.read()
        elif isinstance(voice, str):
            if os.path.isfile(voice):
                with open(voice, "rb") as f:
                    content_bytes = f.read()
            else:
                content_bytes = voice.encode("utf-8")
        else:
            content_bytes = voice

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
            if parse_mode:
                data["parse_mode"] = parse_mode
        if duration is not None:
            data["duration"] = str(duration)
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(reply_to_message_id)
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)

        files = {"voice": (filename, content_bytes, "audio/ogg")}

        try:
            resp = await client.post(url, data=data, files=files, timeout=60.0)
            res_json = resp.json()
        except Exception as e:
            logger.error("HTTP error calling sendVoice: %s", e)
            raise

        if not res_json.get("ok"):
            err_code = res_json.get("error_code", 500)
            desc = res_json.get("description", "Unknown error")
            logger.error("Telegram sendVoice failed: %d - %s", err_code, desc)
            raise TelegramAPIError(err_code, desc)

        return res_json.get("result", {})

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> bool:
        """Send chat action (e.g. typing, upload_document)."""
        try:
            await self._post("sendChatAction", {"chat_id": chat_id, "action": action})
            return True
        except Exception:
            return False

    async def set_my_commands(
        self,
        commands: List[Dict[str, str]],
        scope: Optional[Dict[str, Any]] = None,
        language_code: Optional[str] = None,
    ) -> bool:
        """Register the list of bot commands in Telegram to display the command menu."""
        payload: Dict[str, Any] = {"commands": commands}
        if scope:
            payload["scope"] = scope
        if language_code:
            payload["language_code"] = language_code
        try:
            res = await self._post("setMyCommands", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to set bot commands: %s", e)
            return False

    async def get_my_commands(
        self,
        scope: Optional[Dict[str, Any]] = None,
        language_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get the current list of registered bot commands."""
        payload: Dict[str, Any] = {}
        if scope:
            payload["scope"] = scope
        if language_code:
            payload["language_code"] = language_code
        try:
            res = await self._post("getMyCommands", payload)
            return res if isinstance(res, list) else []
        except Exception as e:
            logger.warning("Failed to get bot commands: %s", e)
            return []

    async def delete_my_commands(
        self,
        scope: Optional[Dict[str, Any]] = None,
        language_code: Optional[str] = None,
    ) -> bool:
        """Delete registered bot commands."""
        payload: Dict[str, Any] = {}
        if scope:
            payload["scope"] = scope
        if language_code:
            payload["language_code"] = language_code
        try:
            res = await self._post("deleteMyCommands", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to delete bot commands: %s", e)
            return False

    async def set_chat_menu_button(
        self,
        chat_id: Optional[int] = None,
        menu_button: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Change the bot's menu button in Telegram chats."""
        payload: Dict[str, Any] = {}
        if chat_id is not None:
            payload["chat_id"] = chat_id
        if menu_button:
            payload["menu_button"] = menu_button
        else:
            payload["menu_button"] = {"type": "commands"}
        try:
            res = await self._post("setChatMenuButton", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to set chat menu button: %s", e)
            return False

    async def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: Optional[str] = None,
        is_big: bool = False,
    ) -> bool:
        """Add emoji reaction to a message (Telegram Bot API 7.0+). Pass reaction=None to clear."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reaction:
            payload["reaction"] = [{"type": "emoji", "emoji": reaction}]
        else:
            payload["reaction"] = []
        if is_big:
            payload["is_big"] = is_big
        try:
            res = await self._post("setMessageReaction", payload)
            return bool(res)
        except Exception as e:
            logger.debug("Failed to set message reaction '%s' for msg %s: %s", reaction, message_id, e)
            return False

    async def pin_chat_message(
        self,
        chat_id: int,
        message_id: int,
        disable_notification: bool = True,
    ) -> bool:
        """Pin a message at the top of the chat."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": disable_notification,
        }
        try:
            res = await self._post("pinChatMessage", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to pin message %s in chat %s: %s", message_id, chat_id, e)
            return False

    async def unpin_chat_message(
        self,
        chat_id: int,
        message_id: Optional[int] = None,
    ) -> bool:
        """Unpin a message from the top of the chat."""
        payload: Dict[str, Any] = {"chat_id": chat_id}
        if message_id is not None:
            payload["message_id"] = message_id
        try:
            res = await self._post("unpinChatMessage", payload)
            return bool(res)
        except Exception as e:
            logger.warning("Failed to unpin message in chat %s: %s", chat_id, e)
            return False


if __name__ == "__main__":
    async def main() -> None:
        client = TelegramClient("dummy_token")
        print("TelegramClient initialized with base_url:", client.base_url)
        await client.close()
        print("TelegramClient self-test passed!")

    asyncio.run(main())
