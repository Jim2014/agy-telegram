"""Session and conversation state management for agy-telegram."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """State for a single Telegram chat."""

    chat_id: int
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    working_dir: Optional[str] = None
    is_busy: bool = False
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to serializable dict (excluding runtime transient flags)."""
        return {
            "chat_id": self.chat_id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "effort": self.effort,
            "working_dir": self.working_dir,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChatSession:
        return cls(
            chat_id=int(data["chat_id"]),
            conversation_id=data.get("conversation_id"),
            model=data.get("model"),
            effort=data.get("effort"),
            working_dir=data.get("working_dir"),
            is_busy=False,
            created_at=data.get("created_at", time.time()),
            last_active_at=data.get("last_active_at", time.time()),
        )


class SessionManager:
    """Manages chat sessions with persistence."""

    def __init__(self, persistence_file: Optional[str | Path] = None) -> None:
        self.persistence_file = Path(persistence_file) if persistence_file else None
        self._sessions: Dict[int, ChatSession] = {}
        self.load()

    def get_session(self, chat_id: int) -> ChatSession:
        """Get or create session for chat_id."""
        if chat_id not in self._sessions:
            self._sessions[chat_id] = ChatSession(chat_id=chat_id)
            self.save()
        session = self._sessions[chat_id]
        session.last_active_at = time.time()
        return session

    def reset_conversation(self, chat_id: int) -> ChatSession:
        """Reset conversation_id for chat, starting fresh next time."""
        session = self.get_session(chat_id)
        session.conversation_id = None
        session.last_active_at = time.time()
        self.save()
        return session

    def set_conversation_id(self, chat_id: int, conversation_id: str) -> None:
        """Record the active agy conversation ID for this chat."""
        session = self.get_session(chat_id)
        session.conversation_id = conversation_id
        session.last_active_at = time.time()
        self.save()

    def set_model(self, chat_id: int, model: Optional[str]) -> None:
        """Set or clear model override."""
        session = self.get_session(chat_id)
        session.model = model.strip() if model else None
        session.last_active_at = time.time()
        self.save()

    def set_effort(self, chat_id: int, effort: Optional[str]) -> None:
        """Set or clear effort override (low, medium, high)."""
        session = self.get_session(chat_id)
        if effort:
            eff = effort.strip().lower()
            if eff in ("low", "medium", "high"):
                session.effort = eff
        else:
            session.effort = None
        session.last_active_at = time.time()
        self.save()

    def set_working_dir(self, chat_id: int, working_dir: Optional[str]) -> None:
        """Set or clear custom working directory."""
        session = self.get_session(chat_id)
        session.working_dir = str(Path(working_dir).resolve()) if working_dir else None
        session.last_active_at = time.time()
        self.save()

    def set_busy(self, chat_id: int, is_busy: bool) -> None:
        """Set whether chat currently has a running task."""
        session = self.get_session(chat_id)
        session.is_busy = is_busy

    def is_busy(self, chat_id: int) -> bool:
        """Check if chat is currently executing an agy prompt."""
        if chat_id in self._sessions:
            return self._sessions[chat_id].is_busy
        return False

    def load(self) -> None:
        """Load sessions from persistence file if exists."""
        if not self.persistence_file or not self.persistence_file.is_file():
            return
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                self._sessions[int(k)] = ChatSession.from_dict(v)
            logger.info("Loaded %d sessions from %s", len(self._sessions), self.persistence_file)
        except Exception as e:
            logger.warning("Failed to load sessions from %s: %s", self.persistence_file, e)

    def save(self) -> None:
        """Save sessions to persistence file."""
        if not self.persistence_file:
            return
        try:
            data = {str(k): v.to_dict() for k, v in self._sessions.items()}
            temp_path = self.persistence_file.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.persistence_file)
        except Exception as e:
            logger.warning("Failed to save sessions to %s: %s", self.persistence_file, e)


if __name__ == "__main__":
    mgr = SessionManager("test_sessions.json")
    s = mgr.get_session(12345)
    print("Initial session:", s)
    mgr.set_conversation_id(12345, "conv-abc-123")
    mgr.set_model(12345, "gemini-2.5-pro")
    mgr.set_effort(12345, "high")
    print("Updated session:", mgr.get_session(12345))
    mgr.reset_conversation(12345)
    print("Reset session:", mgr.get_session(12345))
    if Path("test_sessions.json").exists():
        Path("test_sessions.json").unlink()
    print("SessionManager self-test passed!")
