"""Unit tests for gateway.ipc_router (AGY_IPC protocol & pinned accordion card formatting)."""

from gateway.ipc_router import (
    IPCCall,
    IPCResult,
    extract_ipc_calls,
    format_incoming_envelope,
    format_pinned_accordion_card,
    format_return_envelope,
    log_trace_span,
    validate_ipc_call,
)


def test_extract_ipc_calls_basic():
    text = """
Here is my preliminary analysis.

[AGY_IPC to="learner"]
Please synchronize the audio bar into learner:
1. Support scrubbing
2. Run pytest
[END_AGY_IPC]

I will now wait for learner's response.
"""
    clean_text, calls = extract_ipc_calls(text, default_source="agy-telegram")
    assert len(calls) == 1
    assert calls[0].target_project == "learner"
    assert calls[0].source_project == "agy-telegram"
    assert calls[0].hop_count == 1
    assert "Support scrubbing" in calls[0].content
    assert "[AGY_IPC" not in clean_text
    assert "[END_AGY_IPC]" not in clean_text
    assert "Here is my preliminary analysis." in clean_text
    assert "I will now wait for learner's response." in clean_text


def test_extract_ipc_calls_multiple_and_hops():
    text = """
[AGY_IPC to="learner" hop="2"]
Task for learner
[END_AGY_IPC]

Middle text.

[AGY_IPC to="news"]
Task for news
[END_AGY_IPC]
"""
    clean_text, calls = extract_ipc_calls(text, default_source="admin")
    assert len(calls) == 2
    assert calls[0].target_project == "learner"
    assert calls[0].hop_count == 2
    assert calls[0].content == "Task for learner"
    assert calls[1].target_project == "news"
    assert calls[1].hop_count == 1
    assert calls[1].content == "Task for news"
    assert clean_text == "Middle text."


def test_extract_ipc_calls_no_calls():
    raw = "Normal conversation without any IPC block."
    clean, calls = extract_ipc_calls(raw, default_source="agy-telegram")
    assert clean == raw
    assert calls == []


def test_format_incoming_envelope():
    env = format_incoming_envelope(
        source="admin",
        target="learner",
        body="Implement user auth endpoint",
        time_str="2026-08-20 12:00:00",
        hop_count=1,
    )
    assert '[AGY_INCOMING from="admin" to="learner" time="2026-08-20 12:00:00" hop="1"]' in env
    assert "Implement user auth endpoint" in env
    assert "[END_AGY_INCOMING]" in env
    assert '[AGY_IPC to="admin"]' in env


def test_format_return_envelope():
    ret_success = format_return_envelope(
        source="learner",
        target="admin",
        body="Tests passed 100%",
        status="SUCCESS",
    )
    assert '[AGY_INCOMING from="learner" to="admin" status="SUCCESS"]' in ret_success
    assert "Tests passed 100%" in ret_success

    ret_err = format_return_envelope(
        source="learner",
        target="admin",
        body="Pytest failed: 2 errors",
        status="ERROR",
    )
    assert '[AGY_INCOMING from="learner" to="admin" status="ERROR"]' in ret_err


def test_format_pinned_accordion_card_in_progress():
    card = format_pinned_accordion_card(
        target_project="learner",
        source_project="agy-telegram",
        task_preview="Synchronize floating audio scrubber",
        tools_log=["📄 view_file: store.js (1.1s)", "⚙️ pytest tests/ (running...)"],
        status_text="Executing unit tests...",
        is_done=False,
        is_error=False,
        duration_seconds=3.5,
    )
    assert "📌 <b>[Background Task · learner]</b> 🟡 <i>In Progress (3.5s)</i>" in card
    assert "<blockquote expandable>" in card
    assert "<b>Task from agy-telegram:</b> Synchronize floating audio scrubber" in card
    assert "• 📄 view_file: store.js (1.1s)" in card
    assert "• ⚙️ pytest tests/ (running...)" in card
    assert "⏱ <i>Executing unit tests...</i>" in card
    assert "</blockquote>" in card


def test_format_pinned_accordion_card_completed():
    card = format_pinned_accordion_card(
        target_project="learner",
        source_project="admin",
        task_preview="Synchronize floating audio scrubber",
        tools_log=["📄 view_file: store.js (1.1s)", "✅ pytest (36 passed) (2.1s)"],
        status_text="Completed",
        is_done=True,
        is_error=False,
        duration_seconds=5.8,
        final_reply="All 36 tests passed and files committed.",
    )
    assert "📌 <b>[Background Task · learner]</b> 🟢 <i>Completed (5.8s)</i>" in card
    assert "<b>Result:</b>\nAll 36 tests passed and files committed." in card
    assert "<blockquote expandable>" in card


