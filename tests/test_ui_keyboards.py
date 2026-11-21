"""Unit tests for gateway.ui_keyboards."""

import pytest
from gateway.ui_keyboards import (
    build_effort_keyboard,
    build_model_keyboard,
    build_projects_keyboard,
)


def test_build_projects_keyboard_empty():
    text, markup = build_projects_keyboard({}, None)
    assert "No projects configured yet" in text
    assert markup["inline_keyboard"] == []


def test_build_projects_keyboard_with_items():
    projects = {
        "proj1": {"path": "/tmp/proj1", "conversation_id": "c12345678"},
        "proj2": {"path": "/tmp/proj2", "conversation_id": None},
    }
    text, markup = build_projects_keyboard(projects, "proj1")
    assert "Available Projects" in text
    assert "[ACTIVE]" in text
    assert "proj1" in text
    assert "proj2" in text

    buttons = markup["inline_keyboard"]
    assert len(buttons) >= 2
    # Check refresh and status buttons present
    bottom_row = buttons[-1]
    assert any(b["callback_data"] == "projects:refresh" for b in bottom_row)
    assert any(b["callback_data"] == "status" for b in bottom_row)


def test_build_model_keyboard():
    text, markup = build_model_keyboard("gemini-3.8-flash", "high")
    assert "Model & Reasoning Configuration" in text
    assert "gemini-3.8-flash" in text
    assert "high" in text

    buttons = markup["inline_keyboard"]
    assert any(b[0]["callback_data"] == "model:gemini-3.8-flash" for b in buttons if b)
    # Check bottom control row
    bottom_row = buttons[-1]
    assert any(b["callback_data"] == "model:refresh" for b in bottom_row)


def test_parse_agy_models():
    from gateway.ui_keyboards import parse_agy_models
    raw_output = """Fetching available models...
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-3.8-flash-low\tGemini 3.8 Flash (Low)
gemini-3.1-pro-high\tGemini 3.1 Pro (High)
claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)
gpt-oss-120b-medium\tGPT-OSS 120B (Medium)
"""
    parsed = parse_agy_models(raw_output)
    assert len(parsed) == 4
    model_ids = [m["id"] for m in parsed]
    assert "gemini-3.8-flash" in model_ids
    assert "gemini-3.1-pro" in model_ids
    assert "claude-sonnet-4-6" in model_ids
    assert "gpt-oss-120b-medium" in model_ids


def test_parse_agy_models_preserves_distinct_claude_and_xhigh_labels():
    from gateway.ui_keyboards import parse_agy_models
    raw_output = """Fetching available models...
gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)
gemini-4-hydrogen-xhigh\tGemini 4 Hydrogen (XHigh)
gemini-4-hydrogen-medium\tGemini 4 Hydrogen (Medium)
claude-opus-5-5-low\tClaude Opus 5.5 (Low)
claude-opus-5-5-medium\tClaude Opus 5.5 (Medium)
claude-opus-5-5-high\tClaude Opus 5.5 (High)
"""
    parsed = parse_agy_models(raw_output)
    names_by_id = {m["id"]: m["name"] for m in parsed}
    assert names_by_id["gemini-4-hydrogen"] == "Gemini 4 Hydrogen"
    assert names_by_id["gemini-4-hydrogen-xhigh"] == "Gemini 4 Hydrogen (XHigh)"
    assert names_by_id["claude-opus-5-5-low"] == "Claude Opus 5.5 (Low)"
    assert names_by_id["claude-opus-5-5-medium"] == "Claude Opus 5.5 (Medium)"
    assert names_by_id["claude-opus-5-5-high"] == "Claude Opus 5.5 (High)"

    # Ensure gemini-4-hydrogen-xhigh only highlights the XHigh entry, not base gemini-4-hydrogen
    _, markup = build_model_keyboard("gemini-4-hydrogen-xhigh", "high", models=parsed)
    btn_texts = [row[0]["text"] for row in markup["inline_keyboard"][:-1]]
    assert any("✓ Gemini 4 Hydrogen (XHigh)" == t for t in btn_texts)
    assert not any("✓ Gemini 4 Hydrogen" == t for t in btn_texts)
    # Bottom row uses compact "⚡ Effort" label so it doesn't truncate on mobile
    assert markup["inline_keyboard"][-1][0]["text"] == "⚡ Effort"


