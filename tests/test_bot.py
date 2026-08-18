"""Unit tests for gateway.bot command routing, steering, file sending, and streaming orchestration."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from gateway.agy_bridge import AgyBridge, AgyEvent
from gateway.bot import AgyTelegramBot
from gateway.config import Config


@pytest.fixture
def mock_bot(tmp_path):
    cfg = Config(
        bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        allowed_chat_ids={100, 200},
        agy_working_dir=str(tmp_path),
        stream_throttle_seconds=0.01,
        sessions_file=str(tmp_path / "bot_sessions.json"),
    )
    bot = AgyTelegramBot(cfg)
    bot.client.send_message = AsyncMock(return_value={"message_id": 999})
    bot.client.edit_message_text = AsyncMock(return_value={"message_id": 999})
    bot.client.send_document = AsyncMock(return_value={"message_id": 1000})
    bot.client.send_chat_action = AsyncMock(return_value=True)
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
    assert "/steer" in mock_bot.client.send_message.call_args[1]["text"]
    assert "/file" in mock_bot.client.send_message.call_args[1]["text"]
    assert "/export" in mock_bot.client.send_message.call_args[1]["text"]

    # /status
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/status", "message_id": 2}})
    assert "Status" in mock_bot.client.send_message.call_args[1]["text"]


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
async def test_bot_new_project_command(mock_bot, tmp_path):
    p1 = str(tmp_path / "proj1")
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
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj1 {p1}", "message_id": 12}})
    assert "created and activated" in mock_bot.client.send_message.call_args[1]["text"]
    session = mock_bot.session_manager.get_session(100)
    assert session.active_project == "proj1"
    assert session.active_path() == str(Path(p1).resolve())

    # /new proj1 again -> treated as /open
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj1 {p1}_other", "message_id": 13}})
    assert "already exists" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).active_project == "proj1"


@pytest.mark.asyncio
async def test_bot_open_and_projects_command(mock_bot, tmp_path):
    pa = str(tmp_path / "app_a")
    pb = str(tmp_path / "app_b")
    # Create two projects
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new app_a {pa}", "message_id": 20}})
    mock_bot.session_manager.set_conversation_id(100, "conv-app-a")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new app_b {pb}", "message_id": 21}})
    mock_bot.session_manager.set_conversation_id(100, "conv-app-b")

    # /projects
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/projects", "message_id": 22}})
    projects_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "app_a" in projects_text
    assert "app_b" in projects_text
    assert "/open app_a" in projects_text
    assert "/open app_b" in projects_text

    # /open app_a
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/open app_a", "message_id": 23}})
    assert "Opened project" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).active_project == "app_a"
    assert mock_bot.session_manager.get_session(100).conversation_id == "conv-app-a"


@pytest.mark.asyncio
async def test_bot_delete_and_reset_command(mock_bot, tmp_path):
    px = str(tmp_path / "proj_x")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj_x {px}", "message_id": 30}})
    mock_bot.session_manager.set_conversation_id(100, "conv-to-reset")

    # /reset
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/reset", "message_id": 31}})
    assert "Session reset" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).conversation_id is None

    # /delete
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/delete proj_x", "message_id": 32}})
    assert "deleted successfully" in mock_bot.client.send_message.call_args[1]["text"]
    assert "proj_x" not in mock_bot.session_manager.get_session(100).projects


@pytest.mark.asyncio
async def test_bot_dir_command(mock_bot, tmp_path):
    valid_dir = str(tmp_path / "custom_dir")
    Path(valid_dir).mkdir(parents=True, exist_ok=True)

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/dir {valid_dir}", "message_id": 40}})
    session = mock_bot.session_manager.get_session(100)
    assert session.active_path() == valid_dir


@pytest.mark.asyncio
async def test_bot_file_and_export_commands(mock_bot, tmp_path):
    # Setup test file in project workspace
    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    test_file = proj_dir / "sample.txt"
    test_file.write_text("Hello from workspace file")

    mock_bot.session_manager.create_project(100, "my_project", str(proj_dir))

    # Test /file sample.txt
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/file sample.txt", "message_id": 60}})
    mock_bot.client.send_document.assert_called_once()
    call_args = mock_bot.client.send_document.call_args[1]
    assert call_args["filename"] == "sample.txt"
    assert call_args["chat_id"] == 100

    # Test /export
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/export", "message_id": 61}})
    mock_bot.client.send_document.assert_called_once()
    export_args = mock_bot.client.send_document.call_args[1]
    assert "session_report" in export_args["filename"]


@pytest.mark.asyncio
async def test_bot_unauthorized_chat(mock_bot):
    await mock_bot.process_update({"message": {"chat": {"id": 999}, "text": "Hello", "message_id": 1}})
    mock_bot.client.send_message.assert_called_with(
        chat_id=999,
        text="⛔ <b>Access Denied</b>\nThis bot is private and your chat ID is not authorized.",
    )


@pytest.mark.asyncio
async def test_bot_prompt_streaming(mock_bot, monkeypatch):
    async def mock_run_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-streaming-123")
        yield AgyEvent(type="tool", tool_name="run_command", tool_state="ACTIVE", tool_info={"parameters": {"CommandLine": "ls"}})
        yield AgyEvent(type="tool", tool_name="run_command", tool_state="DONE", tool_info={"parameters": {"CommandLine": "ls"}})
        yield AgyEvent(type="text_delta", text_delta="Answer is 42")
        yield AgyEvent(type="result", response="Answer is 42", duration_seconds=1.2, usage={"total_tokens": 500, "thinking_tokens": 50})

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
async def test_bot_prompt_auto_file_export_for_diff(mock_bot, monkeypatch):
    diff_content = """```diff
diff --git a/gateway/bot.py b/gateway/bot.py
index 1234..5678 100644
--- a/gateway/bot.py
+++ b/gateway/bot.py
@@ -1,5 +1,6 @@
+import html
+print("added document export")
-print("old line")
""" + "more content line\n" * 40 + "```"

    async def mock_diff_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-diff-999")
        yield AgyEvent(type="text_delta", text_delta=diff_content)
        yield AgyEvent(type="result", response=diff_content, duration_seconds=2.0, usage={"total_tokens": 1200})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_diff_streaming)

    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Show me the git diff as a file", "message_id": 70}})

    # Verify document was automatically generated and uploaded
    mock_bot.client.send_document.assert_called_once()
    doc_args = mock_bot.client.send_document.call_args[1]
    assert "diff_" in doc_args["filename"]
    assert "<!DOCTYPE html>" in doc_args["document_content"]
    assert "diff-add" in doc_args["document_content"]


