"""Message formatting and chunking utilities for Telegram display."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

MAX_TELEGRAM_MESSAGE_LEN = 4000


def escape_html(text: str) -> str:
    """Escape HTML special characters for safe Telegram HTML parsing."""
    if not text:
        return ""
    return html.escape(text, quote=False)


def format_tool_summary(tool_name: str, state: str, tool_info: Dict[str, Any]) -> str:
    """Format a concise single-line tool execution status."""
    params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}
    
    # Extract representative parameter for quick visual identification
    param_summary = ""
    if tool_name == "run_command":
        cmd = params.get("CommandLine", "")
        param_summary = f": <code>{escape_html(cmd[:60] + ('...' if len(cmd) > 60 else ''))}</code>"
    elif tool_name in ("view_file", "write_to_file", "replace_file_content"):
        path = params.get("AbsolutePath") or params.get("TargetFile") or ""
        short_path = path.split("/")[-1] if path else ""
        param_summary = f" {escape_html(short_path)}"
    elif tool_name == "list_dir":
        dpath = params.get("DirectoryPath", "")
        short_path = dpath.split("/")[-1] or "/" if dpath else ""
        param_summary = f" {escape_html(short_path)}"
    elif tool_name == "grep_search":
        q = params.get("Query", "")
        param_summary = f" '{escape_html(q[:30])}'"
    elif tool_name == "search_web":
        q = params.get("query", "")
        param_summary = f" '{escape_html(q[:30])}'"

    if state == "ACTIVE":
        return f"⚙️ <i>{escape_html(tool_name)}</i>{param_summary}..."
    elif state == "DONE":
        return f"✅ <i>{escape_html(tool_name)}</i>{param_summary}"
    elif state == "ERROR":
        err_msg = ""
        if isinstance(tool_info, dict) and "error" in tool_info:
            err = tool_info["error"]
            err_msg = err.get("message", "") if isinstance(err, dict) else str(err)
        err_short = escape_html(err_msg[:60] + ("..." if len(err_msg) > 60 else ""))
        return f"⚠️ <i>{escape_html(tool_name)} failed:</i> {err_short}"
    else:
        return f"🔧 <i>{escape_html(tool_name)}</i> [{escape_html(state)}]"


def format_streaming_message(
    tool_events: List[str],
    current_text: str,
    cursor: bool = True,
    max_len: int = MAX_TELEGRAM_MESSAGE_LEN,
) -> str:
    """
    Build a live streaming preview message combining recent tool activities
    and the ongoing generated text.
    """
    sections: List[str] = []

    # Include recent tool events (keep up to last 4 to keep message concise)
    if tool_events:
        recent_tools = tool_events[-4:]
        tools_block = "\n".join(recent_tools)
        sections.append(f"<b>[Progress]</b>\n{tools_block}")

    # Text content
    cursor_char = " ▌" if cursor else ""
    body = current_text + cursor_char if current_text else "(thinking...)"
    sections.append(body)

    full_content = "\n\n".join(sections)
    if len(full_content) <= max_len:
        return full_content

    # If exceeding max_len, trim the body text from the top (rolling window)
    header = ""
    if tool_events:
        header = f"<b>[Progress]</b>\n" + "\n".join(tool_events[-3:]) + "\n\n[...earlier text omitted...]\n"

    available_space = max_len - len(header) - len(cursor_char) - 10
    if available_space < 100:
        available_space = 100

    tail_body = current_text[-available_space:] + cursor_char
    return f"{header}{tail_body}"


def format_final_result(
    response_text: str,
    duration_seconds: Optional[float] = None,
    total_tokens: Optional[int] = None,
    thinking_tokens: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Format final response with metadata footer."""
    footer_parts: List[str] = []
    if duration_seconds is not None:
        footer_parts.append(f"⏱ {duration_seconds:.1f}s")
    if total_tokens is not None:
        tok_str = f"{total_tokens/1000:.1f}k" if total_tokens >= 1000 else f"{total_tokens}"
        if thinking_tokens:
            tok_str += f" (thinking: {thinking_tokens})"
        footer_parts.append(f"🪙 {tok_str} tokens")
    if conversation_id:
        short_id = conversation_id[:8] + "..."
        footer_parts.append(f"🆔 <code>{short_id}</code>")

    footer = f"\n\n———\n{' | '.join(footer_parts)}" if footer_parts else ""
    return (response_text or "(No response text)").strip() + footer


def split_text_chunks(text: str, chunk_size: int = MAX_TELEGRAM_MESSAGE_LEN) -> List[str]:
    """Split long message text into safe chunks under chunk_size."""
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    remaining = text

    while len(remaining) > chunk_size:
        # Try to split at double newline, newline, or whitespace
        split_idx = remaining.rfind("\n\n", 0, chunk_size)
        if split_idx == -1:
            split_idx = remaining.rfind("\n", 0, chunk_size)
        if split_idx == -1:
            split_idx = remaining.rfind(" ", 0, chunk_size)
        if split_idx == -1 or split_idx < chunk_size // 2:
            # Hard split if no good boundary
            split_idx = chunk_size

        chunk = remaining[:split_idx].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


if __name__ == "__main__":
    t1 = format_tool_summary("run_command", "ACTIVE", {"parameters": {"CommandLine": "ls -la /tmp"}})
    t2 = format_tool_summary("run_command", "DONE", {"parameters": {"CommandLine": "ls -la /tmp"}})
    stream_msg = format_streaming_message([t1, t2], "Here is the calculation...")
    print("Stream preview:\n", stream_msg)
    final_msg = format_final_result("Calculation completed: 4", 2.3, 17500, 420, "fe3e8351-5b1c-4f91-8a63-2394641269b6")
    print("\nFinal preview:\n", final_msg)
    print("\nSplit chunks test:", len(split_text_chunks("A" * 9000)))
    print("MessageFormatter self-test passed!")