@pytest.mark.asyncio
async def test_fetch_agy_models_fallback():
    from gateway.ui_keyboards import fetch_agy_models, DEFAULT_STATIC_MODELS
    # When invalid binary passed, falls back gracefully
    models = await fetch_agy_models(agy_bin="/non/existent/bin", force_refresh=True)
    assert len(models) >= 2
    assert any(m["id"] == "gemini-3.8-flash" for m in models)


def test_build_effort_keyboard():
    text, markup = build_effort_keyboard("gemini-3.8-flash", "medium")
    assert "Reasoning Effort Setting" in text
    assert "medium" in text

    buttons = markup["inline_keyboard"]
    effort_row = buttons[0]
    assert any(b["callback_data"] == "effort:medium" for b in effort_row)


def test_build_adaptive_bot_commands():
    from gateway.session_manager import ChatSession
    from gateway.ui_keyboards import (
        ALL_BOT_COMMAND_SPECS,
        build_adaptive_bot_commands,
        build_artifacts_keyboard,
        build_help_keyboard,
        build_skills_keyboard,
        build_usage_keyboard,
    )

    # 1. Empty session -> returns all registered commands with help pinned at index 0
    cmds = build_adaptive_bot_commands(None)
    assert len(cmds) == len(ALL_BOT_COMMAND_SPECS)
    assert cmds[0]["command"] == "help"

    # 2. Session with LRU usage -> most recently used command bubbles up immediately after help
    session = ChatSession(
        chat_id=123,
        command_stats={"model": 1, "skills": 5, "artifacts": 10},
        command_last_used={
            "model": 1700000100.0,
            "skills": 1700000200.0,
            "artifacts": 1700000300.0,  # Most recently used
            "help": 1700000400.0,       # Pinned command
        },
    )
    adaptive_cmds = build_adaptive_bot_commands(session)
    assert len(adaptive_cmds) == len(ALL_BOT_COMMAND_SPECS)
    assert adaptive_cmds[0]["command"] == "help"       # Pinned at index 0
    assert adaptive_cmds[1]["command"] == "artifacts"  # t=300
    assert adaptive_cmds[2]["command"] == "skills"     # t=200
    assert adaptive_cmds[3]["command"] == "model"      # t=100

    # 3. Explicit max_commands limit
    limited_cmds = build_adaptive_bot_commands(session, max_commands=4)
    assert len(limited_cmds) == 4
    assert [c["command"] for c in limited_cmds] == ["help", "artifacts", "skills", "model"]

    # 4. Test new keyboards
    hk = build_help_keyboard()
    assert any(any(b["callback_data"] == "usage:show" for b in row) for row in hk["inline_keyboard"])

    uk = build_usage_keyboard()
    assert any(any(b["callback_data"] == "usage:refresh" for b in row) for row in uk["inline_keyboard"])

    sk = build_skills_keyboard()
    assert any(any(b["callback_data"] == "skills:show" for b in row) for row in sk["inline_keyboard"])

    ak = build_artifacts_keyboard([{"name": "test_report.html"}])
    assert any(any(b["callback_data"] == "artifact:dl:0" for b in row) for row in ak["inline_keyboard"])


def test_lru_command_sequence_progression():
    from gateway.session_manager import ChatSession
    from gateway.ui_keyboards import COMMAND_ALIASES, build_adaptive_bot_commands

    session = ChatSession(chat_id=999)

    # Initial order: help at 0, usage at 1, status at 2
    cmds = build_adaptive_bot_commands(session)
    assert cmds[0]["command"] == "help"
    assert cmds[1]["command"] == "usage"

    # Step 1: User runs /export at t=100
    session.command_last_used["export"] = 100.0
    cmds = build_adaptive_bot_commands(session)
    assert cmds[0]["command"] == "help"
    assert cmds[1]["command"] == "export"

    # Step 2: User runs /projects at t=200 -> projects moves to front (right below help)
    session.command_last_used["projects"] = 200.0
    cmds = build_adaptive_bot_commands(session)
    assert cmds[0]["command"] == "help"
    assert cmds[1]["command"] == "projects"
    assert cmds[2]["command"] == "export"

    # Step 3: User runs alias /quota -> mapped to 'usage' at t=300
    canonical = COMMAND_ALIASES.get("quota", "quota")
    assert canonical == "usage"
    session.command_last_used[canonical] = 300.0
    cmds = build_adaptive_bot_commands(session)
    assert cmds[0]["command"] == "help"
    assert cmds[1]["command"] == "usage"
    assert cmds[2]["command"] == "projects"
    assert cmds[3]["command"] == "export"

    # Step 4: User runs /help at t=400 -> help stays at index 0, usage at index 1
    session.command_last_used["help"] = 400.0
    cmds = build_adaptive_bot_commands(session)
    assert cmds[0]["command"] == "help"
    assert cmds[1]["command"] == "usage"
    assert cmds[2]["command"] == "projects"
    assert cmds[3]["command"] == "export"


