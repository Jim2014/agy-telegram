"""Configuration loader and validator for agy-telegram gateway."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

from gateway.agy_bridge import normalize_permission_preset

# Base directory for the agy-telegram project
BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_SYSTEM_PROMPT = """You are interacting with a user via the agy-telegram gateway on Telegram.

# Output Conventions & Protocol

0. **Strict Dynamic Language Matching Invariant**:
   - You MUST determine your response language SOLELY based on the user's MOST RECENT (latest) message in the conversation, completely IGNORING any previous conversation history or past turns:
     - If the user's latest prompt is in **English**, your entire text response and `[AGY_VOICE lang="en"]` MUST be in **English**.
     - If the user's latest prompt is in **Chinese**, your entire text response and `[AGY_VOICE lang="zh"]` MUST be in **Chinese**.
     - NEVER reply in Chinese if the user's latest message is in English, even if all previous turns were in Chinese.

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

4. **`[AGY_ACTIONS]` Interactive Quick Action Buttons Protocol**:
   - When concluding a response where follow-up actions or next steps are recommended, output an `[AGY_ACTIONS]` block:
   [AGY_ACTIONS]
   - 🧪 Run Tests: pytest -v
   - 📝 View Git Diff: /diff
   - 🚀 Commit & Deploy
   [END_AGY_ACTIONS]

5. **`[AGY_VOICE]` Spoken Audio Protocol (Audio Podcast Edition)**:
   - At the very end of your response (after completing all written text and any `[AGY_ACTIONS]`), ALWAYS append an acoustic-friendly, spoken podcast audio version inside `[AGY_VOICE lang="zh|en"]` and `[END_AGY_VOICE]` tags (each on its own standalone line).
   - Rules for `[AGY_VOICE]`:
     1. **Complete Written Text First**: You MUST finish writing the entire written response 100% to completion. NEVER stop mid-sentence or truncate the written text before opening `[AGY_VOICE]`.
     2. **Complete Meaning & Coverage (Acoustic-Friendly Adaptation)**: Convey all substantive results, decisions, architectural concepts, and reasoning from your response, but adapt it completely for auditory listening.
     3. **Natural Spoken Broadcast Tone**: Imagine speaking as an articulate tech podcast host or audiobook narrator. The narrative must flow smoothly and conversationally.
     4. **STRICTLY AVOID Unspeakable / Visual Artifacts**:
        - **NEVER recite long file paths or URLs**: Instead of saying `/path/to/message_formatter.py`, say "the message formatter module".
        - **NEVER read raw code syntax or symbols**: Do not read function signatures with brackets, colons, underscores, backticks, or variable names like `foo_bar_baz`. Explain what the code does or describe the logic in natural spoken words.
        - **NO Markdown or table markup**: Strip all asterisks, hashes, backticks, pipes, and dashes.
     5. **Do Not Include Buttons / Command Syntax**: NEVER repeat button choices, action menus (`[AGY_ACTIONS]`), decision lists, or slash commands.
     6. **Language Code**: Specify `lang="zh"` for Chinese or `lang="en"` for English matching the user's latest prompt language.
     7. This block will be extracted by the gateway, removed from visible text, and synthesized into spoken audio.

   Example:
   [AGY_VOICE lang="en"]
   I have executed all 126 automated tests and every test passed successfully. In the codebase, we updated the message formatting engine and ensured that language detection always aligns with your latest input. All services are running smoothly, and we are ready for the next steps.
   [END_AGY_VOICE]

6. **`[AGY_IPC]` Decentralized Cross-Project Communication Protocol**:
   - When you need to delegate a task, synchronize code/features, query status, or run tests in another workspace project (e.g. `learner`, `news`, `admin`, `agy-telegram`), emit an `[AGY_IPC]` block:
   [AGY_IPC to="<target_project_name>"]
   <Specific instructions, context, and test requirements for the target project>
   [END_AGY_IPC]
   - When you receive a message wrapped in `[AGY_INCOMING from="<source>" to="<target>"]...[END_AGY_INCOMING]`, execute the requested task in your workspace and reply with `[AGY_IPC to="<source>"]...[END_AGY_IPC]`.
