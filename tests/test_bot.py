"""Unit tests for gateway.bot command routing, steering, file sending, and streaming orchestration."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
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
        pairing_file=str(tmp_path / "bot_pairing.json"),
    )
    bot = AgyTelegramBot(cfg)
    bot.client.send_message = AsyncMock(return_value={"message_id": 999})
    bot.client.edit_message_text = AsyncMock(return_value={"message_id": 999})
    bot.client.send_document = AsyncMock(return_value={"message_id": 1000})
    bot.client.send_chat_action = AsyncMock(return_value=True)
    bot.client.answer_callback_query = AsyncMock(return_value=True)
    bot.client.edit_message_reply_markup = AsyncMock(return_value=True)
    bot.client.pin_chat_message = AsyncMock(return_value=True)
    bot.client.unpin_chat_message = AsyncMock(return_value=True)
    bot.client.set_message_reaction = AsyncMock(return_value=True)
    bot.client.send_voice = AsyncMock(return_value={"message_id": 1005, "voice": {"duration": 5}})
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
    status_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Status:" in status_text
    assert "Active Session Cumulative Stats:" in status_text
    assert "Total Turns:" in status_text
    assert "Total Tokens:" in status_text


@pytest.mark.asyncio
async def test_bot_model_and_effort(mock_bot):
    # /model
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/model gemini-3.8-flash", "message_id": 3}})
    assert "gemini-3.8-flash" in mock_bot.client.send_message.call_args[1]["text"]
    assert mock_bot.session_manager.get_session(100).model == "gemini-3.8-flash"

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

    # /new proj1 /tmp/proj1 -> created on disk automatically
    assert not Path(p1).exists()
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj1 {p1}", "message_id": 12}})
    text = mock_bot.client.send_message.call_args[1]["text"]
    assert "created and activated" in text
    assert "Directory created" in text
    assert Path(p1).is_dir()
    session = mock_bot.session_manager.get_session(100)
    assert session.active_project == "proj1"
    assert session.active_path() == str(Path(p1).resolve())

    # /new proj1 again -> treated as /open (with Existing directory notice)
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
    proj_dir = tmp_path / "custom_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (proj_dir / "README.md").write_text("# Project", encoding="utf-8")
    sub = proj_dir / "gateway"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "bot.py").write_text("# bot", encoding="utf-8")

    mock_bot.session_manager.create_project(100, "custom_proj", str(proj_dir))

    # Test /dir
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/dir", "message_id": 40}})
    mock_bot.client.send_document.assert_called_once()
    call_args = mock_bot.client.send_document.call_args[1]
    assert "tree_" in call_args["filename"]
    assert "custom_proj" in call_args["caption"]
    assert "Folders:" in call_args["caption"]
    assert "Files:" in call_args["caption"]
    assert "/file" in call_args["caption"]

    # Test /ls alias
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/ls", "message_id": 41}})
    mock_bot.client.send_document.assert_called_once()


@pytest.mark.asyncio
async def test_bot_file_and_export_commands(mock_bot, tmp_path):
    # Setup test file in project workspace
    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    test_file = proj_dir / "sample.txt"
    test_file.write_text("Hello from workspace file")

    mock_bot.session_manager.create_project(100, "my_project", str(proj_dir))

    # Test /file sample.txt (raw)
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/file sample.txt", "message_id": 60}})
    mock_bot.client.send_document.assert_called_once()
    call_args = mock_bot.client.send_document.call_args[1]
    assert call_args["filename"] == "sample.txt"
    assert call_args["chat_id"] == 100

    # Test /file -h sample.txt (HTML mode)
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/file -h sample.txt", "message_id": 61}})
    mock_bot.client.send_document.assert_called_once()
    call_args_h = mock_bot.client.send_document.call_args[1]
    assert call_args_h["filename"] == "sample.txt.html"
    assert "HTML view" in call_args_h["caption"]

    # Test /export
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/export", "message_id": 62}})
    mock_bot.client.send_document.assert_called_once()
    export_args = mock_bot.client.send_document.call_args[1]
    assert "session_report" in export_args["filename"]


@pytest.mark.asyncio
async def test_bot_unauthorized_chat(mock_bot):
    # 1. With DM pairing enabled (default), unauthorized chat gets a pairing code
    await mock_bot.process_update({"message": {"chat": {"id": 999}, "text": "Hello", "message_id": 1}})
    last_call = mock_bot.client.send_message.call_args[1]
    assert last_call["chat_id"] == 999
    assert "Access Denied" in last_call["text"]
    assert "Pairing Code" in last_call["text"]

    # 2. With DM pairing disabled, unauthorized chat gets static private rejection
    mock_bot.config.dm_pairing_enabled = False
    await mock_bot.process_update({"message": {"chat": {"id": 998}, "text": "Hello", "message_id": 2}})
    mock_bot.client.send_message.assert_called_with(
        chat_id=998,
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
    assert "default" in final_call_args["text"]

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
    assert "Full report generated and attached below" in final_text


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
    assert "Failed to export file" in final_text or "File not found" in final_text


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
    assert "2 report file(s) generated and attached below" in final_text
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
async def test_bot_midflight_steering_progress_rotation_and_single_report(mock_bot, monkeypatch):
    """Test that midflight steering freezes old progress message, creates new progress reply, and yields only 1 final report."""
    mock_bot.session_manager.create_project(100, "test_proj", "/tmp")

    sent_msgs = []
    msg_counter = 1000

    async def mock_send_message(chat_id, text, reply_to_message_id=None, **kwargs):
        nonlocal msg_counter
        msg_counter += 1
        m = {"message_id": msg_counter, "chat": {"id": chat_id}, "text": text, "reply_to": reply_to_message_id}
        sent_msgs.append(m)
        return m

    edited_msgs = []

    async def mock_edit_message_text(chat_id, message_id, text, **kwargs):
        edited_msgs.append({"message_id": message_id, "text": text})
        return {"message_id": message_id, "text": text}

    mock_bot.client.send_message = AsyncMock(side_effect=mock_send_message)
    mock_bot.client.edit_message_text = AsyncMock(side_effect=mock_edit_message_text)

    steer_event = asyncio.Event()

    async def mock_run_streaming(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-steer-test")
        yield AgyEvent(type="text_delta", text_delta="Working on step 1...")
        await steer_event.wait()
        yield AgyEvent(type="turn_complete", response="Step 1 complete, now adapting to steering...")
        yield AgyEvent(type="result", response="Final unified result after steering!", duration_seconds=2.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_run_streaming)
    monkeypatch.setattr(AgyBridge, "is_running", lambda self: True)
    monkeypatch.setattr(AgyBridge, "steer", AsyncMock(return_value=True))

    # 1. Start prompt 1 in background
    task = asyncio.create_task(
        mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Task 1", "message_id": 10}})
    )
    await asyncio.sleep(0.05)

    # Verify session is busy
    assert mock_bot.session_manager.is_busy(100) is True
    # Initial placeholder was sent (message_id 1001)
    assert any(m["message_id"] == 1001 for m in sent_msgs)

    # 2. User sends steering instruction (message_id 11)
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "Actually do Task 2", "message_id": 11}})

    # Old working message (1001) must have been edited to freeze with Task Steered
    frozen = [e for e in edited_msgs if e["message_id"] == 1001 and "Task Steered" in e["text"]]
    assert len(frozen) >= 1

    # New progress message (1002) was sent replying to message 11
    steer_msgs = [m for m in sent_msgs if m.get("reply_to") == 11 and "Steered into active session" in m["text"]]
    assert len(steer_msgs) == 1
    new_progress_id = steer_msgs[0]["message_id"]

    # 3. Resume streaming and finish execution
    steer_event.set()
    await task

    # Session must now be idle
    assert mock_bot.session_manager.is_busy(100) is False

    # The final report must have edited the NEW progress message (1002), NOT 1001
    final_edits = [e for e in edited_msgs if e["message_id"] == new_progress_id and "Final unified result" in e["text"]]
    assert len(final_edits) >= 1


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


@pytest.mark.asyncio
async def test_bot_projects_inline_keyboard(mock_bot, tmp_path):
    p1 = str(tmp_path / "proj1")
    p2 = str(tmp_path / "proj2")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj1 {p1}", "message_id": 10}})
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj2 {p2}", "message_id": 11}})

    # Call /projects
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/projects", "message_id": 12}})

    call_kwargs = mock_bot.client.send_message.call_args[1]
    assert "reply_markup" in call_kwargs
    inline_kb = call_kwargs["reply_markup"]["inline_keyboard"]
    # Verify buttons exist for both projects
    flattened_btns = [btn for row in inline_kb for btn in row]
    callback_datas = [b["callback_data"] for b in flattened_btns]
    assert "open:proj1" in callback_datas
    assert "open:proj2" in callback_datas
    assert "projects:refresh" in callback_datas


@pytest.mark.asyncio
async def test_bot_callback_query_open_project(mock_bot, tmp_path):
    p1 = str(tmp_path / "proj_alpha")
    p2 = str(tmp_path / "proj_beta")
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj_alpha {p1}", "message_id": 20}})
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": f"/new proj_beta {p2}", "message_id": 21}})

    # Currently active is proj_beta. Now click inline button for proj_alpha
    cb_update = {
        "callback_query": {
            "id": "cb_query_999",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 555},
            "data": "open:proj_alpha",
        }
    }
    await mock_bot.process_update(cb_update)

    # Check that answer_callback_query was called
    mock_bot.client.answer_callback_query.assert_called_with(
        callback_query_id="cb_query_999",
        text="Opened project: proj_alpha",
        show_alert=False,
    )
    # Check that session active project switched to proj_alpha
    assert mock_bot.session_manager.get_session(100).active_project == "proj_alpha"
    # Check that message was edited in place
    assert mock_bot.client.edit_message_text.called


@pytest.mark.asyncio
async def test_bot_callback_query_effort_and_model(mock_bot):
    # Effort callback
    cb_effort = {
        "callback_query": {
            "id": "cb_effort_1",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 601},
            "data": "effort:high",
        }
    }
    await mock_bot.process_update(cb_effort)
    assert mock_bot.session_manager.get_session(100).effort == "high"
    mock_bot.client.answer_callback_query.assert_called_with(
        callback_query_id="cb_effort_1",
        text="Reasoning effort set to high",
        show_alert=False,
    )

    # Model callback
    cb_model = {
        "callback_query": {
            "id": "cb_model_1",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 602},
            "data": "model:gemini-3.1-pro",
        }
    }
    await mock_bot.process_update(cb_model)
    assert mock_bot.session_manager.get_session(100).model == "gemini-3.1-pro"
    mock_bot.client.answer_callback_query.assert_called_with(
        callback_query_id="cb_model_1",
        text="Model set to gemini-3.1-pro",
        show_alert=False,
    )

    # Model refresh callback
    cb_model_refresh = {
        "callback_query": {
            "id": "cb_model_ref",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 602},
            "data": "model:refresh",
        }
    }
    await mock_bot.process_update(cb_model_refresh)
    mock_bot.client.answer_callback_query.assert_called_with(
        callback_query_id="cb_model_ref",
        text="Refreshing models from agy...",
        show_alert=False,
    )


@pytest.mark.asyncio
async def test_bot_voice_message_flow(mock_bot, tmp_path):
    fake_voice_file = tmp_path / "voice_100_v123.oga"
    fake_voice_file.write_bytes(b"TEST_AUDIO")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_voice_file, {"file_id": "v123", "file_path": "voice/file.oga"})
    )

    captured_prompt = {}

    async def mock_handle_prompt(chat_id, prompt, reply_to_id, **kwargs):
        captured_prompt["chat_id"] = chat_id
        captured_prompt["prompt"] = prompt
        captured_prompt["reply_to_id"] = reply_to_id
        captured_prompt["is_voice_input"] = kwargs.get("is_voice_input", False)

    mock_bot.handle_prompt = mock_handle_prompt

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 777,
            "voice": {
                "file_id": "v123",
                "file_unique_id": "vu123",
                "duration": 8,
                "mime_type": "audio/ogg",
            },
        }
    }

    with patch("gateway.media_handlers.transcribe_voice", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = ("检查一下各个项目的状态", "zh")
        await mock_bot.process_update(update)

    mock_bot.client.send_chat_action.assert_called_with(chat_id=100, action="record_voice")
    mock_bot.client.download_file_by_id.assert_called_once()
    assert captured_prompt["chat_id"] == 100
    assert captured_prompt["reply_to_id"] == 777
    assert captured_prompt["prompt"] == "检查一下各个项目的状态"


@pytest.mark.asyncio
async def test_bot_voice_message_steer(mock_bot, tmp_path):
    fake_voice_file = tmp_path / "voice_100_steer.oga"
    fake_voice_file.write_bytes(b"STEER_AUDIO")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_voice_file, {"file_id": "v_steer", "file_path": "voice/steer.oga"})
    )

    # Simulate busy bridge
    session = mock_bot.session_manager.get_session(100)
    session.is_busy = True
    fake_bridge = AsyncMock()
    fake_bridge.steer = AsyncMock(return_value=True)
    mock_bot.active_bridges[100] = fake_bridge

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 888,
            "voice": {
                "file_id": "v_steer",
                "file_unique_id": "vu_steer",
                "duration": 5,
                "mime_type": "audio/ogg",
            },
        }
    }

    with patch("gateway.media_handlers.transcribe_voice", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = ("请帮我改用异步写法", "zh")
        await mock_bot.process_update(update)

    assert fake_bridge.steer.called
    steer_call_arg = fake_bridge.steer.call_args[0][0]
    assert steer_call_arg == "请帮我改用异步写法"

    # Confirmation sent to chat
    sent_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Steered voice instruction (5s)" in sent_text
    assert "请帮我改用异步写法" in sent_text


@pytest.mark.asyncio
async def test_bot_audio_message_with_caption(mock_bot, tmp_path):
    fake_audio_file = tmp_path / "audio_100_song.mp3"
    fake_audio_file.write_bytes(b"AUDIO_DATA")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_audio_file, {"file_id": "a999", "file_path": "music/song.mp3"})
    )

    captured_prompt = {}

    async def mock_handle_prompt(chat_id, prompt, reply_to_id, **kwargs):
        captured_prompt["prompt"] = prompt

    mock_bot.handle_prompt = mock_handle_prompt

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 900,
            "audio": {
                "file_id": "a999",
                "file_unique_id": "au999",
                "duration": 12,
                "mime_type": "audio/mpeg",
            },
            "caption": "Please translate this voice message",
        }
    }

    with patch("gateway.media_handlers.transcribe_voice", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = ("Hello world", "en")
        await mock_bot.process_update(update)

    assert "Hello world" in captured_prompt["prompt"]
    assert "[Caption]: Please translate this voice message" in captured_prompt["prompt"]


@pytest.mark.asyncio
async def test_bot_document_message_flow(mock_bot, tmp_path):
    fake_doc_file = tmp_path / "doc_100_data.csv"
    fake_doc_file.write_bytes(b"col1,col2\n1,2")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_doc_file, {"file_id": "d123", "file_path": "documents/data.csv"})
    )

    captured_prompt = {}

    async def mock_handle_prompt(chat_id, prompt, reply_to_id):
        captured_prompt["prompt"] = prompt

    mock_bot.handle_prompt = mock_handle_prompt

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 890,
            "document": {
                "file_id": "d123",
                "file_unique_id": "du123",
                "file_name": "data.csv",
                "file_size": 256,
                "mime_type": "text/csv",
            },
            "caption": "Please analyze this CSV dataset",
        }
    }

    await mock_bot.process_update(update)

    mock_bot.client.send_chat_action.assert_called_with(chat_id=100, action="upload_document")
    assert str(fake_doc_file.resolve()) in captured_prompt["prompt"]
    assert "data.csv" in captured_prompt["prompt"]
    assert "User Instructions/Caption: Please analyze this CSV dataset" in captured_prompt["prompt"]


@pytest.mark.asyncio
async def test_bot_photo_message_flow(mock_bot, tmp_path):
    fake_photo_file = tmp_path / "photo_pu456.jpg"
    fake_photo_file.write_bytes(b"IMAGE_BYTES")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_photo_file, {"file_id": "p456", "file_path": "photos/photo.jpg"})
    )

    captured_prompt = {}

    async def mock_handle_prompt(chat_id, prompt, reply_to_id):
        captured_prompt["prompt"] = prompt

    mock_bot.handle_prompt = mock_handle_prompt

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 891,
            "photo": [
                {"file_id": "p123", "file_size": 100},
                {"file_id": "p456", "file_unique_id": "pu456", "file_size": 500},
            ],
            "caption": "Please describe this architecture diagram",
        }
    }

    await mock_bot.process_update(update)

    assert str(fake_photo_file.resolve()) in captured_prompt["prompt"]
    assert "photo_pu456.jpg" in captured_prompt["prompt"]
    assert "Please describe this architecture diagram" in captured_prompt["prompt"]


@pytest.mark.asyncio
async def test_bot_voice_download_error(mock_bot):
    mock_bot.client.download_file_by_id = AsyncMock(side_effect=RuntimeError("Connection reset by peer"))

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 901,
            "voice": {
                "file_id": "v_err",
                "file_unique_id": "vu_err",
                "duration": 3,
            },
        }
    }

    await mock_bot.process_update(update)

    sent_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Failed to download voice message" in sent_text
    assert "Connection reset by peer" in sent_text


@pytest.mark.asyncio
async def test_bot_voice_unauthorized(mock_bot):
    update = {
        "message": {
            "chat": {"id": 999},  # Unauthorized chat ID
            "message_id": 902,
            "voice": {
                "file_id": "v_unauth",
                "file_unique_id": "vu_unauth",
                "duration": 3,
            },
        }
    }

    await mock_bot.process_update(update)

    sent_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Access Denied" in sent_text


@pytest.mark.asyncio
async def test_bot_usage_skills_artifacts_commands(mock_bot, monkeypatch, tmp_path):
    """Test /usage, /skills, /artifacts commands and their callback interactions."""
    import gateway.commands as commands_module

    # Mock _run_agy_readonly_cmd
    async def mock_readonly(agy_bin, slash_cmd):
        if slash_cmd == "/usage":
            return {
                "command": {
                    "data": {
                        "groups": [
                            {
                                "name": "Gemini Models",
                                "buckets": [
                                    {"name": "Weekly Limit", "remaining_fraction": 0.75, "reset_time": "2026-08-25T01:00:00Z"},
                                    {"name": "5-Hour Limit", "remaining_fraction": 0.90, "reset_time": "2026-08-20T03:00:00Z"},
                                ],
                            }
                        ]
                    }
                }
            }
        elif slash_cmd == "/credits":
            return {"command": {"data": {"remaining_credits": 10}}}
        elif slash_cmd == "/skills":
            return {
                "command": {
                    "data": {
                        "skills": [
                            {"name": "agy-customizations", "description": "Customization guide"},
                            {"name": "firebase:firestore", "description": "Firestore DB", "plugin": "firebase"},
                        ]
                    }
                }
            }
        return None

    monkeypatch.setattr(commands_module, "_run_agy_readonly_cmd", mock_readonly)

    # 1. /usage
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/usage", "message_id": 1001}})
    usage_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Quota Monitor" in usage_text
    assert "Gemini Models" in usage_text
    assert "75.0%" in usage_text
    assert "10 credits" in usage_text

    # 2. /skills
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/skills", "message_id": 1002}})
    skills_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Skills & Plugin Tools" in skills_text
    assert "agy-customizations" in skills_text
    assert "firebase" in skills_text

    # 3. /artifacts (empty)
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/artifacts", "message_id": 1003}})
    art_text = mock_bot.client.send_message.call_args[1]["text"]
    assert "Conversation Artifacts & Reports" in art_text

    # 4. /artifacts with mock file
    fake_art = tmp_path / "runtime" / "artifacts" / "test_summary.html"
    fake_art.parent.mkdir(parents=True, exist_ok=True)
    fake_art.write_text("<h1>Report</h1>")
    session = mock_bot.session_manager.get_session(100)
    session.projects["default"] = {"name": "default", "path": str(tmp_path)}
    session.active_project = "default"

    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/artifacts 1", "message_id": 1004}})
    mock_bot.client.send_document.assert_called_once()
    assert "test_summary.html" in mock_bot.client.send_document.call_args[1]["filename"]

    # 5. Callback query: usage:show
    mock_bot.client.edit_message_text.reset_mock()
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_u1",
            "message": {"chat": {"id": 100}, "message_id": 1001},
            "data": "usage:show",
        }
    })
    mock_bot.client.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_bot_decision_and_action_buttons_flow(mock_bot):
    session = mock_bot.session_manager.get_session(100)
    session.pending_decisions = ["FastAPI (Recommended)", "Flask"]
    session.pending_actions = [
        {"label": "🧪 Run Tests", "action": "pytest -v"},
        {"label": "📊 Check Status", "action": "/status"},
    ]

    dispatched_prompts = []

    async def mock_handle_prompt(chat_id, prompt, reply_to_id=None):
        dispatched_prompts.append((chat_id, prompt))

    mock_bot.handle_prompt = mock_handle_prompt

    dispatched_commands = []

    async def mock_dispatch_cmd(chat_id, cmd, arg="", message_id=None):
        dispatched_commands.append((chat_id, cmd, arg))

    mock_bot.dispatch_slash_command = mock_dispatch_cmd

    # 1. Test clicking a decision button 'dec:0'
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_dec_0",
            "message": {"chat": {"id": 100}, "message_id": 500},
            "data": "dec:0",
        }
    })
    assert len(dispatched_prompts) == 1
    assert dispatched_prompts[0][1] == "FastAPI (Recommended)"

    # 2. Test clicking a prompt action button 'act:0' (pytest -v)
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_act_0",
            "message": {"chat": {"id": 100}, "message_id": 501},
            "data": "act:0",
        }
    })
    assert len(dispatched_prompts) == 2
    assert dispatched_prompts[1][1] == "pytest -v"

    # 3. Test clicking a slash command action button 'act:1' (/status)
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_act_1",
            "message": {"chat": {"id": 100}, "message_id": 502},
            "data": "act:1",
        }
    })
    assert len(dispatched_commands) == 1
    assert dispatched_commands[0][1] == "/status"


@pytest.mark.asyncio
async def test_bot_prompt_generates_action_keyboard(mock_bot):
    async def mock_stream(*args, **kwargs):
        yield AgyEvent(type="init", conversation_id="c12345")
        yield AgyEvent(
            type="result",
            response="Task completed.\n\n[AGY_ACTIONS]\n- 🧪 Run Tests: pytest\n- 📝 View Diff: /diff\n[END_AGY_ACTIONS]",
            usage={"total_tokens": 500},
            duration_seconds=1.5,
        )

    mock_bot.active_bridges[100] = MagicMock()
    mock_bot.active_bridges[100].run_streaming = mock_stream

    update = {"message": {"chat": {"id": 100}, "text": "build project", "message_id": 777}}
    await mock_bot.process_update(update)

    # Verify that reply_markup was attached to the final message edit
    call_args = mock_bot.client.edit_message_text.call_args[1]
    reply_markup = call_args.get("reply_markup")
    assert reply_markup is not None
    assert "inline_keyboard" in reply_markup
    buttons = reply_markup["inline_keyboard"]
    assert len(buttons) >= 1
    assert any(btn.get("callback_data") == "act:0" for row in buttons for btn in row)
    assert any(btn.get("callback_data", "").startswith("voice:") for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_bot_update_pinned_banner_and_pin_command(mock_bot):
    session = mock_bot.session_manager.get_session(100)
    mock_bot.session_manager.create_project(100, "pin-test", "/tmp/pin-test")

    # 1. Update pinned banner when no pinned message exists -> sends message and pins it
    mock_bot.client.send_message.reset_mock()
    mock_bot.client.pin_chat_message.reset_mock()
    msg_id = await mock_bot.update_pinned_banner(100)
    assert msg_id == 999  # default mock message_id
    assert session.pinned_message_id == 999
    assert mock_bot.client.pin_chat_message.called
    assert mock_bot.client.pin_chat_message.call_args[1]["disable_notification"] is True

    # 2. Update pinned banner when pinned message exists -> edits existing message
    mock_bot.client.edit_message_text.reset_mock()
    mock_bot.client.send_message.reset_mock()
    msg_id_2 = await mock_bot.update_pinned_banner(100)
    assert msg_id_2 == 999
    assert mock_bot.client.edit_message_text.called
    assert mock_bot.client.send_message.call_count == 0

    # 3. Test /pin slash command
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/pin", "message_id": 88}})
    assert mock_bot.client.send_message.called
    assert "Pinned workspace banner updated" in mock_bot.client.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_bot_voice_command_and_voice_summary_flow(mock_bot, monkeypatch):
    session = mock_bot.session_manager.get_session(100)

    # 1. Test /voice command display
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/voice", "message_id": 101}})
    assert mock_bot.client.send_message.called
    assert "Voice Broadcast Settings" in mock_bot.client.send_message.call_args[1]["text"]

    # 2. Test /voice autosend / autoplay setting
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/voice autosend", "message_id": 102}})
    assert session.tts_mode == "autosend"

    # 3. Test prompt response with [AGY_VOICE] block in ondemand mode (button generation)
    session.tts_mode = "ondemand"
    voice_resp = """Here is the written report.