@pytest.mark.asyncio
async def test_bot_prompt_explicit_file_export_direct_html(mock_bot, monkeypatch):
    proto_response = """Here is the diff analysis for your review:

[AGY_FILE]
filename: feature_diff.html
title: Feature Implementation Diff
---
<!DOCTYPE html>
<html>
<head><title>Feature Diff</title></head>
<body>
<h1>Feature Implementation Diff</h1>
<pre class="diff"><span class="diff-add">+ def new_feature(): return True</span></pre>
</body>
</html>
[END_AGY_FILE]

Please review the attached HTML file."""

    async def mock_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-explicit-1")
        yield AgyEvent(type="text_delta", text_delta=proto_response)
        yield AgyEvent(type="result", response=proto_response, duration_seconds=1.5, usage={"total_tokens": 800})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming)

    mock_bot.client.send_document.reset_mock()
    mock_bot.client.edit_message_text.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "implement feature", "message_id": 80}})

    # Verify document was uploaded with exact filename and direct HTML
    mock_bot.client.send_document.assert_called_once()
    doc_args = mock_bot.client.send_document.call_args[1]
    assert doc_args["filename"] == "feature_diff.html"
    assert "<!DOCTYPE html>" in doc_args["document_content"]
    assert "Feature Implementation Diff" in doc_args["document_content"]
    assert "diff-add" in doc_args["document_content"]

    # Verify message text has protocol stripped and contains preview + notice
    assert mock_bot.client.edit_message_text.called
    final_text = mock_bot.client.edit_message_text.call_args[1]["text"]
    assert "[AGY_FILE]" not in final_text
    assert "[END_AGY_FILE]" not in final_text
    assert "Here is the diff analysis for your review:" in final_text
    assert "Please review the attached HTML file." in final_text
    assert "已生成完整报告文件并发送至下方" in final_text


