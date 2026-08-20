"""Lightweight zero-dependency async WebApp HTTP server for Telegram Mini App viewing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TELEGRAM_WEBAPP_JS_INJECTION = """
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
  try {
    if (window.Telegram && window.Telegram.WebApp) {
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
      if (window.Telegram.WebApp.themeParams) {
        var tp = window.Telegram.WebApp.themeParams;
        if (tp.bg_color) document.documentElement.style.setProperty('--bg', tp.bg_color);
        if (tp.text_color) document.documentElement.style.setProperty('--text', tp.text_color);
        if (tp.secondary_bg_color) document.documentElement.style.setProperty('--card-bg', tp.secondary_bg_color);
        if (tp.button_color) document.documentElement.style.setProperty('--primary', tp.button_color);
      }
    }
  } catch (e) {
    console.warn("Telegram WebApp init error:", e);
  }
</script>
"""


@dataclass
class WebAppDoc:
    """Cached document served to Telegram WebApp."""

    doc_id: str
    filename: str
    content: str
    created_at: float = field(default_factory=time.time)
    content_type: str = "text/html; charset=utf-8"


class WebAppServer:
    """Async embedded HTTP server for serving Telegram Mini App Webviews."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765, base_url: str = "") -> None:
        self.host = host
        self.port = port
        self.base_url = base_url.rstrip("/") if base_url else f"http://127.0.0.1:{port}"
        self._server: Optional[asyncio.AbstractServer] = None
        self._docs: Dict[str, WebAppDoc] = {}
        self._is_running = False

    def register_document(self, doc_id: str, content: str, filename: str = "report.html") -> str:
        """Register an HTML document and return its viewing URL."""
        # Inject WebApp JS SDK if not present
        if "<!doctype html" in content.lower() or "<html" in content.lower():
            if "telegram-web-app.js" not in content:
                if "</head>" in content:
                    content = content.replace("</head>", f"{TELEGRAM_WEBAPP_JS_INJECTION}\n</head>", 1)
                elif "<body" in content:
                    content = re.sub(r"(<body[^>]*>)", r"\1\n" + TELEGRAM_WEBAPP_JS_INJECTION, content, count=1, flags=re.IGNORECASE)
                else:
                    content = TELEGRAM_WEBAPP_JS_INJECTION + "\n" + content
        else:
            content = f"<!DOCTYPE html><html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">{TELEGRAM_WEBAPP_JS_INJECTION}</head><body>{content}</body></html>"

        doc = WebAppDoc(doc_id=doc_id, filename=filename, content=content)
        self._docs[doc_id] = doc
        return f"{self.base_url}/view/{doc_id}/{filename}"

    def get_document(self, doc_id: str) -> Optional[WebAppDoc]:
        """Retrieve a registered document by ID."""
        return self._docs.get(doc_id)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle incoming HTTP requests asynchronously."""
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            line_str = request_line.decode("utf-8", errors="ignore").strip()
            parts = line_str.split()
            if len(parts) < 2:
                writer.close()
                return

            method, path = parts[0].upper(), parts[1]

            # Consume headers until blank line
            while True:
                h_line = await reader.readline()
                if not h_line or h_line in (b"\r\n", b"\n"):
                    break

            if method != "GET":
                body = b"Method Not Allowed"
                resp = f"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("utf-8") + body
                writer.write(resp)
                await writer.drain()
                writer.close()
                return

            clean_path = path.split("?")[0]

            if clean_path in ("/health", "/healthz"):
                body = f'{{"status": "ok", "docs_count": {len(self._docs)}}}'.encode("utf-8")
                resp = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Access-Control-Allow-Origin: *\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("utf-8") + body
                writer.write(resp)
                await writer.drain()
                writer.close()
                return

            # Route: /view/{doc_id} or /view/{doc_id}/{filename}
            match = re.match(r"^/view/([a-zA-Z0-9_\-]+)(?:/.*)?$", clean_path)
            if match:
                doc_id = match.group(1)
                doc = self.get_document(doc_id)
                if doc:
                    body_bytes = doc.content.encode("utf-8")
                    resp = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"Content-Type: {doc.content_type}\r\n"
                        f"Content-Length: {len(body_bytes)}\r\n"
                        f"Access-Control-Allow-Origin: *\r\n"
                        f"Connection: close\r\n\r\n"
                    ).encode("utf-8") + body_bytes
                    writer.write(resp)
                    await writer.drain()
                    writer.close()
                    return

            # 404 Not Found
            body = b"<!DOCTYPE html><html><body><h1>404 Not Found</h1><p>Document not found or expired.</p></body></html>"
            resp = (
                f"HTTP/1.1 404 Not Found\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8") + body
            writer.write(resp)
            await writer.drain()
            writer.close()

        except Exception as e:
            logger.debug("Error handling WebApp request: %s", e)
            try:
                writer.close()
            except Exception:
                pass

    async def start(self) -> None:
        """Start the async HTTP server."""
        if self._is_running:
            return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self._is_running = True
        logger.info("WebApp server listening on http://%s:%d (Base URL: %s)", self.host, self.port, self.base_url)

    async def stop(self) -> None:
        """Stop the async HTTP server."""
        if not self._is_running or not self._server:
            return
        self._server.close()
        await self._server.wait_closed()
        self._is_running = False
        logger.info("WebApp server stopped.")


if __name__ == "__main__":
    async def main() -> None:
        server = WebAppServer(port=8765)
        url = server.register_document("test1", "<h1>Hello WebApp</h1>", "test.html")
        print("Registered doc URL:", url)
        await server.start()
        print("Server running for 1 second...")
        await asyncio.sleep(1)
        await server.stop()
        print("WebAppServer self-test passed!")

    asyncio.run(main())
