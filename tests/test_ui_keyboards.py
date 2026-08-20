"""Unit tests for gateway.ui_keyboards."""

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
    text, markup = build_model_keyboard("gemini-3.7-flash", "high")
    assert "Model & Reasoning Configuration" in text
    assert "gemini-3.7-flash" in text
    assert "high" in text

    buttons = markup["inline_keyboard"]
    assert any(b[0]["callback_data"] == "model:gemini-3.7-flash" for b in buttons if b)


def test_build_effort_keyboard():
    text, markup = build_effort_keyboard("gemini-3.7-flash", "medium")
    assert "Reasoning Effort Setting" in text
    assert "medium" in text

    buttons = markup["inline_keyboard"]
    effort_row = buttons[0]
    assert any(b["callback_data"] == "effort:medium" for b in effort_row)


def test_build_adaptive_bot_commands():
    from gateway.session_manager import ChatSession
    from gateway.ui_keyboards import (
        build_adaptive_bot_commands,
        build_artifacts_keyboard,
        build_help_keyboard,
        build_skills_keyboard,
        build_usage_keyboard,
    )

    # 1. Empty session -> default commands with help at index 0
    cmds = build_adaptive_bot_commands(None, max_commands=8)
    assert len(cmds) == 8
    assert cmds[0]["command"] == "help"

    # 2. Session with heavy usage of 'artifacts' and 'skills' -> they should bubble up right after help!
    session = ChatSession(
        chat_id=123,
        command_stats={"artifacts": 50, "skills": 45, "usage": 30},
        command_last_used={"artifacts": 1700000000.0, "skills": 1700000000.0, "usage": 1700000000.0},
    )
    adaptive_cmds = build_adaptive_bot_commands(session, max_commands=8)
    assert len(adaptive_cmds) == 8
    assert adaptive_cmds[0]["command"] == "help"
    assert adaptive_cmds[1]["command"] == "artifacts"
    assert adaptive_cmds[2]["command"] == "skills"
    assert adaptive_cmds[3]["command"] == "usage"

    # 3. Test new keyboards
    hk = build_help_keyboard()
    assert any(any(b["callback_data"] == "usage:show" for b in row) for row in hk["inline_keyboard"])

    uk = build_usage_keyboard()
    assert any(any(b["callback_data"] == "usage:refresh" for b in row) for row in uk["inline_keyboard"])

    sk = build_skills_keyboard()
    assert any(any(b["callback_data"] == "skills:show" for b in row) for row in sk["inline_keyboard"])

    ak = build_artifacts_keyboard([{"name": "test_report.html"}])
    assert any(any(b["callback_data"] == "artifact:dl:0" for b in row) for row in ak["inline_keyboard"])


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

    # 1. Voice keyboard with autogen active
    text, kb = build_voice_keyboard("autogen")
    assert "语音播报设置" in text
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) == 4
    assert "🔘" in rows[1][0]["text"]  # autogen has active radio
    assert "⚪" in rows[0][0]["text"]  # disable is inactive

    # 2. Voice play button
    btn = build_voice_play_button("v_123_456")
    assert btn["text"] == "🔊 播放语音"
    assert btn["callback_data"] == "voice:v_123_456"