@pytest.mark.asyncio
async def test_bot_prompt_explicit_file_export_raw_html(mock_bot, monkeypatch):
    raw_html_payload = """[AGY_FILE]
filename: dashboard.html
title: System Dashboard
---
<!DOCTYPE html><html><head><title>System</title></head><body><h1>Dashboard OK</h1></body></html>
[END_AGY_FILE]"""

    async def mock_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-explicit-2")
        yield AgyEvent(type="result", response=raw_html_payload, duration_seconds=1.0, usage={"total_tokens": 400})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming)

    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "build dashboard", "message_id": 81}})

    mock_bot.client.send_document.assert_called_once()
    doc_args = mock_bot.client.send_document.call_args[1]
    assert doc_args["filename"] == "dashboard.html"
    assert "Dashboard OK" in doc_args["document_content"]
    assert '<meta name="viewport"' in doc_args["document_content"]


@pytest.mark.asyncio
async def test_bot_prompt_explicit_file_export_file_path(mock_bot, monkeypatch, tmp_path):
    proj_dir = tmp_path / "workspace_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    report_file = proj_dir / "coverage_report.html"
    report_file.write_text("<!DOCTYPE html><html><body><h1>Coverage 98%</h1></body></html>")

    mock_bot.session_manager.create_project(100, "workspace_proj", str(proj_dir))

    path_payload = """[AGY_FILE]
filename: coverage.html
title: Test Coverage
---
coverage_report.html
[END_AGY_FILE]"""

    async def mock_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-explicit-3")
        yield AgyEvent(type="result", response=path_payload, duration_seconds=1.0, usage={"total_tokens": 300})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming)

    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "run tests and export coverage", "message_id": 82}})

    mock_bot.client.send_document.assert_called_once()
    doc_args = mock_bot.client.send_document.call_args[1]
    assert doc_args["filename"] == "coverage.html"
    assert "Coverage 98%" in doc_args["document_content"]


@pytest.mark.asyncio
async def test_bot_prompt_explicit_file_export_file_not_found(mock_bot, monkeypatch):
    missing_payload = """[AGY_FILE]
filename: missing.html
title: Missing Report
---
non_existent_file.html
[END_AGY_FILE]"""

    async def mock_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-explicit-4")
        yield AgyEvent(type="result", response=missing_payload, duration_seconds=1.0, usage={"total_tokens": 200})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming)

    mock_bot.client.send_document.reset_mock()
    mock_bot.client.edit_message_text.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "show missing", "message_id": 83}})

    # Document should NOT be sent for missing file, and bot must not crash
    mock_bot.client.send_document.assert_not_called()
    assert mock_bot.client.edit_message_text.called
    final_text = mock_bot.client.edit_message_text.call_args[1]["text"]
    assert "导出文件失败" in final_text or "File not found" in final_text


