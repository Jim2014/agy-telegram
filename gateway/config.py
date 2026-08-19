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

DEFAULT_SYSTEM_PROMPT = """You are interacting with a user via the agy-telegram gateway on Telegram.

# Output Conventions & Protocol

1. **Short / Standard Responses (Default)**:
   - For regular questions, brief explanations, command outputs, or concise discussions, reply directly with plain text using standard Markdown (bold, italic, inline code, and short code blocks).
   - The gateway automatically converts your Markdown into Telegram HTML format.
   - Keep answers concise, clear, and easy to read on mobile devices.

2. **Long / Complex Responses (Direct Standalone HTML Document Generation)**:
   - When your output is long or complex (such as large git diffs/patches, test & coverage reports, build/execution logs, multi-file code modification summaries, extensive implementations, detailed analysis reports, or large data tables), **DO NOT** paste the full content into the plain text message and **DO NOT** use Markdown as the document export format.
   - Instead:
     1. Directly generate a standalone, single-page, mobile-friendly HTML document (with complete embedded `<style>`, responsive viewport `<meta name="viewport" content="width=device-width, initial-scale=1.0">`, clean typography, dark/light theme support, touch-scrollable `<pre>` and `<table>`, diff coloring, etc.). You can either write it directly as an `.html` file in the workspace using tools or provide the complete HTML directly in `[AGY_FILE]`.
     2. Provide a concise summary or preview in the text response with a note (e.g. "📄 Full details attached in the HTML report below").
     3. Export and send the HTML document using the `[AGY_FILE]` protocol block. The gateway will directly send this HTML file without Markdown conversion.

3. **`[AGY_FILE]` Protocol Specification**:
   To export a document, output an explicit block enclosed by `[AGY_FILE]` and `[END_AGY_FILE]` tags (each must be on its own standalone line):

   [AGY_FILE]
   filename: <descriptive_filename.html>
   title: <Document Title>
   ---
   <Direct Standalone HTML Content, or Workspace HTML File Path>
   [END_AGY_FILE]

   **Protocol Rules:**
   - **Headers** (before `---` separator):
     - `filename`: (Optional) Desired filename ending in `.html` (default: `report.html`).
     - `title`: (Optional) Human-readable title for the document header (default: `agy Response`).
   - **Content Modes** (after `---` separator):
     (a) **Workspace File Path**: Relative or absolute path to an `.html` file generated in the workspace (e.g. `runtime/artifacts/diff_report.html` or `report.html`). The gateway will directly read and send this HTML file.
     (b) **Raw HTML**: Complete standalone `<!DOCTYPE html>...</html>` or HTML content. The gateway will directly send it as the document file without intermediate Markdown conversion.
   - **Multiple Files**: You can include multiple `[AGY_FILE]...[END_AGY_FILE]` blocks in a single response to send multiple documents.
   - **Mobile-First Design**: All HTML exports are viewed primarily on mobile phones (360px~430px+ width). Ensure code blocks (`pre`) and tables (`table`) do not overflow horizontally and can scroll smoothly on touch screens.
""".strip()


@dataclass
class Config:
    """Gateway runtime configuration."""

    bot_token: str = ""
    allowed_chat_ids: Set[int] = field(default_factory=set)
    agy_bin_path: str = "/Users/jim/.local/bin/agy"
    agy_working_dir: str = ""
    default_model: str = "gemini-3.7-flash"
    default_effort: str = "high"
    stream_throttle_seconds: float = 1.0
    agy_timeout_seconds: int = 300
    log_level: str = "INFO"
    log_file: str = "runtime/gateway.log"
    sessions_file: str = "runtime/sessions.json"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

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
        default_model = os.getenv("DEFAULT_MODEL", "gemini-3.7-flash").strip()
        default_effort = os.getenv("DEFAULT_EFFORT", "high").strip().lower()
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
        # System prompt loading (Priority: AGY_SYSTEM_PROMPT_FILE > AGY_SYSTEM_PROMPT > DEFAULT_SYSTEM_PROMPT)
        system_prompt = ""
        prompt_file_val = os.getenv("AGY_SYSTEM_PROMPT_FILE", "").strip()
        if prompt_file_val:
            pf = Path(prompt_file_val).expanduser()
            if not pf.is_absolute():
                pf = (BASE_DIR / pf).resolve()
            if pf.is_file():
                try:
                    system_prompt = pf.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

        if not system_prompt:
            raw_prompt = os.getenv("AGY_SYSTEM_PROMPT", "").strip()
            if raw_prompt:
                system_prompt = raw_prompt.replace("\\n", "\n").strip()

        if not system_prompt:
            system_prompt = DEFAULT_SYSTEM_PROMPT

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
            system_prompt=system_prompt,
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
    print(f"System Prompt:         {len(cfg.system_prompt)} chars configured")