[AGY_VOICE lang="zh"]
我已经为你完成了所有的代码修改。
[END_AGY_VOICE]
"""
    async def mock_stream(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-v-1")
        yield AgyEvent(type="result", response=voice_resp, duration_seconds=1.0, usage={"total_tokens": 100})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream)
    mock_bot.client.edit_message_text.reset_mock()

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "explain architecture", "message_id": 103}})

    # Verify [AGY_VOICE] is stripped from visible text
    edit_call = mock_bot.client.edit_message_text.call_args[1]
    assert "[AGY_VOICE" not in edit_call["text"]
    assert "Here is the written report" in edit_call["text"]

    # Verify play button is attached
    markup = edit_call.get("reply_markup")
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert any("🔊 Send Audio" in btn["text"] for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_bot_voice_with_long_auto_export(mock_bot, monkeypatch):
    session = mock_bot.session_manager.get_session(100)
    session.tts_mode = "autoplay"

    # Simulate a long response (>2000 chars) that triggers auto HTML file export
    long_body = "This is a detailed technical section.\n\n" * 80
    raw_with_voice = f"{long_body}\n\n[AGY_VOICE lang=\"zh\"]\n全部功能总结已生成完毕。\n[END_AGY_VOICE]"

    async def mock_stream(self, prompt, **kwargs):
        yield AgyEvent(type="init", conversation_id="conv-v-2")
        yield AgyEvent(type="result", response=raw_with_voice, duration_seconds=2.0, usage={"total_tokens": 500})

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream)
    mock_bot.client.send_document.reset_mock()

    with patch.object(mock_bot, "_generate_and_send_voice", new_callable=AsyncMock) as mock_gen_voice:
        await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "总结一下功能", "message_id": 104}})

        # Verify HTML file export was sent
        assert mock_bot.client.send_document.called
        doc_call = mock_bot.client.send_document.call_args[1]
        exported_html = doc_call["document_content"]

        # CRITICAL: [AGY_VOICE] must NOT be inside the exported HTML document
        assert "[AGY_VOICE" not in exported_html
        assert "[END_AGY_VOICE]" not in exported_html

        # CRITICAL: _generate_and_send_voice MUST have been called with the voice text!
        assert mock_gen_voice.called
        gen_args = mock_gen_voice.call_args[1]
        assert gen_args["text"] == "全部功能总结已生成完毕。"
        assert gen_args["lang"] == "zh"


@pytest.mark.asyncio
async def test_bot_voice_input_forces_voice_autosend(mock_bot, tmp_path, monkeypatch):
    fake_voice_file = tmp_path / "voice_100_cmd.oga"
    fake_voice_file.write_bytes(b"TEST_AUDIO")

    mock_bot.client.download_file_by_id = AsyncMock(
        return_value=(fake_voice_file, {"file_id": "v_force", "file_path": "voice/cmd.oga"})
    )

    # Set tts_mode to disabled/ondemand explicitly
    session = mock_bot.session_manager.get_session(100)
    session.tts_mode = "disable"

    raw_response = "任务执行成功。\n\n[AGY_VOICE lang=\"zh\"]\n语音任务已经执行完毕。\n[END_AGY_VOICE]"

    async def mock_stream(self, prompt, **kwargs):
        yield AgyEvent(type="result", response=raw_response, duration_seconds=1.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream)

    update = {
        "message": {
            "chat": {"id": 100},
            "message_id": 999,
            "voice": {
                "file_id": "v_force",
                "file_unique_id": "vu_force",
                "duration": 3,
                "mime_type": "audio/ogg",
            },
        }
    }

    with patch("gateway.media_handlers.transcribe_voice", new_callable=AsyncMock) as mock_stt, \
         patch.object(mock_bot, "_generate_and_send_voice", new_callable=AsyncMock) as mock_gen_voice:
        mock_stt.return_value = ("测试语音命令", "zh")
        await mock_bot.process_update(update)

        # Even though tts_mode was "disable", voice input MUST trigger _generate_and_send_voice
        assert mock_gen_voice.called
        gen_args = mock_gen_voice.call_args[1]
        assert gen_args["text"] == "语音任务已经执行完毕。"
        assert gen_args["lang"] == "zh"


@pytest.mark.asyncio
async def test_bot_html_export_includes_voice_and_webapp_buttons(mock_bot, monkeypatch):
    session = mock_bot.session_manager.get_session(100)
    session.tts_mode = "ondemand"

    # Start mock webapp server
    mock_bot.webapp_server._is_running = True

    # Simulate text response generating [AGY_FILE] without explicit [AGY_VOICE]
    raw_response = """Here is the system architecture document.

