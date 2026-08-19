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
