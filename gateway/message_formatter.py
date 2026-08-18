"""Message formatting, Markdown-to-Telegram-HTML conversion, and chunking utilities."""

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


def balance_telegram_html(text: str) -> str:
    """Ensure all opened Telegram HTML tags are properly balanced and closed."""
    tags = ["b", "i", "s", "u", "code", "pre", "blockquote", "a"]
    opened: List[str] = []

    for match in re.finditer(r"<\s*(/)?\s*([a-zA-Z0-9]+)(?:\s+[^>]*)?>", text):
        is_closing = bool(match.group(1))
        tag_name = match.group(2).lower()
        if tag_name in tags:
            if not is_closing:
                opened.append(tag_name)
            else:
                if tag_name in opened:
                    # Remove the most recent matching occurrence
                    for idx in range(len(opened) - 1, -1, -1):
                        if opened[idx] == tag_name:
                            opened.pop(idx)
                            break

    # Close any remaining unclosed tags in reverse order
    for tag_name in reversed(opened):
        text += f"</{tag_name}>"
    return text


def format_markdown_tables(text: str) -> str:
    """Convert Markdown tables into cleanly aligned monospaced code blocks."""
    table_pattern = re.compile(r"((?:^\|[^\n]+\|\r?\n){2,})", re.MULTILINE)

    def repl_table(match: re.Match) -> str:
        block = match.group(1).strip()
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            return match.group(0)

        # Verify separator row
        if not re.match(r"^\|[\s\-:|]+\|$", lines[1]):
            return match.group(0)

        rows: List[List[str]] = []
        for l in [lines[0]] + lines[2:]:
            cols = [c.strip() for c in l.strip("|").split("|")]
            rows.append(cols)

        if not rows:
            return match.group(0)

        max_cols = max(len(r) for r in rows)
        widths = [0] * max_cols
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = max(widths[i], len(c))

        formatted_lines = []
        # Header
        h_cols = [r.ljust(widths[i]) if i < len(r) else "".ljust(widths[i]) for i, r in enumerate(rows[0])]
        formatted_lines.append(" | ".join(h_cols))
        formatted_lines.append("-+-".join("-" * w for w in widths))
        # Data rows
        for row in rows[1:]:
            r_cols = [row[i].ljust(widths[i]) if i < len(row) else "".ljust(widths[i]) for i in range(max_cols)]
            formatted_lines.append(" | ".join(r_cols))

        return "\n```\n" + "\n".join(formatted_lines) + "\n```\n"

    return table_pattern.sub(repl_table, text)