[AGY_FILE]
filename: architecture.html
title: Architecture Overview
---
<!DOCTYPE html><html><body><h1>System Architecture</h1><p>Components details...</p></body></html>
[END_AGY_FILE]
"""
    async def mock_stream(self, prompt, **kwargs):
        yield AgyEvent(type="result", response=raw_response, duration_seconds=1.5)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream)
    mock_bot.client.send_document.reset_mock()
    mock_bot.client.edit_message_text.reset_mock()

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "generate architecture report", "message_id": 201}})

    # 1. Text message edit has the voice button
    assert mock_bot.client.edit_message_text.called
    text_edit_call = mock_bot.client.edit_message_text.call_args[1]
    text_markup = text_edit_call.get("reply_markup")
    assert text_markup is not None
    assert any("🔊 Send Audio" in btn["text"] for row in text_markup["inline_keyboard"] for btn in row)

    # 2. Document upload has both WebApp preview button and Send Audio button
    assert mock_bot.client.send_document.called
    doc_call = mock_bot.client.send_document.call_args[1]
    doc_markup = doc_call.get("reply_markup")
    assert doc_markup is not None
    doc_buttons = [btn["text"] for row in doc_markup["inline_keyboard"] for btn in row]
    assert any("Open in Telegram" in b for b in doc_buttons)
    assert any("Send Audio" in b for b in doc_buttons)


@pytest.mark.asyncio
async def test_bot_text_query_generates_fallback_ondemand_voice_button(mock_bot, monkeypatch):
    session = mock_bot.session_manager.get_session(100)
    session.tts_mode = "ondemand"

    # Plain text without [AGY_VOICE]
    raw_response = "所有自动化测试均已执行并通过，网关服务运行正常。"

    async def mock_stream(self, prompt, **kwargs):
        yield AgyEvent(type="result", response=raw_response, duration_seconds=1.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream)
    mock_bot.client.edit_message_text.reset_mock()

    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "介绍一下状态", "message_id": 202}})

    # Verify voice summary was generated in session
    assert len(session.pending_voice_data) > 0
    v_info = list(session.pending_voice_data.values())[-1]
    assert "所有自动化测试均已执行并通过" in v_info["text"]
    assert v_info["lang"] == "zh"

    # Verify Send Audio button is attached
    edit_call = mock_bot.client.edit_message_text.call_args[1]
    markup = edit_call.get("reply_markup")
    assert markup is not None
    assert any("🔊 Send Audio" in btn["text"] for row in markup["inline_keyboard"] for btn in row)


@pytest.mark.asyncio
async def test_bot_html_mode_command_callbacks_and_behavior(mock_bot, monkeypatch):
    session = mock_bot.session_manager.get_session(100)
    assert session.html_mode == "auto"

    # 1. /html command shows settings keyboard
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/html", "message_id": 301}})
    assert "HTML Attachment Settings" in mock_bot.client.send_message.call_args[1]["text"]

    # 2. Callback html_mode:explicit switches mode
    mock_bot.client.edit_message_text.reset_mock()
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_html_1",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 900},
            "data": "html_mode:explicit",
        }
    })
    assert session.html_mode == "explicit"
    mock_bot.client.answer_callback_query.assert_called_with(
        callback_query_id="cq_html_1",
        text="HTML mode: explicit",
        show_alert=False,
    )

    # 3. Long response (>2000 chars) in explicit mode does NOT auto-attach HTML
    long_resp = "Detailed line.\n" * 200

    async def mock_stream_long(self, prompt, **kwargs):
        yield AgyEvent(type="result", response=long_resp, duration_seconds=1.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream_long)
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "explain details", "message_id": 302}})
    assert mock_bot.client.send_document.called is False

    # 4. Explicit [AGY_FILE] block in explicit mode DOES send document
    explicit_resp = "Summary.\n\n[AGY_FILE]\nfilename: explicit_report.html\ntitle: Report\n---\n<h1>Hello</h1>\n[END_AGY_FILE]"

    async def mock_stream_explicit(self, prompt, **kwargs):
        yield AgyEvent(type="result", response=explicit_resp, duration_seconds=1.0)

    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream_explicit)
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "report please", "message_id": 303}})
    assert mock_bot.client.send_document.called is True

    # 5. In off mode, even [AGY_FILE] is suppressed in normal replies
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/html off", "message_id": 304}})
    assert session.html_mode == "off"
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "report please", "message_id": 305}})
    assert mock_bot.client.send_document.called is False

    # 6. In auto mode, long response auto-attaches HTML
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "/html auto", "message_id": 306}})
    monkeypatch.setattr(AgyBridge, "run_streaming", mock_stream_long)
    mock_bot.client.send_document.reset_mock()
    await mock_bot.process_update({"message": {"chat": {"id": 100}, "text": "explain details", "message_id": 307}})
    assert mock_bot.client.send_document.called is True


@pytest.mark.asyncio
async def test_bot_dm_pairing_flow(mock_bot):
    # Unknown chat 888 sends message -> denied with pairing code, owners (100, 200) notified
    mock_bot.client.send_message.reset_mock()
    await mock_bot.process_update({
        "message": {
            "chat": {"id": 888, "username": "guest", "first_name": "Guest"},
            "from": {"id": 888, "username": "guest", "first_name": "Guest"},
            "text": "hi",
            "message_id": 401,
        }
    })
    assert mock_bot.is_chat_authorized(888) is False

    # Last message goes to guest 888 with Access Denied & Pairing Code
    last_call = mock_bot.client.send_message.call_args[1]
    assert last_call["chat_id"] == 888
    assert "Access Denied" in last_call["text"]
    assert "Pairing Code" in last_call["text"]

    # Owner 100 received inline Approve/Reject notification
    owner_calls = [c[1] for c in mock_bot.client.send_message.call_args_list if c[1]["chat_id"] == 100]
    assert len(owner_calls) == 1
    code_btn = owner_calls[0]["reply_markup"]["inline_keyboard"][0][0]
    assert code_btn["callback_data"].startswith("pair:approve:")
    pcode = code_btn["callback_data"].split(":")[-1]

    # Non-owner cannot approve
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_unauth",
            "from": {"id": 888},
            "message": {"chat": {"id": 888}, "message_id": 500},
            "data": f"pair:approve:{pcode}",
        }
    })
    assert mock_bot.is_chat_authorized(888) is False

    # Owner 100 clicks Approve -> chat 888 becomes authorized
    await mock_bot.process_update({
        "callback_query": {
            "id": "cq_approve",
            "from": {"id": 100},
            "message": {"chat": {"id": 100}, "message_id": 501},
            "data": f"pair:approve:{pcode}",
        }
    })
    assert mock_bot.is_chat_authorized(888) is True