def test_format_pinned_accordion_card_error():
    card = format_pinned_accordion_card(
        target_project="learner",
        source_project="admin",
        task_preview="Synchronize floating audio scrubber",
        tools_log=["❌ pytest failed (1 error)"],
        status_text="AssertionError in test_sync",
        is_done=False,
        is_error=True,
        duration_seconds=4.0,
    )
    assert "📌 <b>[Background Task · learner]</b> 🔴 <i>Failed (4.0s)</i>" in card
    assert "❌ <b>Error:</b> AssertionError in test_sync" in card


def test_format_pinned_accordion_card_renders_markdown_and_prevents_nested_blockquote():
    card = format_pinned_accordion_card(
        target_project="file-share",
        source_project="admin",
        task_preview="Report status",
        tools_log=["✅ read_file (1.3s)"],
        status_text="Completed",
        is_done=True,
        duration_seconds=2.5,
        final_reply="**Project Status: `file-share`**\n- **Git**: `main` clean\n> quoted note",
    )
    assert "<b>Project Status: <code>file-share</code></b>" in card
    assert "• <b>Git</b>: <code>main</code> clean" in card
    assert "**Project Status" not in card
    assert card.count("<blockquote") == 1
    assert card.count("</blockquote>") == 1


def test_validate_ipc_call_cycles_and_max_hops():
    # Normal call passes
    ok_call = IPCCall(target_project="learner", content="Check", source_project="admin", hop_count=2)
    assert validate_ipc_call(ok_call, max_hops=3, visited_projects=("admin",)) is None

    # Self-delegation blocked
    self_call = IPCCall(target_project="admin", content="Loop", source_project="admin", hop_count=1)
    res_self = validate_ipc_call(self_call, max_hops=3)
    assert res_self is not None and res_self[0] == "CYCLE_DETECTED"

    # Transitive cycle blocked (admin -> learner -> admin)
    trans_call = IPCCall(target_project="admin", content="Return loop", source_project="learner", hop_count=2)
    res_trans = validate_ipc_call(trans_call, max_hops=3, visited_projects=("admin", "learner"))
    assert res_trans is not None and res_trans[0] == "CYCLE_DETECTED"
    assert "admin -> learner -> admin" in res_trans[1]

    # Exceeding max_hops blocked
    deep_call = IPCCall(target_project="news", content="Too deep", source_project="learner", hop_count=4)
    res_hop = validate_ipc_call(deep_call, max_hops=3, visited_projects=("admin", "learner"))
    assert res_hop is not None and res_hop[0] == "MAX_HOPS_EXCEEDED"


def test_extract_ipc_attrs_envelope_card_and_trace_jsonl(tmp_path):
    import json

    raw = '[AGY_IPC to="news" trace_id="tr1234567890abcd" hop="2" path="admin->learner"]Fetch headlines[END_AGY_IPC]'
    _, calls = extract_ipc_calls(raw, default_source="learner")
    assert len(calls) == 1
    assert calls[0].trace_id == "tr1234567890abcd"
    assert calls[0].hop_count == 2
    assert calls[0].visited_projects == ("admin", "learner")

    env = format_incoming_envelope(
        source="learner",
        target="news",
        body="Fetch headlines",
        hop_count=2,
        trace_id="tr1234567890abcd",
        visited_projects=("admin", "learner", "news"),
    )
    assert 'trace_id="tr1234567890abcd"' in env
    assert 'path="admin->learner->news"' in env

    card = format_pinned_accordion_card(
        target_project="news",
        source_project="learner",
        task_preview="Fetch headlines",
        tools_log=[],
        status_text="Running",
        trace_id="tr1234567890abcd",
    )
    assert "<code>tr123456</code>" in card

    tf = tmp_path / "traces.jsonl"
    rec = log_trace_span(
        trace_id="tr1234567890abcd",
        span_id="sp1122334455",
        parent_span_id="sp0000000000",
        operation="ipc_call",
        status="SUCCESS",
        duration_ms=42.5,
        source_project="learner",
        target_project="news",
        hop=2,
        trace_file=tf,
    )
    assert rec["trace_id"] == "tr1234567890abcd"
    lines = tf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["parent_span_id"] == "sp0000000000"
    assert parsed["duration_ms"] == 42.5