@pytest.mark.asyncio
async def test_bot_prompt_multiple_file_export_blocks(mock_bot, monkeypatch):
    multi_payload = """Here are the results:

[AGY_FILE]
filename: diff_report.html
title: Diff Report
---
<!DOCTYPE html><html><body><h1>Diff Section</h1><code>+ new code line</code></body></html>
[END_AGY_FILE]

[AGY_FILE]
filename: test_results.html
title: Test Results
---
<!DOCTYPE html><html><body><h1>Test Suite</h1><p>Test A: PASS</p></body></html>
[END_AGY_FILE]

Summary: 2 files exported."""

    async def mock_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-explicit-5")
        yield AgyEvent(type="result", response=multi_payload, duration_seconds=2.0, usage={"total_tokens": 1500})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming)

    mock_bot.client.send_document.reset_mock()
    mock_bot.client.edit_message_text.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "export multiple files", "message_id": 84}})

    # Verify both files are sent
    assert mock_bot.client.send_document.call_count == 2
    sent_filenames = [call[1]["filename"] for call in mock_bot.client.send_document.call_args_list]
    assert "diff_report.html" in sent_filenames
    assert "test_results.html" in sent_filenames

    # Check edit text notice
    final_text = mock_bot.client.edit_message_text.call_args[1]["text"]
    assert "已生成 2 个报告文件并发送至下方" in final_text
    assert "[AGY_FILE]" not in final_text


@pytest.mark.asyncio
async def test_bot_steer_command_and_midflight_steering(mock_bot):
    mock_bot.session_manager.create_project(100, "test_proj", "/tmp")

    # 1. /steer when idle
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/steer add comments", "message_id": 50}})
    assert "No task is currently running" in mock_bot.client.send_message.call_args[1]["text"]

    # 2. Simulate running bridge
    mock_bridge = AsyncMock()
    mock_bridge.steer = AsyncMock(return_value=True)
    mock_bridge.is_running = lambda: True
    mock_bot.active_bridges[100] = mock_bridge
    mock_bot.session_manager.set_busy(100, True)

    # /steer with instruction
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/steer use async", "message_id": 51}})
    mock_bridge.steer.assert_called_with("use async")
    assert "Steering instruction sent" in mock_bot.client.send_message.call_args[1]["text"]

    # 3. Regular text sent while busy automatically steers!
    mock_bridge.steer.reset_mock()
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "make it faster", "message_id": 52}})
    mock_bridge.steer.assert_called_with("make it faster")
    assert "Steered into active session" in mock_bot.client.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_stop_command(mock_bot):
    # When no task is running
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/stop", "message_id": 20}})
    mock_bot.client.send_message.assert_called_with(
        chat_id=100,
        text="ℹ️ No active task is currently running.",
    )

    # When a task is running
    mock_bridge = AsyncMock()
    mock_bridge.stop = AsyncMock()
    mock_bot.active_bridges[100] = mock_bridge
    mock_bot.session_manager.set_busy(100, True)

    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/stop", "message_id": 21}})
    mock_bridge.stop.assert_called_once()
    assert mock_bot.session_manager.is_busy(100) is False
    assert "interrupted" in mock_bot.client.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_streaming_telegram_network_error_resilience(mock_bot, monkeypatch):
    """Verify that httpx.ReadError during streaming edits does NOT crash the bot."""
    import httpx

    async def mock_run_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-test-resilience")
        yield AgyEvent(type="text_delta", text_delta="chunk 1")
        yield AgyEvent(type="text_delta", text_delta="chunk 2")
        yield AgyEvent(type="result", response="Final answer after network glitch", duration_seconds=1.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_run_streaming)

    # edit_message_text raises httpx.ReadError
    mock_bot.client.edit_message_text = AsyncMock(side_effect=httpx.ReadError("Connection reset by peer"))

    # Must complete cleanly without raising
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Do work", "message_id": 90}})

    session = mock_bot.session_manager.get_session(100)
    assert session.is_busy is False
    assert session.conversation_id == "conv-test-resilience"


