"""Message formatting, Markdown-to-Telegram-HTML conversion, HTML file export, and chunking utilities."""

from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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


def format_tokens(num: int, thinking: int = 0) -> str:
    """
    Format token counts with human-readable abbreviations and full numbers.
    Examples:
        3973596 -> "3.97M (3,973,596)"
        45210 -> "45.2k (45,210)"
        350 -> "350"
    """
    safe_num = max(0, int(num))
    if safe_num >= 1_000_000:
        main_str = f"{safe_num / 1_000_000:.2f}M ({safe_num:,})"
    elif safe_num >= 1_000:
        main_str = f"{safe_num / 1_000:.1f}k ({safe_num:,})"
    else:
        main_str = f"{safe_num:,}"

    if thinking > 0:
        safe_th = int(thinking)
        if safe_th >= 1_000_000:
            th_str = f"{safe_th / 1_000_000:.2f}M"
        elif safe_th >= 1_000:
            th_str = f"{safe_th / 1_000:.1f}k"
        else:
            th_str = f"{safe_th:,}"
        return f"{main_str} [thinking: {th_str}]"

    return main_str


def format_duration(seconds: float) -> str:
    """
    Format duration seconds into human-readable hours/minutes/seconds.
    Examples:
        7208.2 -> "2h 0m 8s" (or "2h 0m 8s (7,208s)")
        75.4 -> "1m 15s (75.4s)"
        4.2 -> "4.2s"
    """
    sec = max(0.0, float(seconds))
    if sec < 60.0:
        return f"{sec:.1f}s"

    tot_sec = int(round(sec))
    hours = tot_sec // 3600
    minutes = (tot_sec % 3600) // 60
    rem_s = tot_sec % 60

    if hours > 0:
        return f"{hours}h {minutes}m {rem_s}s ({sec:,.0f}s)"
    return f"{minutes}m {rem_s}s ({sec:.1f}s)"


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


