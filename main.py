#!/usr/bin/env python3
"""Main entrypoint for agy-telegram gateway."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from gateway.agy_bridge import AgyBridge
from gateway.bot import AgyTelegramBot
from gateway.config import Config


def setup_logging(config: Config) -> None:
    """Configure console and file logging."""
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # File handler
    if config.log_file:
        Path(config.log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


async def run_smoke_test(config: Config) -> int:
    """Run an isolated smoke test against agy CLI."""
    print("\n🔍 Running agy smoke test...")
    resolved_bin = config.get_resolved_agy_bin()
    print(f"• Target agy binary: {resolved_bin}")
    if not os.path.exists(resolved_bin):
        print(f"❌ Error: agy binary not found at {resolved_bin}")
        return 1

    bridge = AgyBridge(agy_bin=resolved_bin, default_working_dir=config.agy_working_dir)
    test_prompt = "Say 'agy gateway smoke test passed' in one short sentence."
    print(f"• Prompt: {test_prompt}")
    print("• Streaming output:")

    conv_id = None
    accumulated = ""
    status = "UNKNOWN"

    async for event in bridge.run_streaming(prompt=test_prompt):
        if event.type == "init":
            conv_id = event.conversation_id
            print(f"  [init] Conversation ID: {conv_id}")
        elif event.type == "tool":
            print(f"  [tool] {event.tool_name} ({event.tool_state})")
        elif event.type == "text_delta":
            accumulated += event.text_delta
            print(event.text_delta, end="", flush=True)
        elif event.type == "result":
            status = event.status
            print(f"\n  [result] status={event.status}, tokens={event.usage.get('total_tokens')}, time={event.duration_seconds}s")
        elif event.type == "error":
            print(f"\n  [error] {event.response}")

    if status == "SUCCESS" or accumulated.strip():
        print("✅ Smoke test PASSED!\n")
        return 0
    else:
        print(f"❌ Smoke test FAILED (status={status})\n")
        return 1


def main() -> None:
    """Parse CLI arguments and run gateway."""
    parser = argparse.ArgumentParser(
        description="agy-telegram: Direct Telegram <-> agy streaming bridge gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help="Path to custom .env file",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run an agy execution smoke test and exit",
    )

    args = parser.parse_args()
    config = Config.load(env_path=args.env)
    setup_logging(config)

    if args.check_config:
        print("\n=== agy-telegram Configuration Check ===")
        print(f"• .env Loaded from:      {args.env or (Path.cwd() / '.env')}")
        print(f"• BOT_TOKEN valid:       {'YES' if config.is_token_valid() else 'NO (token missing or placeholder)'}")
        print(f"• Whitelist chat IDs:    {list(config.allowed_chat_ids) if config.allowed_chat_ids else 'ALL allowed'}")
        print(f"• agy Binary:            {config.get_resolved_agy_bin()} (exists: {os.path.exists(config.get_resolved_agy_bin())})")
        print(f"• Working Directory:     {config.agy_working_dir}")
        print(f"• Default Model:         {config.default_model or '(default)'}")
        print(f"• Default Effort:        {config.default_effort}")
        print(f"• Stream Throttle:       {config.stream_throttle_seconds}s")
        print(f"• Log File:              {config.log_file}")
        print("========================================\n")
        if not config.is_token_valid():
            print("⚠️ Note: BOT_TOKEN is not configured yet. To use with Telegram:")
            print("  1. Create a bot with @BotFather on Telegram")
            print("  2. Copy token into .env: BOT_TOKEN=123456:ABC-DEF...")
            print("  3. Run ./venv/bin/python main.py\n")
        return

    if args.smoke_test:
        code = asyncio.run(run_smoke_test(config))
        sys.exit(code)

    # Standard bot execution
    if not config.is_token_valid():
        print("\n⚠️  Notice: BOT_TOKEN is missing or is the default placeholder in .env.")
        print("Telegram polling cannot start without a valid bot token.")
        print("\nTo configure and run the bot:")
        print("  1. Open Telegram and message @BotFather to create a new bot (run /newbot).")
        print("  2. Copy the HTTP API token provided by BotFather.")
        print("  3. Create/edit .env file in this directory:")
        print("     BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        print("  4. (Optional) set ALLOWED_CHAT_IDS=your_chat_id to restrict access.")
        print("  5. Start the gateway: ./venv/bin/python main.py\n")
        print("You can verify your agy setup now by running: ./venv/bin/python main.py --smoke-test\n")
        return

    bot = AgyTelegramBot(config)

    # Handle OS signals for graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))
        except (NotImplementedError, RuntimeError):
            pass

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(bot.shutdown())
        loop.close()


if __name__ == "__main__":
    main()
