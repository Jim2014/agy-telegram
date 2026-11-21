"""Unit tests for gateway.session_manager with project-based architecture."""

import json
from pathlib import Path
import pytest
from gateway.session_manager import ChatSession, SessionManager


def test_session_crud():
    mgr = SessionManager()
    session = mgr.get_session(1001)
    assert session.chat_id == 1001
    assert session.active_project is None
    assert session.projects == {}
    assert session.conversation_id is None
    assert session.active_path() is None
    assert session.model is None
    assert session.effort is None
    assert session.is_busy is False

    mgr.create_project(1001, "myproj", "/tmp")
    s = mgr.get_session(1001)
    assert s.active_project == "myproj"
    assert s.active_path() == str(Path("/tmp").resolve())
    assert s.conversation_id is None

    mgr.set_conversation_id(1001, "conv-uuid-1234")
    mgr.set_model(1001, "gemini-2.5-flash")
    mgr.set_effort(1001, "high")

    s = mgr.get_session(1001)
    assert s.conversation_id == "conv-uuid-1234"
    assert s.model == "gemini-2.5-flash"
    assert s.effort == "high"
    assert s.working_dir == str(Path("/tmp").resolve())

    # Reset conversation preserves project and preferences
    mgr.reset_conversation(1001)
    s2 = mgr.get_session(1001)
    assert s2.conversation_id is None
    assert s2.active_project == "myproj"
    assert s2.model == "gemini-2.5-flash"
    assert s2.effort == "high"


def test_busy_flag():
    mgr = SessionManager()
    assert mgr.is_busy(2002) is False
    mgr.set_busy(2002, True)
    assert mgr.is_busy(2002) is True
    mgr.set_busy(2002, False)
    assert mgr.is_busy(2002) is False


def test_project_lifecycle():
    mgr = SessionManager()
    chat_id = 5005

    # Create project
    mgr.create_project(chat_id, "p1", "/tmp/p1")
    mgr.set_conversation_id(chat_id, "conv-p1")
    s = mgr.get_session(chat_id)
    assert s.active_project == "p1"
    assert s.conversation_id == "conv-p1"

    # Creating duplicate project name raises ValueError
    with pytest.raises(ValueError, match="already exists"):
        mgr.create_project(chat_id, "p1", "/tmp/p1_other")

    # Create second project
    mgr.create_project(chat_id, "p2", "/tmp/p2")
    mgr.set_conversation_id(chat_id, "conv-p2")
    s = mgr.get_session(chat_id)
    assert s.active_project == "p2"
    assert s.conversation_id == "conv-p2"

    # Switch back to p1
    mgr.open_project(chat_id, "p1")
    s = mgr.get_session(chat_id)
    assert s.active_project == "p1"
    assert s.conversation_id == "conv-p1"

    # Open nonexistent project raises KeyError
    with pytest.raises(KeyError):
        mgr.open_project(chat_id, "p_nonexistent")

    # Update project path
    mgr.set_project_path(chat_id, "p1", "/tmp/p1_updated")
    assert mgr.get_session(chat_id).active_path() == str(Path("/tmp/p1_updated").resolve())

    # Delete active project p1 -> active becomes None
    assert mgr.delete_project(chat_id, "p1") is True
    s = mgr.get_session(chat_id)
    assert s.active_project is None
    assert s.conversation_id is None
    assert "p1" not in s.projects
    assert "p2" in s.projects

    # Delete nonexistent project returns False
    assert mgr.delete_project(chat_id, "p_unknown") is False

    # Reset with no active project raises ValueError
    with pytest.raises(ValueError, match="No active project"):
        mgr.reset_conversation(chat_id)


def test_persistence(tmp_path):
    persist_file = tmp_path / "sessions.json"
    mgr1 = SessionManager(persistence_file=persist_file)
    mgr1.create_project(3003, "proj_alpha", "/tmp/alpha")
    mgr1.set_conversation_id(3003, "conv-alpha-999")
    mgr1.set_model(3003, "gemini-2.5-pro")
    mgr1.set_effort(3003, "medium")

    mgr1.create_project(3003, "proj_beta", "/tmp/beta")
    mgr1.set_conversation_id(3003, "conv-beta-888")

    assert persist_file.exists()

    # Load in new instance
    mgr2 = SessionManager(persistence_file=persist_file)
    s = mgr2.get_session(3003)
    assert s.active_project == "proj_beta"
    assert s.conversation_id == "conv-beta-888"
    assert s.model == "gemini-2.5-pro"
    assert s.effort == "medium"
    assert "proj_alpha" in s.projects
    assert s.projects["proj_alpha"]["conversation_id"] == "conv-alpha-999"