def format_tool_summary(
    tool_name: str,
    state: str,
    tool_info: Dict[str, Any],
    duration_seconds: Optional[float] = None,
) -> str:
    """Format a concise, informative single-line tool execution status."""
    params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}

    # Tool specific icon mapping
    tool_icons = {
        "run_command": "⚙️",
        "view_file": "📄",
        "write_to_file": "📝",
        "replace_file_content": "✏️",
        "list_dir": "📁",
        "find_by_name": "🔍",
        "grep_search": "🔍",
        "search_web": "🌐",
        "read_url_content": "🌐",
        "invoke_subagent": "🤖",
        "manage_subagents": "👥",
        "send_message": "💬",
        "define_subagent": "🧬",
        "generate_image": "🎨",
        "manage_task": "📋",
        "schedule": "⏰",
        "ask_question": "❓",
    }
    icon = tool_icons.get(tool_name, "🔧")

    param_summary = ""
    if tool_name == "run_command":
        cmd = params.get("CommandLine", "")
        param_summary = f": <code>{escape_html(cmd[:60] + ('...' if len(cmd) > 60 else ''))}</code>"
    elif tool_name in ("view_file", "write_to_file", "replace_file_content"):
        path = params.get("AbsolutePath") or params.get("TargetFile") or ""
        short_path = path.split("/")[-1] if path else ""
        start_l = params.get("StartLine")
        end_l = params.get("EndLine")
        line_range = f"#L{start_l}-L{end_l}" if start_l and end_l else (f"#L{start_l}" if start_l else "")
        param_summary = f" {escape_html(short_path + line_range)}"
    elif tool_name == "list_dir":
        dpath = params.get("DirectoryPath", "")
        short_path = dpath.split("/")[-1] or "/" if dpath else ""
        param_summary = f" {escape_html(short_path)}"
    elif tool_name == "find_by_name":
        pat = params.get("Pattern", "")
        dpath = params.get("SearchDirectory", "")
        short_d = dpath.split("/")[-1] if dpath else ""
        param_summary = f" '{escape_html(pat)}'{' in ' + escape_html(short_d) if short_d else ''}"
    elif tool_name == "grep_search":
        q = params.get("Query", "")
        param_summary = f" '{escape_html(q[:30])}'"
    elif tool_name in ("search_web", "read_url_content"):
        q = params.get("query") or params.get("Url") or ""
        param_summary = f" '{escape_html(q[:40])}'"
    elif tool_name == "invoke_subagent":
        subagents = params.get("Subagents", [])
        if isinstance(subagents, list) and subagents:
            roles = [s.get("Role") or s.get("TypeName") or "agent" for s in subagents if isinstance(s, dict)]
            param_summary = f" [{', '.join(roles[:2])}{'...' if len(roles) > 2 else ''}]"
        else:
            param_summary = " subagent"
    elif tool_name == "manage_subagents":
        act = params.get("Action", "")
        param_summary = f" ({escape_html(act)})" if act else ""
    elif tool_name == "send_message":
        rec = params.get("Recipient", "")
        short_rec = rec.split("/")[-1][:12] if rec else ""
        param_summary = f" -> <code>{escape_html(short_rec)}</code>" if short_rec else ""
    elif tool_name == "define_subagent":
        s_name = params.get("name", "")
        param_summary = f" <code>{escape_html(s_name)}</code>" if s_name else ""
    elif tool_name == "generate_image":
        img_name = params.get("ImageName") or params.get("Prompt", "")
        param_summary = f" '{escape_html(img_name[:30])}'"
    elif tool_name == "manage_task":
        act = params.get("Action", "")
        tid = params.get("TaskId", "")
        short_tid = tid.split("/")[-1] if tid else ""
        param_summary = f" ({escape_html(act)}{' ' + escape_html(short_tid) if short_tid else ''})"
    elif tool_name == "schedule":
        dur = params.get("DurationSeconds")
        cron = params.get("CronExpression")
        timing = f"{dur}s" if dur else (cron or "")
        param_summary = f" ({escape_html(str(timing))})" if timing else ""
    elif tool_name == "ask_question":
        qs = params.get("questions", [])
        q_text = qs[0].get("question", "") if isinstance(qs, list) and qs and isinstance(qs[0], dict) else ""
        param_summary = f": {escape_html(q_text[:30] + ('...' if len(q_text) > 30 else ''))}" if q_text else ""

    dur_str = f" <i>({duration_seconds:.1f}s)</i>" if duration_seconds is not None and duration_seconds > 0 else ""

    if state == "ACTIVE":
        return f"{icon} <i>{escape_html(tool_name)}</i>{param_summary}..."
    elif state == "DONE":
        return f"✅ <i>{escape_html(tool_name)}</i>{param_summary}{dur_str}"
    elif state == "ERROR":
        err_msg = ""
        if isinstance(tool_info, dict) and "error" in tool_info:
            err = tool_info["error"]
            err_msg = err.get("message", "") if isinstance(err, dict) else str(err)
        err_short = escape_html(err_msg[:60] + ("..." if len(err_msg) > 60 else ""))
        return f"⚠️ <i>{escape_html(tool_name)} failed:</i> {err_short}"
    else:
        return f"🔧 <i>{escape_html(tool_name)}</i> [{escape_html(state)}]{dur_str}"


def format_streaming_message(
    tool_events: List[str],
    current_text: str = "",
    cursor: bool = True,
    max_len: int = MAX_TELEGRAM_MESSAGE_LEN,
    **kwargs: Any,
) -> str:
    """
    Build a live streaming preview message combining recent tool activities
    and formatted generated text (expanded by default).
    """
    if not current_text and "accumulated_text" in kwargs:
        current_text = kwargs["accumulated_text"]
    sections: List[str] = []

    # Include recent tool events in an expanded blockquote (default open, up to 8 recent steps)
    if tool_events:
        max_tools = 8
        if len(tool_events) > max_tools:
            omitted = len(tool_events) - max_tools
            recent_tools = [f"<i>... ({omitted} earlier steps omitted)</i>"] + tool_events[-max_tools:]
        else:
            recent_tools = tool_events
        tools_block = "\n".join(recent_tools)
        sections.append(f"<blockquote><b>[Progress]</b>\n{tools_block}</blockquote>")

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
        recent = tool_events[-5:]
        header = f"<blockquote><b>[Progress]</b>\n" + "\n".join(recent) + "</blockquote>\n\n[...earlier text omitted...]\n"

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
    project_name: Optional[str] = None,
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
    if project_name:
        footer_parts.append(f"📁 <code>{escape_html(project_name)}</code>")
    elif conversation_id:
        short_id = conversation_id[:8] + "..."
        footer_parts.append(f"🆔 <code>{short_id}</code>")

    footer = f"\n\n———\n{' | '.join(footer_parts)}" if footer_parts else ""
    return balance_telegram_html(formatted_body.strip() + footer)


