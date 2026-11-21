"""Interactive configuration helper for agy-telegram onboarding and .env generation."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gateway.config import BASE_DIR, Config, resolve_default_agy_bin


def verify_bot_token(
    bot_token: str,
    timeout: float = 10.0,
    http_client: Optional[httpx.Client] = None,
) -> Optional[Dict[str, Any]]:
    """Verify a Telegram Bot API token via getMe and return bot metadata if valid."""
    token = (bot_token or "").strip()
    if not Config(bot_token=token).is_token_valid():
        return None

    url = f"https://api.telegram.org/bot{token}/getMe"
    close_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("ok") and isinstance(data.get("result"), dict):
            return data["result"]
    except Exception:
        return None
    finally:
        if close_client:
            client.close()
    return None


def _extract_chat_from_update(upd: Dict[str, Any]) -> Optional[Tuple[int, str]]:
    """Extract (chat_id, label) from a Telegram Update object."""
    msg = upd.get("message") or upd.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    sender = msg.get("from") or {}
    username = sender.get("username") or chat.get("username") or ""
    first_name = sender.get("first_name") or chat.get("first_name") or chat.get("title") or "Telegram User"
    label = f"{first_name} (@{username})" if username else first_name
    return int(chat_id), label


def detect_chat_id_via_polling(
    bot_token: str,
    timeout_seconds: int = 45,
    poll_interval: float = 1.5,
    http_client: Optional[httpx.Client] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[Tuple[int, str]]:
    """
    Poll Telegram getUpdates briefly to capture the user's numeric chat_id
    when they send a message (e.g. /start) to their newly created bot.
    """
    token = (bot_token or "").strip()
    if not token:
        return None

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    close_client = http_client is None
    client = http_client or httpx.Client(timeout=15.0)
    deadline = time.monotonic() + max(1, timeout_seconds)
    offset: Optional[int] = None

    try:
        # Check immediately for any recent pending message, otherwise advance offset
        resp = client.get(url, params={"limit": 10, "timeout": 0})
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("result", []) if data.get("ok") else []
            for upd in reversed(results):
                extracted = _extract_chat_from_update(upd)
                if extracted:
                    return extracted
            if results:
                offset = max(int(u.get("update_id", 0)) for u in results) + 1

        while time.monotonic() < deadline:
            remaining = max(1, min(5, int(deadline - time.monotonic())))
            params: Dict[str, Any] = {"limit": 10, "timeout": remaining}
            if offset is not None:
                params["offset"] = offset
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", []) if data.get("ok") else []
                for upd in results:
                    uid = upd.get("update_id")
                    if uid is not None:
                        offset = int(uid) + 1
                    extracted = _extract_chat_from_update(upd)
                    if extracted:
                        return extracted
            sleep_fn(poll_interval)
    except Exception:
        return None
    finally:
        if close_client:
            client.close()
    return None


def _format_env_value(value: str) -> str:
    """Format an environment value safely for .env files."""
    if any(ch in value for ch in (" ", "#", '"', "'", "\n")):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


def render_env_content(
    base_content: str,
    updates: Dict[str, str],
) -> str:
    """Update existing or commented keys in .env content while preserving comments and order."""
    lines = base_content.splitlines()
    remaining = dict(updates)
    out_lines = []

    key_pattern = re.compile(r"^\s*(?:#\s*)?([A-Z0-9_]+)\s*=")
    for line in lines:
        m = key_pattern.match(line)
        if m:
            key = m.group(1)
            if key in remaining:
                val = remaining.pop(key)
                out_lines.append(f"{key}={_format_env_value(val)}")
                continue
        out_lines.append(line)

    for key, val in remaining.items():
        out_lines.append(f"{key}={_format_env_value(val)}")

    return "\n".join(out_lines) + "\n"


def write_env_file(
    env_path: Path,
    updates: Dict[str, str],
    example_path: Optional[Path] = None,
) -> Path:
    """Create or update .env with restricted 0600 permissions."""
    base_content = ""
    if env_path.is_file():
        base_content = env_path.read_text(encoding="utf-8")
    elif example_path and example_path.is_file():
        base_content = example_path.read_text(encoding="utf-8")

    rendered = render_env_content(base_content, updates)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(rendered, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def _prompt_with_default(
    label: str,
    default: str = "",
    secret: bool = False,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Prompt user with optional default value and masked input for secrets."""
    suffix = f" [{default}]" if (default and not secret) else (" [kept existing]" if (default and secret) else "")
    prompt_str = f"{label}{suffix}: "
    raw = getpass.getpass(prompt_str) if (secret and input_fn is input) else input_fn(prompt_str)
    val = raw.strip()
    return val if val else default


