"""Unit tests for gateway.session_manager."""

import os
from pathlib import Path
from gateway.session_manager import ChatSession, SessionManager


def test_session_crud():
    mgr = SessionManager()
    session = mgr.get_session(1001)
    assert session.chat_id == 1001
    assert session.conversation_id is None
    assert session.model is None
    assert session.effort is None
    assert session.is_busy is False

    mgr.set_conversation_id(1001, "conv-uuid-1234")
    mgr.set_model(1001, "gemini-2.5-flash")
    mgr.set_effort(1001, "high")
    mgr.set_working_dir(1001, "/tmp")

    s = mgr.get_session(1001)
    assert s.conversation_id == "conv-uuid-1234"
    assert s.model == "gemini-2.5-flash"
    assert s.effort == "high"
    assert s.working_dir == str(Path("/tmp").resolve())

    # Reset conversation preserves preferences
    mgr.reset_conversation(1001)
    s2 = mgr.get_session(1001)
    assert s2.conversation_id is None
    assert s2.model == "gemini-2.5-flash"
    assert s2.effort == "high"


def test_busy_flag():
    mgr = SessionManager()
    assert mgr.is_busy(2002) is False
    mgr.set_busy(2002, True)
    assert mgr.is_busy(2002) is True
    mgr.set_busy(2002, False)
    assert mgr.is_busy(2002) is False


def test_persistence(tmp_path):
    persist_file = tmp_path / "sessions.json"
    mgr1 = SessionManager(persistence_file=persist_file)
    mgr1.set_conversation_id(3003, "conv-persistent-111")
    mgr1.set_model(3003, "gemini-2.5-pro")

    assert persist_file.exists()

    # Load in new instance
    mgr2 = SessionManager(persistence_file=persist_file)
    s = mgr2.get_session(3003)
    assert s.conversation_id == "conv-persistent-111"
    assert s.model == "gemini-2.5-pro"
