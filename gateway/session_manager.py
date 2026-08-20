"""Session and conversation state management for agy-telegram."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """State for a single Telegram chat with multiple project workspaces."""

    chat_id: int
    active_project: Optional[str] = None
    projects: Dict[str, dict] = field(default_factory=dict)
    model: Optional[str] = None
    effort: Optional[str] = None
    is_busy: bool = False
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    command_stats: Dict[str, int] = field(default_factory=dict)
    command_last_used: Dict[str, float] = field(default_factory=dict)
    pending_decisions: List[str] = field(default_factory=list)
    pending_actions: List[Dict[str, str]] = field(default_factory=list)
    pinned_message_id: Optional[int] = None

    @property
    def conversation_id(self) -> Optional[str]:
        """Read-only property returning conversation_id of active project (or None)."""
        if self.active_project and self.active_project in self.projects:
            return self.projects[self.active_project].get("conversation_id")
        return None

    def active_path(self) -> Optional[str]:
        """Return the filesystem path of current active project (or None)."""
        if self.active_project and self.active_project in self.projects:
            return self.projects[self.active_project].get("path")
        return None

    @property
    def working_dir(self) -> Optional[str]:
        """Backward-compatibility property for active project path."""
        return self.active_path()

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to serializable dict (excluding runtime transient flags)."""
        return {
            "chat_id": self.chat_id,
            "active_project": self.active_project,
            "model": self.model,
            "effort": self.effort,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "projects": self.projects,
            "command_stats": self.command_stats,
            "command_last_used": self.command_last_used,
            "pinned_message_id": self.pinned_message_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChatSession:
        """Deserialize ChatSession from dict with lossless migration for legacy schemas."""
        projects = data.get("projects")
        active_project = data.get("active_project")

        # Migration logic for legacy flat format
        if projects is None:
            projects = {}
            old_wdir = data.get("working_dir")
            old_conv = data.get("conversation_id")
            if old_wdir or old_conv or ("working_dir" in data or "conversation_id" in data):
                name = Path(old_wdir).name if (old_wdir and str(old_wdir).strip()) else "default"
                path = str(Path(old_wdir).resolve()) if (old_wdir and str(old_wdir).strip()) else str(Path.cwd().resolve())
                projects[name] = {
                    "name": name,
                    "path": path,
                    "conversation_id": old_conv,
                    "last_active_at": data.get("last_active_at", time.time()),
                }
                active_project = name

        return cls(
            chat_id=int(data["chat_id"]),
            active_project=active_project,
            projects=projects,
            model=data.get("model"),
            effort=data.get("effort"),
            created_at=float(data.get("created_at", time.time())),
            last_active_at=float(data.get("last_active_at", time.time())),
            command_stats=data.get("command_stats", {}),
            command_last_used=data.get("command_last_used", {}),
            pinned_message_id=data.get("pinned_message_id"),
        )


class SessionManager:
    """Manages chat sessions with project-based multi-workspace persistence."""

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

    def create_project(self, chat_id: int, name: str, path: str) -> ChatSession:
        """Create a new project, set as active, and initialize conversation_id to None."""
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty")
        session = self.get_session(chat_id)
        if name in session.projects:
            raise ValueError(f"Project '{name}' already exists")

        resolved_path = str(Path(path).expanduser().resolve())
        session.projects[name] = {
            "name": name,
            "path": resolved_path,
            "conversation_id": None,
            "last_active_at": time.time(),
            "stats": {
                "total_turns": 0,
                "total_tokens": 0,
                "thinking_tokens": 0,
                "total_duration_seconds": 0.0,
                "session_started_at": time.time(),
            },
        }
        session.active_project = name
        session.last_active_at = time.time()
        self.save()
        return session

    def open_project(self, chat_id: int, name: str) -> ChatSession:
        """Switch active project to existing project name."""
        name = name.strip()
        session = self.get_session(chat_id)
        if name not in session.projects:
            raise KeyError(f"Project '{name}' does not exist")

        session.active_project = name
        session.projects[name]["last_active_at"] = time.time()
        session.last_active_at = time.time()
        self.save()
        return session

    def delete_project(self, chat_id: int, name: str) -> bool:
        """Delete project. If it was active, active_project is reset to None."""
        name = name.strip()
        session = self.get_session(chat_id)
        if name not in session.projects:
            return False

        del session.projects[name]
        if session.active_project == name:
            session.active_project = None
        session.last_active_at = time.time()
        self.save()
        return True

    def reset_conversation(self, chat_id: int) -> ChatSession:
        """Reset conversation_id and cumulative statistics for the current active project."""
        session = self.get_session(chat_id)
        if not session.active_project or session.active_project not in session.projects:
            raise ValueError("No active project to reset conversation")

        proj = session.projects[session.active_project]
        proj["conversation_id"] = None
        proj["last_active_at"] = time.time()
        proj["stats"] = {
            "total_turns": 0,
            "total_tokens": 0,
            "thinking_tokens": 0,
            "total_duration_seconds": 0.0,
            "session_started_at": time.time(),
        }
        session.last_active_at = time.time()
        self.save()
        return session

    def reset_session(self, chat_id: int) -> ChatSession:
        """Alias for reset_conversation."""
        return self.reset_conversation(chat_id)

    def set_conversation_id(self, chat_id: int, conversation_id: Optional[str]) -> None:
        """Record the active agy conversation ID for the current active project."""
        session = self.get_session(chat_id)
        if session.active_project and session.active_project in session.projects:
            proj = session.projects[session.active_project]
            if proj.get("conversation_id") != conversation_id:
                proj["conversation_id"] = conversation_id
                if "stats" not in proj or not isinstance(proj["stats"], dict):
                    proj["stats"] = {
                        "total_turns": 0,
                        "total_tokens": 0,
                        "thinking_tokens": 0,
                        "total_duration_seconds": 0.0,
                        "session_started_at": time.time(),
                    }
            proj["last_active_at"] = time.time()
        session.last_active_at = time.time()
        self.save()

    def record_turn_stats(
        self,
        chat_id: int,
        turn_tokens: Optional[int] = None,
        turn_thinking: Optional[int] = None,
        turn_duration: Optional[float] = None,
        session_total_tokens: Optional[int] = None,
        session_total_thinking: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """Record per-turn metrics and update cumulative session stats."""
        session = self.get_session(chat_id)
        if not session.active_project or session.active_project not in session.projects:
            return

        if turn_tokens is None and "total_tokens" in kwargs:
            turn_tokens = kwargs["total_tokens"]
        if turn_thinking is None and "thinking_tokens" in kwargs:
            turn_thinking = kwargs["thinking_tokens"]
        if turn_duration is None and "duration_seconds" in kwargs:
            turn_duration = kwargs["duration_seconds"]

        proj = session.projects[session.active_project]
        if "stats" not in proj or not isinstance(proj["stats"], dict):
            proj["stats"] = {
                "total_turns": 0,
                "total_tokens": 0,
                "thinking_tokens": 0,
                "total_duration_seconds": 0.0,
                "session_started_at": time.time(),
            }

        safe_duration = max(0.0, float(turn_duration)) if turn_duration is not None else 0.0
        safe_turn_tokens = max(0, int(turn_tokens)) if turn_tokens is not None else 0
        safe_turn_thinking = max(0, int(turn_thinking)) if turn_thinking is not None else 0

        stats = proj["stats"]
        stats["total_turns"] = stats.get("total_turns", 0) + 1
        stats["total_duration_seconds"] = stats.get("total_duration_seconds", 0.0) + safe_duration

        if session_total_tokens is not None and session_total_tokens > 0:
            stats["total_tokens"] = session_total_tokens
        else:
            stats["total_tokens"] = stats.get("total_tokens", 0) + safe_turn_tokens

        if session_total_thinking is not None and session_total_thinking > 0:
            stats["thinking_tokens"] = session_total_thinking
        else:
            stats["thinking_tokens"] = stats.get("thinking_tokens", 0) + safe_turn_thinking

        stats["last_turn_tokens"] = safe_turn_tokens
        stats["last_turn_thinking"] = safe_turn_thinking
        stats["last_turn_duration"] = safe_duration

        session.last_active_at = time.time()
        self.save()

    def get_active_stats(self, chat_id: int) -> Dict[str, Any]:
        """Get cumulative stats for current active project session."""
        session = self.get_session(chat_id)
        if session.active_project and session.active_project in session.projects:
            proj = session.projects[session.active_project]
            stats = proj.get("stats")
            if isinstance(stats, dict):
                return dict(stats)
        return {
            "total_turns": 0,
            "total_tokens": 0,
            "thinking_tokens": 0,
            "total_duration_seconds": 0.0,
            "session_started_at": session.created_at,
        }

    def set_project_path(self, chat_id: int, name: str, path: str) -> None:
        """Update workspace path for a specific project."""
        name = name.strip()
        session = self.get_session(chat_id)
        if name not in session.projects:
            raise KeyError(f"Project '{name}' does not exist")

        resolved_path = str(Path(path).expanduser().resolve())
        session.projects[name]["path"] = resolved_path
        session.projects[name]["last_active_at"] = time.time()
        session.last_active_at = time.time()
        self.save()

    def list_projects(self, chat_id: int) -> Dict[str, dict]:
        """Return dict of all projects for this chat."""
        session = self.get_session(chat_id)
        return dict(session.projects)

    def set_working_dir(self, chat_id: int, working_dir: Optional[str]) -> None:
        """Update path for active project (backward compatibility)."""
        session = self.get_session(chat_id)
        if session.active_project and working_dir:
            self.set_project_path(chat_id, session.active_project, working_dir)
        elif working_dir:
            resolved = str(Path(working_dir).expanduser().resolve())
            proj_name = Path(resolved).name or "default"
            if proj_name in session.projects:
                self.open_project(chat_id, proj_name)
                self.set_project_path(chat_id, proj_name, resolved)
            else:
                self.create_project(chat_id, proj_name, resolved)

    def set_model(self, chat_id: int, model: Optional[str]) -> None:
        """Set or clear model override (chat-level)."""
        session = self.get_session(chat_id)
        session.model = model.strip() if model else None
        session.last_active_at = time.time()
        self.save()

    def set_effort(self, chat_id: int, effort: Optional[str]) -> None:
        """Set or clear effort override (chat-level: low, medium, high)."""
        session = self.get_session(chat_id)
        if effort:
            eff = effort.strip().lower()
            if eff in ("low", "medium", "high"):
                session.effort = eff
        else:
            session.effort = None
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
        """Load sessions from persistence file (or legacy fallback) if exists."""
        target_file = self.persistence_file
        if target_file and not target_file.is_file():
            # Check legacy root sessions.json fallback
            legacy_file = Path("sessions.json")
            if legacy_file.is_file():
                target_file = legacy_file

        if not target_file or not target_file.is_file():
            return
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                self._sessions[int(k)] = ChatSession.from_dict(v)
            logger.info("Loaded %d sessions from %s", len(self._sessions), target_file)
        except Exception as e:
            logger.warning("Failed to load sessions from %s: %s", target_file, e)

    def record_command_usage(self, chat_id: int, command: str) -> None:
        """Record command usage frequency and timestamp for adaptive menu ordering."""
        clean_cmd = command.strip().lstrip("/").lower().split("@")[0]
        if not clean_cmd:
            return
        session = self.get_session(chat_id)
        session.command_stats[clean_cmd] = session.command_stats.get(clean_cmd, 0) + 1
        session.command_last_used[clean_cmd] = time.time()
        session.last_active_at = time.time()
        self.save()

    def save(self) -> None:
        """Save sessions to persistence file."""
        if not self.persistence_file:
            return
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            data = {str(k): v.to_dict() for k, v in self._sessions.items()}
            temp_path = self.persistence_file.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.persistence_file)
        except Exception as e:
            logger.warning("Failed to save sessions to %s: %s", self.persistence_file, e)


if __name__ == "__main__":
    test_file = Path("runtime/test_sessions_tmp.json")
    if test_file.exists():
        test_file.unlink()

    mgr = SessionManager(test_file)
    s = mgr.get_session(12345)
    print("Initial session:", s)
    mgr.create_project(12345, "demo", "/tmp")
    mgr.set_conversation_id(12345, "conv-abc-123")
    mgr.set_model(12345, "gemini-2.5-pro")
    mgr.set_effort(12345, "high")
    print("Updated session:", mgr.get_session(12345))
    print("Active path:", mgr.get_session(12345).active_path())
    print("Conversation ID:", mgr.get_session(12345).conversation_id)

    mgr.reset_conversation(12345)
    print("Reset session conv:", mgr.get_session(12345).conversation_id)

    # Persistence verification
    mgr2 = SessionManager(test_file)
    s2 = mgr2.get_session(12345)
    assert s2.active_project == "demo"
    assert "demo" in s2.projects
    assert s2.model == "gemini-2.5-pro"

    if test_file.exists():
        test_file.unlink()
    print("SessionManager self-test passed!")