def test_build_decision_and_action_keyboards():
    from gateway.message_formatter import AgyActionItem
    from gateway.ui_keyboards import build_actions_keyboard, build_decision_keyboard

    # Test decision keyboard
    options = ["FastAPI (Recommended)", "Flask", "Django"]
    dk = build_decision_keyboard(options)
    buttons = dk["inline_keyboard"]
    assert len(buttons) >= 2
    assert buttons[0][0]["callback_data"] == "dec:0"
    assert "FastAPI" in buttons[0][0]["text"]
    assert buttons[1][0]["callback_data"] == "dec:1" or (len(buttons[0]) > 1 and buttons[0][1]["callback_data"] == "dec:1")

    # Test actions keyboard
    actions = [
        AgyActionItem(label="🧪 Run Tests", action="pytest -v"),
        AgyActionItem(label="📝 View Diff", action="/diff"),
    ]
    ak = build_actions_keyboard(actions)
    abuttons = ak["inline_keyboard"]
    assert len(abuttons) >= 1
    assert abuttons[0][0]["callback_data"] == "act:0"
    assert "Run Tests" in abuttons[0][0]["text"]


def test_build_reply_keyboard_and_webapp_button():
    from gateway.ui_keyboards import build_reply_keyboard, build_webapp_viewer_button

    # 1. Reply keyboard with placeholder
    rk = build_reply_keyboard(placeholder="🎯 Type to steer...", is_busy=True)
    assert rk["resize_keyboard"] is True
    assert rk["is_persistent"] is True
    assert rk["input_field_placeholder"] == "🎯 Type to steer..."
    assert len(rk["keyboard"][0]) == 4

    # 2. WebApp viewer button (HTTPS -> web_app)
    wb_https = build_webapp_viewer_button("https://webapp.test/view/123/doc.html", "🌐 View Diff")
    assert wb_https["text"] == "🌐 View Diff"
    assert wb_https["web_app"]["url"] == "https://webapp.test/view/123/doc.html"

    # 3. WebApp viewer button fallback (HTTP -> url)
    wb_http = build_webapp_viewer_button("http://127.0.0.1:8765/view/123/doc.html", "🌐 View Tree")
    assert wb_http["text"] == "🌐 View Tree"
    assert "web_app" not in wb_http
    assert wb_http["url"] == "http://127.0.0.1:8765/view/123/doc.html"


def test_build_pinned_banner_keyboard():
    from gateway.ui_keyboards import build_pinned_banner_keyboard

    kb = build_pinned_banner_keyboard()
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    assert rows[0][0]["text"] == "📊 Status"
    assert rows[0][1]["text"] == "📁 Projects"
    assert rows[1][0]["text"] == "🪙 Quota"
    assert rows[1][1]["text"] == "📖 Help"


def test_build_voice_keyboard_and_play_button():
    from gateway.ui_keyboards import build_voice_keyboard, build_voice_play_button

    # 1. Voice keyboard with ondemand active
    text, kb = build_voice_keyboard("ondemand")
    assert "Voice Broadcast Settings" in text
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) == 4
    assert "🔘" in rows[1][0]["text"]  # ondemand has active radio
    assert "⚪" in rows[0][0]["text"]  # disable is inactive

    # 2. Voice play button
    btn = build_voice_play_button("v_123_456")
    assert btn["text"] == "🔊 Send Audio"
    assert btn["callback_data"] == "voice:v_123_456"


def test_build_html_keyboard_and_help_button():
    from gateway.ui_keyboards import build_help_keyboard, build_html_keyboard

    text, kb = build_html_keyboard("explicit")
    assert "HTML Attachment Settings" in text
    rows = kb["inline_keyboard"]
    assert len(rows) == 4
    assert "🔘" in rows[1][0]["text"]  # explicit active
    assert "⚪" in rows[0][0]["text"]  # auto inactive
    assert rows[0][0]["callback_data"] == "html_mode:auto"
    assert rows[1][0]["callback_data"] == "html_mode:explicit"
    assert rows[2][0]["callback_data"] == "html_mode:off"

    hk = build_help_keyboard()
    assert any(any(b["callback_data"] == "html:show" for b in row) for row in hk["inline_keyboard"])