def test_legacy_migration(tmp_path):
    persist_file = tmp_path / "legacy_sessions.json"
    legacy_data = {
        "100": {
            "chat_id": 100,
            "conversation_id": "conv-legacy-1",
            "model": "gemini-2.5-pro",
            "effort": "high",
            "working_dir": "/tmp/legacy_dir",
            "created_at": 1000.0,
            "last_active_at": 2000.0,
        },
        "200": {
            "chat_id": 200,
            "conversation_id": "conv-legacy-2",
            "model": None,
            "effort": None,
            "working_dir": None,
            "created_at": 1000.0,
            "last_active_at": 2000.0,
        },
    }
    with open(persist_file, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    mgr = SessionManager(persistence_file=persist_file)

    # Verify chat 100 migration
    s100 = mgr.get_session(100)
    assert s100.active_project == "legacy_dir"
    assert s100.conversation_id == "conv-legacy-1"
    assert s100.active_path() == str(Path("/tmp/legacy_dir").resolve())
    assert s100.model == "gemini-2.5-pro"
    assert s100.effort == "high"

    # Verify chat 200 migration
    s200 = mgr.get_session(200)
    assert s200.active_project == "default"
    assert s200.conversation_id == "conv-legacy-2"
    assert "default" in s200.projects


def test_turn_and_session_stats(tmp_path):
    persist_file = tmp_path / "stats_sessions.json"
    mgr = SessionManager(persistence_file=persist_file)

    mgr.create_project(300, "proj_stats", "/tmp/stats")
    mgr.set_conversation_id(300, "conv-stats-1")

    # Initial stats
    stats0 = mgr.get_active_stats(300)
    assert stats0["total_turns"] == 0
    assert stats0["total_tokens"] == 0

    # Record turn 1
    mgr.record_turn_stats(
        chat_id=300,
        turn_tokens=1500,
        turn_thinking=200,
        turn_duration=3.5,
        session_total_tokens=1500,
        session_total_thinking=200,
    )
    stats1 = mgr.get_active_stats(300)
    assert stats1["total_turns"] == 1
    assert stats1["total_tokens"] == 1500
    assert stats1["thinking_tokens"] == 200
    assert stats1["total_duration_seconds"] == 3.5

    # Record turn 2
    mgr.record_turn_stats(
        chat_id=300,
        turn_tokens=2500,
        turn_thinking=300,
        turn_duration=4.2,
        session_total_tokens=4000,
        session_total_thinking=500,
    )
    stats2 = mgr.get_active_stats(300)
    assert stats2["total_turns"] == 2
    assert stats2["total_tokens"] == 4000
    assert stats2["thinking_tokens"] == 500
    assert stats2["total_duration_seconds"] == 7.7

    # Reset conversation zeroes stats
    mgr.reset_conversation(300)
    stats_reset = mgr.get_active_stats(300)
    assert stats_reset["total_turns"] == 0
    assert stats_reset["total_tokens"] == 0
    assert stats_reset["thinking_tokens"] == 0
    assert stats_reset["total_duration_seconds"] == 0.0


def test_command_usage_tracking(tmp_path):
    persist_file = tmp_path / "cmd_stats.json"
    mgr = SessionManager(persistence_file=persist_file)

    mgr.record_command_usage(400, "/usage")
    mgr.record_command_usage(400, "usage")
    mgr.record_command_usage(400, "/skills")

    s = mgr.get_session(400)
    assert s.command_stats["usage"] == 2
    assert s.command_stats["skills"] == 1
    assert "usage" in s.command_last_used

    # Persistence verification
    mgr2 = SessionManager(persistence_file=persist_file)
    s2 = mgr2.get_session(400)
    assert s2.command_stats["usage"] == 2
    assert s2.command_stats["skills"] == 1


def test_html_mode_persistence(tmp_path):
    persist_file = tmp_path / "html_mode.json"
    mgr1 = SessionManager(persistence_file=persist_file)
    assert mgr1.get_session(500).html_mode == "auto"

    mgr1.set_html_mode(500, "explicit")
    assert mgr1.get_session(500).html_mode == "explicit"

    mgr1.set_html_mode(500, "disable")
    assert mgr1.get_session(500).html_mode == "off"

    mgr2 = SessionManager(persistence_file=persist_file)
    assert mgr2.get_session(500).html_mode == "off"


def test_permission_preset_persistence(tmp_path):
    persist_file = tmp_path / "perm_sessions.json"
    mgr1 = SessionManager(persistence_file=persist_file)
    assert mgr1.get_session(600).permission_preset is None

    mgr1.set_permission_preset(600, "auto")
    assert mgr1.get_session(600).permission_preset == "auto"

    mgr2 = SessionManager(persistence_file=persist_file)
    assert mgr2.get_session(600).permission_preset == "auto"

    mgr2.set_permission_preset(600, None)
    mgr3 = SessionManager(persistence_file=persist_file)
    assert mgr3.get_session(600).permission_preset is None