""".strip()


def resolve_default_agy_bin() -> str:
    """Resolve the default agy CLI binary path from local bin or PATH."""
    local_bin = Path.home() / ".local" / "bin" / "agy"
    if local_bin.is_file():
        return str(local_bin)
    return shutil.which("agy") or str(local_bin)


@dataclass
class Config:
    """Gateway runtime configuration."""

    bot_token: str = ""
    allowed_chat_ids: Set[int] = field(default_factory=set)
    allow_all_chats: bool = False
    dm_pairing_enabled: bool = True
    pairing_file: str = "runtime/pairing.json"
    agy_bin_path: str = field(default_factory=resolve_default_agy_bin)
    agy_working_dir: str = ""
    default_model: str = "gemini-3.8-flash"
    default_effort: str = "high"
    default_permission_preset: str = "turbo"
    stream_throttle_seconds: float = 1.0
    agy_timeout_seconds: int = 300
    log_level: str = "INFO"
    log_file: str = "runtime/gateway.log"
    sessions_file: str = "runtime/sessions.json"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    webapp_port: int = 8765
    webapp_base_url: str = ""
    webapp_enabled: bool = True
    tts_enabled: bool = True
    tts_mode: str = "autogen"  # "disable" | "autogen" | "autoplay"
    tts_voice_zh: str = "zh-CN-XiaoxiaoNeural"
    tts_voice_en: str = "en-US-JennyNeural"
    whisper_model_size: str = "base"
    html_mode: str = "auto"  # "auto" | "explicit" | "off"

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

        # Parse allowed chat IDs and deny-by-default security flags
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
        allow_all_chats = os.getenv("ALLOW_ALL_CHATS", "false").strip().lower() in ("true", "1", "yes")
        dm_pairing_enabled = os.getenv("DM_PAIRING_ENABLED", "true").strip().lower() in ("true", "1", "yes")
        pairing_file = os.getenv("PAIRING_FILE", "runtime/pairing.json").strip() or "runtime/pairing.json"

        # agy binary path
        agy_bin = os.getenv("AGY_BIN_PATH", "").strip()
        if not agy_bin:
            agy_bin = resolve_default_agy_bin()

        # agy working dir
        working_dir = os.getenv("AGY_WORKING_DIR", "").strip()
        if not working_dir:
            working_dir = str(BASE_DIR)

        # Other options
        default_model = os.getenv("DEFAULT_MODEL", "gemini-3.8-flash").strip()
        default_effort = os.getenv("DEFAULT_EFFORT", "high").strip().lower()
        if default_effort not in ("low", "medium", "high"):
            default_effort = "medium"
        default_permission_preset = normalize_permission_preset(
            os.getenv("AGY_PERMISSION_PRESET", "turbo")
        )

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

        try:
            webapp_port = int(os.getenv("WEBAPP_PORT", "8765"))
        except ValueError:
            webapp_port = 8765
        webapp_base_url = os.getenv("WEBAPP_BASE_URL", "").strip()
        webapp_enabled = os.getenv("WEBAPP_ENABLED", "true").strip().lower() in ("true", "1", "yes")

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

        # TTS configuration
        tts_enabled = os.getenv("TTS_ENABLED", "true").strip().lower() in ("true", "1", "yes")
        tts_mode = os.getenv("TTS_MODE", "autogen").strip().lower()
        if tts_mode not in ("disable", "autogen", "autoplay"):
            tts_mode = "autogen"
        tts_voice_zh = os.getenv("TTS_VOICE_ZH", "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural"
        tts_voice_en = os.getenv("TTS_VOICE_EN", "en-US-JennyNeural").strip() or "en-US-JennyNeural"
        whisper_model_size = os.getenv("WHISPER_MODEL_SIZE", "base").strip().lower() or "base"

        # HTML attachment mode ("auto" | "explicit" | "off")
        raw_html_mode = os.getenv("HTML_MODE", "auto").strip().lower()
        if raw_html_mode in ("explicit", "manual", "ondemand", "agy_file"):
            html_mode = "explicit"
        elif raw_html_mode in ("off", "disable", "disabled", "none"):
            html_mode = "off"
        else:
            html_mode = "auto"

        return cls(
            bot_token=bot_token,
            allowed_chat_ids=allowed_chat_ids,
            allow_all_chats=allow_all_chats,
            dm_pairing_enabled=dm_pairing_enabled,
            pairing_file=pairing_file,
            agy_bin_path=agy_bin,
            agy_working_dir=working_dir,
            default_model=default_model,
            default_effort=default_effort,
            default_permission_preset=default_permission_preset,
            stream_throttle_seconds=stream_throttle,
            agy_timeout_seconds=agy_timeout,
            log_level=log_level,
            log_file=log_file,
            sessions_file=sessions_file,
            system_prompt=system_prompt,
            webapp_port=webapp_port,
            webapp_base_url=webapp_base_url,
            webapp_enabled=webapp_enabled,
            tts_enabled=tts_enabled,
            tts_mode=tts_mode,
            tts_voice_zh=tts_voice_zh,
            tts_voice_en=tts_voice_en,
            whisper_model_size=whisper_model_size,
            html_mode=html_mode,
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
        """Check if chat_id is explicitly allowed (deny-by-default unless ALLOW_ALL_CHATS=true)."""
        if self.allow_all_chats:
            return True
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
    print(f"Allowed Chat IDs:      {list(cfg.allowed_chat_ids) if cfg.allowed_chat_ids else ('ALL (open access)' if cfg.allow_all_chats else 'NONE (deny-by-default / DM pairing)')}")
    print(f"agy Binary Path:       {cfg.get_resolved_agy_bin()} (exists: {os.path.exists(cfg.get_resolved_agy_bin())})")
    print(f"agy Working Dir:       {cfg.agy_working_dir}")
    print(f"Default Model:         {cfg.default_model or '(agy default)'}")
    print(f"Default Effort:        {cfg.default_effort}")
    print(f"Default Permissions:   {cfg.default_permission_preset}")
    print(f"Stream Throttle (s):   {cfg.stream_throttle_seconds}")
    print(f"Timeout (s):           {cfg.agy_timeout_seconds}")
    print(f"Log Level:             {cfg.log_level}")
    print(f"Log File:              {cfg.log_file}")
    print(f"System Prompt:         {len(cfg.system_prompt)} chars configured")