def split_text_chunks(text: str, chunk_size: int = MAX_TELEGRAM_MESSAGE_LEN, **kwargs: Any) -> List[str]:
    """Split long message text into safe chunks under chunk_size with balanced tags."""
    if "max_chunk_size" in kwargs:
        chunk_size = kwargs["max_chunk_size"]
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


def format_pinned_banner(
    session: Any,
    default_model: str = "gemini-3.7-flash",
    default_effort: str = "high",
    is_busy: bool = False,
) -> str:
    """
    Format a high-density, concise pinned status card for the Telegram chat header.
    """
    active_proj = getattr(session, "active_project", None) or "(none)"
    active_path = session.active_path() if hasattr(session, "active_path") else None
    active_path_str = active_path or "(no workspace directory)"
    model = getattr(session, "model", None) or default_model
    effort = getattr(session, "effort", None) or default_effort

    # Statistics
    stats: Dict[str, Any] = {}
    if hasattr(session, "projects") and isinstance(session.projects, dict):
        stats = session.projects.get(active_proj, {}) or {}
    elif hasattr(session, "get_project_stats"):
        stats = session.get_project_stats() or {}
    tokens = stats.get("total_tokens", 0)
    thinking = stats.get("thinking_tokens", 0)
    turns = stats.get("turns", 0)
    total_cpu = stats.get("total_duration_seconds", 0.0)

    # Session uptime
    created_at = getattr(session, "created_at", time.time())
    uptime_sec = max(0.0, time.time() - created_at)

    status_badge = "⚡ <b>Running...</b>" if is_busy else "🟢 <b>Ready</b>"

    return (
        f"📌 <b>agy Workspace Hub</b>\n\n"
        f"• <b>Project:</b> <code>{escape_html(active_proj)}</code> ({status_badge})\n"
        f"• <b>Directory:</b> <code>{escape_html(active_path_str)}</code>\n"
        f"• <b>Model:</b> <code>{escape_html(model)}</code> (effort: <code>{escape_html(effort)}</code>)\n"
        f"• <b>Usage:</b> 🪙 {format_tokens(tokens, thinking)} | ⏱️ {format_duration(uptime_sec)}\n"
        f"• <b>Activity:</b> {turns} turns executed | {format_duration(total_cpu)} runtime\n\n"
        f"💡 <i>Quick shortcuts below or message agy anytime.</i>"
    )


def should_export_as_file(prompt: str, response_text: str) -> bool:
    """
    Determine whether the response should be automatically exported as a standalone HTML file.
    Triggers when:
    - User explicitly requested a file/report/diff export
    - Response contains large git diffs or patch blocks (> 500 chars)
    - Response text is long (> 2000 chars)
    - Response contains extensive code blocks (> 1000 chars)
    """
    p_lower = prompt.lower()
    # Explicit user requests
    if any(k in p_lower for k in ("发我文件", "发文件", "导出文件", "生成文件", "生成html", "发我html", "export file", "send file", "as file", "export html", "diff", "补丁", "报告")):
        return True

    # Large content threshold
    if len(response_text) >= 2000:
        return True

    # Code diff or patch
    if "```diff" in response_text or "diff --git" in response_text or "--- a/" in response_text:
        if len(response_text) >= 500:
            return True

    # Contains large code blocks
    code_matches = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", response_text, re.DOTALL)
    total_code_len = sum(len(c) for c in code_matches)
    if total_code_len >= 1000:
        return True

    return False