@pytest.mark.asyncio
async def test_bot_initial_placeholder_network_error_resilience(mock_bot, monkeypatch):
    """Verify that placeholder send_message failure does not crash agy execution."""
    import httpx

    async def mock_run_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-init-fail")
        yield AgyEvent(type="result", response="Result delivered despite placeholder failure", duration_seconds=0.5)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_run_streaming)

    # Initial send_message fails with ConnectError, subsequent fallback send_message succeeds
    call_count = 0

    async def mock_send(chat_id, text, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Initial connection timed out")
        return {"message_id": 1001, "text": text}

    mock_bot.client.send_message = AsyncMock(side_effect=mock_send)

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Calculate 1+1", "message_id": 91}})

    session = mock_bot.session_manager.get_session(100)
    assert session.is_busy is False
    assert session.conversation_id == "conv-init-fail"
    # Fallback final result was attempted
    assert call_count >= 2


@pytest.mark.asyncio
async def test_bot_slash_commands_network_failure_resilience(mock_bot):
    """Verify that Telegram network failure during slash commands does not raise."""
    import httpx

    mock_bot.client.send_message = AsyncMock(side_effect=httpx.ReadError("Telegram read timeout"))
    mock_bot.client.send_document = AsyncMock(side_effect=httpx.ReadError("Telegram upload error"))

    # Test various slash commands under network failure
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/help", "message_id": 92}})
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/status", "message_id": 93}})
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/projects", "message_id": 94}})
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/export", "message_id": 95}})


@pytest.mark.asyncio
async def test_bot_internal_error_telegram_failure_resilience(mock_bot, monkeypatch):
    """Verify that if both agy throws an error and Telegram edit/send fails, bot stays alive."""
    import httpx

    async def mock_crashing_streaming(self, prompt, **kwargs):
        raise RuntimeError("Internal subprocess crash")
        yield  # make it a generator

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_crashing_streaming)
    mock_bot.client.edit_message_text = AsyncMock(side_effect=httpx.ReadError("Telegram API down"))
    mock_bot.client.send_message = AsyncMock(side_effect=httpx.ReadError("Telegram API down"))

    # Must complete cleanly without unhandled exception
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Break something", "message_id": 96}})

    session = mock_bot.session_manager.get_session(100)
    assert session.is_busy is False


@pytest.mark.asyncio
async def test_bot_system_prompt_passed_to_bridge(mock_bot, monkeypatch):
    """Verify that bot passes configured system prompt to AgyBridge on first turn."""
    captured_kwargs = {}

    async def mock_streaming_capture(self, prompt, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["prompt"] = prompt
        yield AgyEvent(type="init", conversation_id="conv-first-turn-888")
        yield AgyEvent(type="result", response="Hello from agy", duration_seconds=0.5, usage={"total_tokens": 100})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_streaming_capture)

    # First turn: conversation_id is None
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Start chat", "message_id": 101}})

    assert captured_kwargs["conversation_id"] is None
    assert captured_kwargs["system_prompt"] == mock_bot.config.system_prompt
    assert "[AGY_FILE]" in captured_kwargs["system_prompt"]

    # Verify conversation ID was set after turn
    session = mock_bot.session_manager.get_session(100)
    assert session.conversation_id == "conv-first-turn-888"

    # Second turn: conversation_id is now set
    captured_kwargs.clear()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Second message", "message_id": 102}})
    assert captured_kwargs["conversation_id"] == "conv-first-turn-888"
    assert captured_kwargs["system_prompt"] == mock_bot.config.system_prompt


@pytest.mark.asyncio
async def test_bot_setup_bot_commands(mock_bot):
    mock_bot.client.set_my_commands = AsyncMock(return_value=True)
    mock_bot.client.set_chat_menu_button = AsyncMock(return_value=True)

    success = await mock_bot.setup_bot_commands()
    assert success is True
    mock_bot.client.set_my_commands.assert_called_once()
    mock_bot.client.set_chat_menu_button.assert_called_once_with(menu_button={"type": "commands"})