def run_interactive_setup(
    env_path: Optional[Path] = None,
    example_path: Optional[Path] = None,
    input_fn: Callable[[str], str] = input,
    skip_network: bool = False,
) -> int:
    """Run the interactive .env and Telegram onboarding wizard."""
    target_env = env_path or (BASE_DIR / ".env")
    tmpl_env = example_path or (BASE_DIR / ".env.example")

    existing_cfg = Config.load(env_path=target_env) if target_env.is_file() else Config()

    print("\n=== agy-telegram Interactive Configuration ===")
    print("Leave blank to keep the value shown in brackets.\n")

    # 1. Telegram Bot Token
    print("Step 1: Telegram Bot Token (from @BotFather -> /newbot)")
    bot_token = ""
    bot_username = ""
    while True:
        candidate = _prompt_with_default(
            "  BOT_TOKEN",
            default=existing_cfg.bot_token,
            secret=True,
            input_fn=input_fn,
        )
        if not Config(bot_token=candidate).is_token_valid():
            print("  ❌ Invalid token format (expected digits:secret from @BotFather). Please try again.")
            continue

        if skip_network:
            bot_token = candidate
            break

        print("  🔍 Verifying token with Telegram API...")
        bot_info = verify_bot_token(candidate)
        if bot_info:
            bot_token = candidate
            bot_username = bot_info.get("username", "")
            print(f"  ✅ Verified bot: {bot_info.get('first_name', 'Bot')} (@{bot_username})")
            break

        retry = _prompt_with_default("  ⚠️ Token check failed (bad token or network error). Retry? [Y/n]", default="Y", input_fn=input_fn)
        if retry.lower().startswith("n"):
            bot_token = candidate
            break

    # 2. Allowed Chat IDs (Manual or Auto-Detect via getUpdates)
    print("\nStep 2: Allowed Telegram Chat IDs (Security Whitelist)")
    print("  Only whitelisted Chat IDs can control your local agy CLI.")
    existing_ids = ",".join(str(i) for i in sorted(existing_cfg.allowed_chat_ids))
    allowed_ids = existing_ids

    if not skip_network and bot_token:
        detect_choice = _prompt_with_default(
            "  Auto-detect your Chat ID by sending /start to the bot? [Y/n]",
            default="Y" if not existing_ids else "n",
            input_fn=input_fn,
        )
        if detect_choice.lower().startswith("y"):
            bot_link = f"https://t.me/{bot_username}" if bot_username else "your bot in Telegram"
            print(f"  📱 Open {bot_link} and send /start or any message (waiting up to 45s)...")
            detected = detect_chat_id_via_polling(bot_token, timeout_seconds=45)
            if detected:
                cid, sender_label = detected
                confirm = _prompt_with_default(
                    f"  ✅ Detected {sender_label} (Chat ID: {cid}). Allow this chat? [Y/n]",
                    default="Y",
                    input_fn=input_fn,
                )
                if not confirm.lower().startswith("n"):
                    allowed_ids = str(cid)
            else:
                print("  ⏱️ Timed out waiting for a Telegram message.")

    allowed_ids = _prompt_with_default(
        "  ALLOWED_CHAT_IDS (comma-separated, empty = allow all)",
        default=allowed_ids,
        input_fn=input_fn,
    ).replace(" ", "")

    if not allowed_ids:
        print("  ⚠️ Warning: ALLOWED_CHAT_IDS is empty — anyone who finds your bot can access it.")

    # 3. agy Binary & Default Workspace
    print("\nStep 3: Antigravity CLI & Workspace Settings")
    default_agy = existing_cfg.agy_bin_path or resolve_default_agy_bin()
    agy_bin = _prompt_with_default("  AGY_BIN_PATH", default=default_agy, input_fn=input_fn)
    default_wdir = existing_cfg.agy_working_dir or str(BASE_DIR)
    working_dir = _prompt_with_default("  AGY_WORKING_DIR", default=default_wdir, input_fn=input_fn)

    # 4. Output Preferences (HTML & Voice)
    print("\nStep 4: Output Preferences")
    html_mode = _prompt_with_default(
        "  HTML_MODE (auto | explicit | off)",
        default=existing_cfg.html_mode or "auto",
        input_fn=input_fn,
    )
    tts_mode = _prompt_with_default(
        "  TTS_MODE (autogen | autoplay | disable)",
        default=existing_cfg.tts_mode or "autogen",
        input_fn=input_fn,
    )

    updates = {
        "BOT_TOKEN": bot_token,
        "ALLOWED_CHAT_IDS": allowed_ids,
        "AGY_BIN_PATH": agy_bin,
        "AGY_WORKING_DIR": working_dir,
        "HTML_MODE": html_mode,
        "TTS_MODE": tts_mode,
    }
    write_env_file(target_env, updates=updates, example_path=tmpl_env)
    print(f"\n✅ Saved configuration to {target_env} (permissions 0600)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive setup helper for agy-telegram")
    parser.add_argument("--env-file", type=str, default=None, help="Target .env file path")
    parser.add_argument("--skip-network", action="store_true", help="Skip live Telegram API verification/polling")
    args = parser.parse_args()

    env_path = Path(args.env_file) if args.env_file else None
    sys.exit(run_interactive_setup(env_path=env_path, skip_network=args.skip_network))


if __name__ == "__main__":
    main()