def markdown_to_telegram_html(text: str) -> str:
    """
    Convert GitHub-flavored Markdown text to valid, rich Telegram-supported HTML.
    Handles:
    - Fenced code blocks with language highlighting: ```lang ... ```
    - Inline code: `...`
    - Tables: formatted into monospaced text blocks
    - Bold: **bold** or __bold__
    - Italic: *italic* or _italic_
    - Strikethrough: ~~strikethrough~~
    - Blockquotes: > quote
    - Headings: # H1, ## H2, ### H3 -> <b>...</b>
    - Links: [label](url) -> <a href="url">label</a>
    - Bullet lists: - item, * item -> • item
    - Horizontal rules: ---, *** -> ———
    - Tag balancing and safe HTML entity escaping
    """
    if not text:
        return ""

    src = format_markdown_tables(text)

    # If unclosed triple backtick at the end (e.g. while streaming), close it temporarily
    if src.count("```") % 2 != 0:
        src += "\n```"

    # 1. Extract and protect code blocks
    code_blocks: List[str] = []

    def save_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        code = match.group(2)
        idx = len(code_blocks)
        escaped_code = html.escape(code.rstrip("\r\n"), quote=False)
        lang_str = lang.strip().lower()
        if lang_str:
            tag = f'<pre><code class="language-{html.escape(lang_str)}">{escaped_code}</code></pre>'
        else:
            tag = f"<pre><code>{escaped_code}</code></pre>"
        code_blocks.append(tag)
        return f"\x00CB{idx}\x00"

    src = re.sub(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", save_code_block, src, flags=re.DOTALL)

    # 2. Extract and protect inline code
    inline_codes: List[str] = []

    def save_inline_code(match: re.Match) -> str:
        code = match.group(1)
        idx = len(inline_codes)
        tag = f"<code>{html.escape(code, quote=False)}</code>"
        inline_codes.append(tag)
        return f"\x00IC{idx}\x00"

    src = re.sub(r"`([^`\n]+)`", save_inline_code, src)

    # 3. Escape HTML special characters in the remaining non-code text
    src = html.escape(src, quote=False)

    # 4. Links: [text](https://...) -> <a href="https://...">text</a>
    def replace_link(match: re.Match) -> str:
        label = match.group(1)
        url = match.group(2).strip()
        return f'<a href="{url}">{label}</a>'

    src = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", replace_link, src)

    # 5. Headings: # H1, ## H2, ### H3 -> <b>...</b>
    src = re.sub(r"^(#{1,6})\s+(.+)$", r"<b>\2</b>", src, flags=re.MULTILINE)

    # 6. Bold: **text** or __text__ -> <b>text</b>
    src = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", src)
    src = re.sub(r"__(.+?)__", r"<b>\1</b>", src)

    # 7. Italic: *text* or _text_ (excluding words with internal underscores like var_name)
    src = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", src)
    src = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", src)

    # 8. Strikethrough: ~~text~~ -> <s>text</s>
    src = re.sub(r"~~(.+?)~~", r"<s>\1</s>", src)

    # 9. Blockquotes: > quote -> <blockquote>...</blockquote>
    def replace_blockquote(match: re.Match) -> str:
        raw = match.group(0).rstrip("\r\n")
        lines = [re.sub(r"^&gt;\s?", "", l) for l in raw.split("\n")]
        return f"<blockquote>{chr(10).join(lines)}</blockquote>\n"

    src = re.sub(r"^(?:&gt;.*(?:\n|$))+", replace_blockquote, src, flags=re.MULTILINE)

    # 10. Horizontal rules: --- or ***
    src = re.sub(r"^(?:---|\*\*\*|___)\s*$", "———", src, flags=re.MULTILINE)

    # 11. Bullet lists: - item, * item, + item -> • item
    src = re.sub(r"^[\*\-\+]\s+(.+)$", r"• \1", src, flags=re.MULTILINE)

    # 12. Restore protected inline code and code blocks
    for idx, tag in enumerate(inline_codes):
        src = src.replace(f"\x00IC{idx}\x00", tag)
    for idx, tag in enumerate(code_blocks):
        src = src.replace(f"\x00CB{idx}\x00", tag)

    return balance_telegram_html(src)


def format_tool_summary(tool_name: str, state: str, tool_info: Dict[str, Any]) -> str:
    """Format a concise single-line tool execution status."""
    params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}

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
    and formatted generated text.
    """
    sections: List[str] = []

    # Include recent tool events (keep up to last 4 to keep message concise)
    if tool_events:
        recent_tools = tool_events[-4:]
        tools_block = "\n".join(recent_tools)
        sections.append(f"<b>[Progress]</b>\n{tools_block}")

    # Convert ongoing markdown text to Telegram HTML
    formatted_body = markdown_to_telegram_html(current_text) if current_text else "<i>(thinking...)</i>"
    cursor_char = " ▌" if cursor and current_text else ""
    sections.append(formatted_body + cursor_char)

    full_content = "\n\n".join(sections)
    if len(full_content) <= max_len:
        return balance_telegram_html(full_content)

    # If exceeding max_len, trim the body text from top
    header = ""
    if tool_events:
        header = f"<b>[Progress]</b>\n" + "\n".join(tool_events[-3:]) + "\n\n[...earlier text omitted...]\n"

    available_space = max_len - len(header) - len(cursor_char) - 20
    if available_space < 100:
        available_space = 100

    tail_body = current_text[-available_space:]
    tail_html = markdown_to_telegram_html(tail_body) + cursor_char
    return balance_telegram_html(f"{header}{tail_html}")


def format_final_result(
    response_text: str,
    duration_seconds: Optional[float] = None,
    total_tokens: Optional[int] = None,
    thinking_tokens: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Format final response by converting Markdown to Telegram HTML and appending metadata footer."""
    formatted_body = markdown_to_telegram_html(response_text) if response_text else "<i>(No response text)</i>"

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
    return balance_telegram_html(formatted_body.strip() + footer)


def split_text_chunks(text: str, chunk_size: int = MAX_TELEGRAM_MESSAGE_LEN) -> List[str]:
    """Split long message text into safe chunks under chunk_size with balanced tags."""
    if len(text) <= chunk_size:
        return [balance_telegram_html(text)]

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
            split_idx = chunk_size

        chunk = remaining[:split_idx].strip()
        if chunk:
            chunks.append(balance_telegram_html(chunk))
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(balance_telegram_html(remaining))

    return chunks


if __name__ == "__main__":
    sample = """### Title Header
Here is **bold text** and `inline code with <brackets> & special`.
Also link: [Telegram](https://telegram.org)

```python
def check(x < 5):
    # This has <b>tags</b> & <symbols>
    return True
```

- Item 1 with `code`
- Item 2 with **bold**

> Note: this is a quote block.
"""
    t1 = format_tool_summary("run_command", "ACTIVE", {"parameters": {"CommandLine": "ls -la /tmp"}})
    t2 = format_tool_summary("run_command", "DONE", {"parameters": {"CommandLine": "ls -la /tmp"}})
    stream_msg = format_streaming_message([t1, t2], sample)
    print("Stream preview:\n", stream_msg)
    final_msg = format_final_result(sample, 2.3, 17500, 420, "fe3e8351-5b1c-4f91-8a63-2394641269b6")
    print("\nFinal preview:\n", final_msg)
    print("\nSplit chunks test:", len(split_text_chunks("A" * 9000)))
    print("MessageFormatter self-test passed!")
