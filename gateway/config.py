"""Configuration loader and validator for agy-telegram gateway."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

# Base directory for the agy-telegram project
BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    """Gateway runtime configuration."""

    bot_token: str = ""
    allowed_chat_ids: Set[int] = field(default_factory=set)
    agy_bin_path: str = "/Users/jim/.local/bin/agy"
    agy_working_dir: str = ""
    default_model: str = ""
    default_effort: str = "medium"
    stream_throttle_seconds: float = 1.0
    agy_timeout_seconds: int = 300
    log_level: str = "INFO"
    log_file: str = "runtime/gateway.log"
    sessions_file: str = "runtime/sessions.json"

    @classmethod
    def load(cls, env_path: str | Path | None = None) -> Config:
        """Load configuration from .env and environment variables."""
        if env_path is None:
            default_env = BASE_DIR / ".env"
            if default_env.is_file():
                load_dotenv(dotenv_path=default_env, override=True)
        else:
            load_dotenv(dotenv_path=env_path, override=True)

        bot_token = os.getenv("BOT_TOKEN", "").strip()

        # Parse allowed chat IDs
        raw_allowed = os.getenv("ALLOWED_CHAT_IDS", "").strip()
        allowed_chat_ids: Set[int] = set()
        if raw_allowed:
            for item in raw_allowed.split(","):
                item_str = item.strip()
                if item_str:
                    try:
                        allowed_chat_ids.add(int(item_str))
                    except ValueError:
                        pass

        # agy binary path
        agy_bin = os.getenv("AGY_BIN_PATH", "").strip()
        if not agy_bin:
            # Try default known paths or PATH lookup
            if Path("/Users/jim/.local/bin/agy").is_file():
                agy_bin = "/Users/jim/.local/bin/agy"
            else:
                agy_bin = shutil.which("agy") or "/Users/jim/.local/bin/agy"

        # agy working dir
        working_dir = os.getenv("AGY_WORKING_DIR", "").strip()
        if not working_dir:
            working_dir = str(BASE_DIR)

        # Other options
        default_model = os.getenv("DEFAULT_MODEL", "").strip()
        default_effort = os.getenv("DEFAULT_EFFORT", "medium").strip().lower()
        if default_effort not in ("low", "medium", "high"):
            default_effort = "medium"

        try:
            stream_throttle = float(os.getenv("STREAM_THROTTLE_SECONDS", "1.0"))
        except ValueError:
            stream_throttle = 1.0

        try:
            agy_timeout = int(os.getenv("AGY_TIMEOUT_SECONDS", "300"))
        except ValueError:
            agy_timeout = 300

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        log_file = os.getenv("LOG_FILE", "runtime/gateway.log").strip()
        sessions_file = os.getenv("SESSIONS_FILE", "runtime/sessions.json").strip()

        return cls(
            bot_token=bot_token,
            allowed_chat_ids=allowed_chat_ids,
            agy_bin_path=agy_bin,
            agy_working_dir=working_dir,
            default_model=default_model,
            default_effort=default_effort,
            stream_throttle_seconds=stream_throttle,
            agy_timeout_seconds=agy_timeout,
            log_level=log_level,
            log_file=log_file,
            sessions_file=sessions_file,
        )

    def is_token_valid(self) -> bool:
        """Check if bot token is provided and not a placeholder."""
        if not self.bot_token:
            return False
        if self.bot_token.lower() in ("your_bot_token_here", "placeholder", "your_token", "none"):
            return False
        # Telegram token format: <digits>:<token_secret>
        parts = self.bot_token.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or len(parts[1]) < 10:
            return False
        return True

    def is_chat_allowed(self, chat_id: int) -> bool:
        """Check if chat_id is allowed to interact with the bot."""
        if not self.allowed_chat_ids:
            return True  # Empty whitelist = allow all
        return chat_id in self.allowed_chat_ids

    def get_resolved_agy_bin(self) -> str:
        """Return the resolved path to the agy executable."""
        expanded = os.path.expanduser(self.agy_bin_path)
        if os.path.isabs(expanded) and os.path.isfile(expanded):
            return expanded
        found = shutil.which(self.agy_bin_path)
        if found:
            return found
        return expanded


if __name__ == "__main__":
    cfg = Config.load()
    print("=== agy-telegram Configuration ===")
    print(f"Base Directory:        {BASE_DIR}")
    print(f"Bot Token Configured:  {'YES (valid format)' if cfg.is_token_valid() else 'NO or invalid/placeholder'}")
    print(f"Allowed Chat IDs:      {list(cfg.allowed_chat_ids) if cfg.allowed_chat_ids else 'ALL'}")
    print(f"agy Binary Path:       {cfg.get_resolved_agy_bin()} (exists: {os.path.exists(cfg.get_resolved_agy_bin())})")
    print(f"agy Working Dir:       {cfg.agy_working_dir}")
    print(f"Default Model:         {cfg.default_model or '(agy default)'}")
    print(f"Default Effort:        {cfg.default_effort}")
    print(f"Stream Throttle (s):   {cfg.stream_throttle_seconds}")
    print(f"Timeout (s):           {cfg.agy_timeout_seconds}")
    print(f"Log Level:             {cfg.log_level}")
    print(f"Log File:              {cfg.log_file}")