def generate_standalone_html(
    title: str,
    markdown_content: str,
    metadata: Optional[Dict[str, Any]] = None,
    is_html_content: bool = False,
) -> str:
    """
    Generate a complete, self-contained, mobile-first responsive HTML report from Markdown or HTML.
    Includes:
    - Mobile-first responsive layout (meta viewport, adaptive containers, touch horizontal scroll)
    - Auto dark/light mode CSS
    - Styled syntax highlighted code blocks and diffs (green additions, red deletions)
    - Clean table styles
    - Metadata badge bar (tokens, duration, model, conversation ID, timestamp)
    """
    metadata = metadata or {}
    meta_badges = []
    if "time" in metadata and metadata["time"]:
        meta_badges.append(f'<span class="badge">⏱ {html.escape(str(metadata["time"]))}</span>')
    if "tokens" in metadata and metadata["tokens"]:
        meta_badges.append(f'<span class="badge">🪙 {html.escape(str(metadata["tokens"]))} tokens</span>')
    if "model" in metadata and metadata["model"]:
        meta_badges.append(f'<span class="badge">🤖 {html.escape(str(metadata["model"]))}</span>')
    if "conversation_id" in metadata and metadata["conversation_id"]:
        meta_badges.append(f'<span class="badge">🆔 {html.escape(str(metadata["conversation_id"])[:8])}</span>')
    if "created_at" in metadata and metadata["created_at"]:
        meta_badges.append(f'<span class="badge">📅 {html.escape(str(metadata["created_at"]))}</span>')

    badges_html = " ".join(meta_badges)

    if is_html_content:
        src = markdown_content
    else:
        # 1. Protect code blocks with diff & syntax styling
        code_blocks: List[str] = []

        def save_cb(m: re.Match) -> str:
            lang = (m.group(1) or "").strip().lower()
            code = m.group(2)
            idx = len(code_blocks)
            lines = code.split("\n")
            formatted_lines = []
            is_diff = lang in ("diff", "patch") or any(l.startswith("@@") or l.startswith("diff --git") for l in lines[:5])
            for l in lines:
                esc = html.escape(l, quote=False)
                if is_diff and esc.startswith("+") and not esc.startswith("+++"):
                    formatted_lines.append(f'<span class="diff-add">{esc}</span>')
                elif is_diff and esc.startswith("-") and not esc.startswith("---"):
                    formatted_lines.append(f'<span class="diff-sub">{esc}</span>')
                elif is_diff and (esc.startswith("@@") or esc.startswith("diff --git") or esc.startswith("index ")):
                    formatted_lines.append(f'<span class="diff-info">{esc}</span>')
                else:
                    formatted_lines.append(esc)

            lang_label = lang or ("diff" if is_diff else "code")
            tag = (
                f'<div class="code-wrapper">'
                f'<div class="code-header"><span class="lang">{html.escape(lang_label)}</span></div>'
                f'<pre><code>' + "\n".join(formatted_lines) + f'</code></pre></div>'
            )
            code_blocks.append(tag)
            return f"\x00CB{idx}\x00"

        src = re.sub(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", save_cb, markdown_content, flags=re.DOTALL)

        # 2. Protect inline code
        inline_codes: List[str] = []

        def save_ic(m: re.Match) -> str:
            c = m.group(1)
            idx = len(inline_codes)
            inline_codes.append(f'<code>{html.escape(c, quote=False)}</code>')
            return f"\x00IC{idx}\x00"

        src = re.sub(r"`([^`\n]+)`", save_ic, src)

        # 3. Escape normal text
        src = html.escape(src, quote=False)

        # 4. Links
        src = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', src)

        # 5. Headers
        src = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", src, flags=re.MULTILINE)
        src = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", src, flags=re.MULTILINE)
        src = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", src, flags=re.MULTILINE)

        # 6. Bold & Italic & Strikethrough
        src = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", src)
        src = re.sub(r"__(.+?)__", r"<strong>\1</strong>", src)
        src = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<em>\1</em>", src)
        src = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<em>\1</em>", src)
        src = re.sub(r"~~(.+?)~~", r"<del>\1</del>", src)

        # 7. Blockquotes
        def repl_bq(m: re.Match) -> str:
            raw = m.group(0).rstrip("\r\n")
            lines = [re.sub(r"^&gt;\s?", "", l) for l in raw.split("\n")]
            return f'<blockquote>{chr(10).join(lines)}</blockquote>\n'

        src = re.sub(r"^(?:&gt;.*(?:\n|$))+", repl_bq, src, flags=re.MULTILINE)

        # 8. Lists
        src = re.sub(r"^[\*\-\+]\s+(.+)$", r"<li>\1</li>", src, flags=re.MULTILINE)
        src = re.sub(r"((?:<li>.+</li>\n?)+)", r"<ul>\1</ul>", src)

        # 9. Paragraphs
        paragraphs = src.split("\n\n")
        body_parts: List[str] = []
        for p in paragraphs:
            p_str = p.strip()
            if not p_str:
                continue
            if (
                p_str.startswith("<h")
                or p_str.startswith("<ul")
                or p_str.startswith("<blockquote")
                or p_str.startswith("\x00CB")
            ):
                body_parts.append(p_str)
            else:
                p_formatted = p_str.replace("\n", "<br>")
                body_parts.append(f"<p>{p_formatted}</p>")

        src = "\n".join(body_parts)

        # 10. Restore code blocks & inline code
        for idx, tag in enumerate(inline_codes):
            src = src.replace(f"\x00IC{idx}\x00", tag)
        for idx, tag in enumerate(code_blocks):
            src = src.replace(f"\x00CB{idx}\x00", tag)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>
:root {{
  --bg: #0d1117;
  --card-bg: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --primary: #2f81f7;
  --code-bg: #0d1117;
  --add-bg: rgba(46, 160, 67, 0.18);
  --add-text: #3fb950;
  --sub-bg: rgba(248, 81, 73, 0.18);
  --sub-text: #ff7b72;
  --info-text: #d2a8ff;
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #f6f8fa;
    --card-bg: #ffffff;
    --border: #d0d7de;
    --text: #1f2328;
    --text-muted: #656d76;
    --primary: #0969da;
    --code-bg: #f6f8fa;
    --add-bg: #e6ffec;
    --add-text: #1a7f37;
    --sub-bg: #ffebe9;
    --sub-text: #cf222e;
    --info-text: #8250df;
  }}
}}
* {{
  box-sizing: border-box;
}}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg);
  color: var(--text);
  line-height: 1.6;
  margin: 0;
  padding: 12px;
  -webkit-text-size-adjust: 100%;
  word-break: break-word;
}}
.container {{
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
  background-color: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  overflow-wrap: break-word;
}}
@media (max-width: 480px) {{
  body {{
    padding: 6px;
  }}
  .container {{
    padding: 12px;
    border-radius: 6px;
  }}
  h1 {{ font-size: 1.35rem; }}
  h2 {{ font-size: 1.15rem; }}
  h3 {{ font-size: 1.0rem; }}
  pre {{
    padding: 10px;
    font-size: 0.8rem;
  }}
}}
.header {{
  border-bottom: 1px solid var(--border);
  padding-bottom: 12px;
  margin-bottom: 16px;
}}
h1, h2, h3 {{
  color: var(--text);
  margin-top: 20px;
  margin-bottom: 10px;
}}
.badges {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}}
.badge {{
  font-size: 0.8rem;
  background-color: var(--bg);
  border: 1px solid var(--border);
  padding: 3px 8px;
  border-radius: 12px;
  color: var(--text-muted);
}}
.code-wrapper {{
  margin: 16px 0;
}}
.code-header {{
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-bottom: 4px;
}}
pre {{
  background-color: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.88rem;
  margin: 0;
  max-width: 100%;
}}
code {{
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background-color: var(--code-bg);
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 0.9em;
}}
pre code {{
  padding: 0;
  background: transparent;
}}
.diff-add {{
  background-color: var(--add-bg);
  color: var(--add-text);
  display: block;
}}
.diff-sub {{
  background-color: var(--sub-bg);
  color: var(--sub-text);
  display: block;
}}
.diff-info {{
  color: var(--info-text);
  display: block;
  font-weight: 600;
}}
blockquote {{
  border-left: 4px solid var(--primary);
  padding: 6px 16px;
  margin: 12px 0;
  background-color: var(--bg);
  color: var(--text-muted);
  border-radius: 0 6px 6px 0;
}}
ul, ol {{
  padding-left: 20px;
}}
li {{
  margin-bottom: 4px;
}}
a {{
  color: var(--primary);
  text-decoration: none;
}}
a:hover {{
  text-decoration: underline;
}}
table {{
  display: block;
  width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  border-collapse: collapse;
  margin: 12px 0;
}}
th, td {{
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}}
th {{
  background-color: var(--bg);
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h2 style="margin:0;">{html.escape(title)}</h2>
    <div class="badges">{badges_html}</div>
  </div>
  <div class="content">{src}</div>
</div>
</body>
</html>"""
    return html_doc


@dataclass
class AgyFileBlock:
    """Represents an explicit [AGY_FILE] protocol block emitted by agy."""

    filename: str = "report.html"
    title: str = "agy Response"
    layout: str = "mobile"
    content: str = ""
    mode: str = "html"  # "html", "path"
    raw_block: str = ""


AGY_FILE_PATTERN = re.compile(
    r"(?:^|\r?\n)[ \t]*\[AGY_FILE\][ \t]*\r?\n(.*?)\r?\n[ \t]*\[END_AGY_FILE\][ \t]*(?=\r?\n|$)",
    re.DOTALL | re.IGNORECASE,
)


def sanitize_filename(raw_name: str, default_ext: str = ".html") -> str:
    """Sanitize filename to prevent path traversal and ensure safe valid HTML filename."""
    if not raw_name:
        return f"report{default_ext}"
    name = Path(raw_name.strip()).name
    # Replace dangerous characters with underscore
    name = re.sub(r'[\/\\:\*\?"<>\|\x00-\x1f]', '_', name)
    name = name.strip(" .")
    if not name:
        name = "report"
    if not name.lower().endswith(".html") and not name.lower().endswith(".htm"):
        name = f"{name}{default_ext}"
    return name


def detect_content_mode(content: str, mode_hint: Optional[str] = None) -> str:
    """
    Detect whether block content is:
    - 'path': file path to read from workspace
    - 'html': direct standalone HTML string or fragment
    """
    if mode_hint:
        hint = mode_hint.strip().lower()
        if hint in ("path", "file", "filepath"):
            return "path"
        if hint in ("html", "raw_html", "htm"):
            return "html"

    trimmed = content.strip()
    lines = [l for l in trimmed.splitlines() if l.strip()]

    # 1. File path check: single non-empty line without html tags
    if len(lines) == 1:
        line = lines[0].strip()
        is_path_prefix = line.startswith(("./", "../", "/", "~/", "runtime/", "out/", "build/", "dist/"))
        is_file_ext = bool(re.search(r"\.(html|htm|txt|json|diff|patch|log|py|js|sh|css|yml|yaml|csv|pdf)$", line, re.IGNORECASE))
        has_no_spaces = " " not in line
        has_no_html_tags = not any(c in line for c in ("<", ">"))

        if (is_path_prefix or is_file_ext) and has_no_html_tags and len(line) < 300:
            return "path"

    return "html"


def parse_agy_file_block(raw_body: str) -> AgyFileBlock:
    """Parse the body of an [AGY_FILE]...[END_AGY_FILE] block."""
    headers: Dict[str, str] = {}
    content = raw_body

    # Look for '---' separator
    sep_match = re.search(r"^[ \t]*---[ \t]*$", raw_body, re.MULTILINE)
    if sep_match:
        header_part = raw_body[:sep_match.start()]
        content = raw_body[sep_match.end():].lstrip("\r\n")
        for line in header_part.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                headers[k.strip().lower()] = v.strip()
    else:
        # If no '---', check if first few lines are key: value headers
        lines = raw_body.splitlines()
        content_start_idx = 0
        for idx, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("#"):
                continue
            if ":" in line_str and not line_str.startswith(("<", "#", "*", "-", "+", "`", "|")):
                k, v = line_str.split(":", 1)
                k_clean = k.strip().lower()
                if k_clean in ("filename", "title", "layout", "mode", "type"):
                    headers[k_clean] = v.strip()
                    content_start_idx = idx + 1
                    continue
            break
        if content_start_idx > 0:
            content = "\n".join(lines[content_start_idx:]).lstrip("\r\n")

    raw_filename = headers.get("filename", "")
    title = headers.get("title", "agy Response")
    layout = headers.get("layout", "mobile")
    mode_hint = headers.get("mode") or headers.get("type")

    detected_mode = detect_content_mode(content, mode_hint=mode_hint)

    if not raw_filename and detected_mode == "path":
        derived_name = Path(content.strip()).name
        raw_filename = derived_name if derived_name else "report.html"

    filename = sanitize_filename(raw_filename if raw_filename else "report.html")

    return AgyFileBlock(
        filename=filename,
        title=title,
        layout=layout,
        content=content,
        mode=detected_mode,
        raw_block=raw_body,
    )


def extract_agy_file_blocks(text: str) -> Tuple[str, List[AgyFileBlock]]:
    """
    Extract all explicit [AGY_FILE]...[END_AGY_FILE] blocks from text.
    Returns:
        (cleaned_text, blocks)
        - cleaned_text: Visible user message with protocol blocks removed
        - blocks: List of parsed AgyFileBlock objects
    """
    if not text or "[AGY_FILE]" not in text:
        return text, []

    blocks: List[AgyFileBlock] = []

    def repl(m: re.Match) -> str:
        raw_body = m.group(1)
        block = parse_agy_file_block(raw_body)
        blocks.append(block)
        return ""

    cleaned = AGY_FILE_PATTERN.sub(repl, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, blocks


@dataclass
class AgyActionItem:
    """Action button item from [AGY_ACTIONS] protocol."""

    label: str
    action: str
    description: str = ""


AGY_ACTIONS_PATTERN = re.compile(r"\[AGY_ACTIONS\](.*?)\[END_AGY_ACTIONS\]", re.DOTALL | re.IGNORECASE)


def extract_agy_action_blocks(text: str) -> Tuple[str, List[AgyActionItem]]:
    """
    Extract explicit [AGY_ACTIONS]...[END_AGY_ACTIONS] blocks from text.
    Format inside the block:
      - 🚀 Run Tests: pytest -v
      - 📝 View Diff: /diff
      - 💡 Explain Architecture
    Returns:
      (cleaned_text, list_of_actions)
    """
    if not text or "[AGY_ACTIONS]" not in text:
        return text, []

    actions: List[AgyActionItem] = []

    def repl_action(m: re.Match) -> str:
        body = m.group(1)
        for line in body.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            # Remove leading bullet characters
            line_clean = re.sub(r"^[\*\-\+•]\s*", "", line_str).strip()
            if not line_clean:
                continue
            if ":" in line_clean:
                lbl, act = line_clean.split(":", 1)
                actions.append(AgyActionItem(label=lbl.strip(), action=act.strip()))
            else:
                actions.append(AgyActionItem(label=line_clean, action=line_clean))
        return ""

    cleaned = AGY_ACTIONS_PATTERN.sub(repl_action, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, actions


@dataclass
class AgyVoiceBlock:
    """Spoken voice summary payload from [AGY_VOICE] protocol."""

    text: str
    lang: str = "zh"  # "zh" | "en"


AGY_VOICE_PATTERN = re.compile(
    r"\[AGY_VOICE(?:\s+lang=[\"']?([a-zA-Z_-]+)[\"']?)?\](.*?)\[END_AGY_VOICE\]",
    re.DOTALL | re.IGNORECASE,
)


def extract_agy_voice_block(text: str) -> Tuple[str, Optional[AgyVoiceBlock]]:
    """
    Extract explicit [AGY_VOICE lang="zh|en"]...[END_AGY_VOICE] block from text.
    The block is completely removed from the visible text so it does not affect formatting.
    Returns:
      (cleaned_text, voice_block_or_None)
    """
    if not text or "[AGY_VOICE" not in text:
        return text, None

    voice_block: Optional[AgyVoiceBlock] = None

    def repl_voice(m: re.Match) -> str:
        nonlocal voice_block
        lang = m.group(1) or "zh"
        body = m.group(2).strip()
        if body:
            # Strip any accidental markdown backticks or bold/italic markers
            clean_body = re.sub(r"[`*_#~]", "", body)
            voice_block = AgyVoiceBlock(text=clean_body.strip(), lang=lang.lower()[:2])
        return ""

    cleaned = AGY_VOICE_PATTERN.sub(repl_voice, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, voice_block


def wrap_raw_html_for_mobile(
    title: str,
    raw_html_content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Ensure raw HTML content is a valid, standalone, mobile-responsive HTML document."""
    stripped = raw_html_content.strip()
    is_full_doc = "<!doctype html" in stripped.lower() or "<html" in stripped.lower()

    if is_full_doc:
        doc = stripped
        if 'name="viewport"' not in doc.lower() and "name='viewport'" not in doc.lower():
            viewport_tag = '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            if "<head>" in doc.lower():
                doc = re.sub(r"(<head[^>]*>)", r"\1\n" + viewport_tag, doc, count=1, flags=re.IGNORECASE)
            elif "<html>" in doc.lower():
                doc = re.sub(r"(<html[^>]*>)", r"\1\n<head>\n" + viewport_tag + "\n</head>", doc, count=1, flags=re.IGNORECASE)
            else:
                doc = viewport_tag + "\n" + doc

        if "overflow-x" not in doc.lower():
            responsive_style = """<style>
pre, code, table { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
body { -webkit-text-size-adjust: 100%; margin: 0; padding: 12px; }
img { max-width: 100%; height: auto; }
</style>"""
            if "</head>" in doc.lower():
                doc = re.sub(r"(</head>)", responsive_style + r"\n\1", doc, count=1, flags=re.IGNORECASE)
            else:
                doc = responsive_style + "\n" + doc
        return doc

    return generate_standalone_html(
        title=title,
        markdown_content=raw_html_content,
        metadata=metadata,
        is_html_content=True,
    )


def render_agy_file(
    block: AgyFileBlock,
    base_dir: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Render an AgyFileBlock into a mobile-optimized standalone HTML document.
    Directly uses the agy-generated HTML or workspace HTML file without intermediate Markdown conversion.
    Returns:
        (html_content, error_message)
    """
    mode = block.mode
    content = block.content.strip()

    if mode == "path":
        raw_path = content
        base = Path(base_dir) if base_dir else Path.cwd()
        target_path = Path(raw_path).expanduser()
        if not target_path.is_absolute():
            target_path = (base / target_path).resolve()
        else:
            target_path = target_path.resolve()

        if not target_path.is_file():
            return None, f"File not found: {raw_path} (resolved: {target_path})"

        try:
            file_text = target_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return None, f"Failed to read file {raw_path}: {e}"

        html_doc = wrap_raw_html_for_mobile(title=block.title, raw_html_content=file_text, metadata=metadata)
        return html_doc, None

    else:  # html (direct HTML generated by agy)
        html_doc = wrap_raw_html_for_mobile(title=block.title, raw_html_content=block.content, metadata=metadata)
        return html_doc, None


if __name__ == "__main__":
    sample = """### Title Header
Here is **bold text** and `inline code with <brackets> & special`.
Also link: [Telegram](https://telegram.org)

```diff
diff --git a/file.py b/file.py
@@ -1,3 +1,4 @@
 def test():
+    print("new line")
-    print("old line")
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
    doc = generate_standalone_html("Code Diff Report", sample, {"time": "2.3s", "tokens": "17.5k"})
    print("\nGenerated standalone HTML length:", len(doc))

    proto_sample = """Here is the work summary:

[AGY_FILE]
filename: changes.html
title: Code Changes
---
<!DOCTYPE html>
<html>
<head><title>Code Changes</title></head>
<body>
<h1>Changes Summary</h1>
<ul>
  <li>Direct HTML generation</li>
  <li>No markdown intermediate format</li>
</ul>
</body>
</html>
[END_AGY_FILE]

All done!"""
    clean_txt, blks = extract_agy_file_blocks(proto_sample)
    print("\nProtocol extraction test:")
    print("Cleaned text:", clean_txt)
    print("Extracted blocks:", len(blks), blks[0].filename, blks[0].mode)
    rendered_h, err = render_agy_file(blks[0])
    print("Rendered HTML length:", len(rendered_h) if rendered_h else 0, "err:", err)
    print("MessageFormatter self-test passed!")

