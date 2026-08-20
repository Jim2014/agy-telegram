"""Unit tests for gateway.webapp_server."""

import asyncio
import httpx
import pytest
from gateway.webapp_server import WebAppServer


@pytest.mark.asyncio
async def test_webapp_server_lifecycle_and_endpoints():
    server = WebAppServer(host="127.0.0.1", port=8999, base_url="http://127.0.0.1:8999")
    await server.start()

    try:
        async with httpx.AsyncClient() as client:
            # 1. Health check
            resp = await client.get("http://127.0.0.1:8999/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["docs_count"] == 0

            # 2. Register document
            doc_url = server.register_document(
                doc_id="report_001",
                content="<!DOCTYPE html><html><head><title>Test</title></head><body><h1>Report Body</h1></body></html>",
                filename="test_report.html",
            )
            assert doc_url == "http://127.0.0.1:8999/view/report_001/test_report.html"

            # 3. Retrieve document
            view_resp = await client.get("http://127.0.0.1:8999/view/report_001/test_report.html")
            assert view_resp.status_code == 200
            assert "telegram-web-app.js" in view_resp.text
            assert "Report Body" in view_resp.text

            # 4. 404 for missing document
            missing_resp = await client.get("http://127.0.0.1:8999/view/missing_doc/report.html")
            assert missing_resp.status_code == 404

    finally:
        await server.stop()


def test_webapp_server_document_fragment_wrapping():
    server = WebAppServer(port=8998)
    url = server.register_document("frag_1", "<div>Snippet</div>", "frag.html")
    assert "/view/frag_1/frag.html" in url
    doc = server.get_document("frag_1")
    assert doc is not None
    assert "<!DOCTYPE html>" in doc.content
    assert "telegram-web-app.js" in doc.content
    assert "<div>Snippet</div>" in doc.content
