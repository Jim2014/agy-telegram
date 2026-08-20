"""gateway/ipc_router.py — Decentralized Cross-Project Communication Router & Protocol Engine.

Implements the AGY_IPC protocol:
- Extracts [AGY_IPC to="..."]...[END_AGY_IPC] blocks from Agent text.
- Wraps messages in [AGY_INCOMING from="..." to="..." ...] envelope headers.
- Formats Telegram pinned expandable accordion cards (<blockquote expandable>).
- Manages cross-project task dispatching, loop prevention, and return injection.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class IPCCall:
    """Represents an outbound cross-project call emitted by an Agent."""

    target_project: str
    content: str
    source_project: str = ""
    hop_count: int = 1
    raw_block: str = ""


@dataclass
class IPCResult:
    """Represents the execution outcome of an IPC call from the target project."""

    target_project: str
    source_project: str
    status: str  # "SUCCESS" or "ERROR"
    response_content: str
    duration_seconds: float = 0.0
    error_message: str = ""
    tools_executed: List[str] = field(default_factory=list)


def extract_ipc_calls(
    text: str,
    default_source: str = "",
) -> Tuple[str, List[IPCCall]]:
    """
    Extract all [AGY_IPC to="..."]...[END_AGY_IPC] blocks from text.
    Returns (cleaned_text_without_ipc_blocks, list_of_ipc_calls).
    """
    if not text or "[AGY_IPC" not in text:
        return text, []

    pattern = re.compile(
        r'\[AGY_IPC\s+to=["\']?([a-zA-Z0-9_\-\.]+)["\']?(?:\s+hop=["\']?(\d+)["\']?)?\]\s*(.*?)\s*\[END_AGY_IPC\]',
        re.DOTALL | re.IGNORECASE,
    )

    calls: List[IPCCall] = []

    def repl(match: re.Match) -> str:
        target = match.group(1).strip()
        hop_str = match.group(2)
        hop_count = int(hop_str) if hop_str and hop_str.isdigit() else 1
        body = match.group(3).strip()

        calls.append(
            IPCCall(
                target_project=target,
                content=body,
                source_project=default_source,
                hop_count=hop_count,
                raw_block=match.group(0),
            )
        )
        return ""

    cleaned = pattern.sub(repl, text)
    # Clean up any leftover whitespace or empty newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def format_incoming_envelope(
    source: str,
    target: str,
    body: str,
    time_str: Optional[str] = None,
    hop_count: int = 1,
) -> str:
    """Wrap task instructions in a standardized AGY_INCOMING metadata envelope."""
    if not time_str:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    envelope = (
        f'[AGY_INCOMING from="{source}" to="{target}" time="{time_str}" hop="{hop_count}"]\n'
        f"{body.strip()}\n"
        f"[END_AGY_INCOMING]\n\n"
        f'(System Directive: This is an automated cross-project collaboration request from [{source}]. '
        f'Please execute the requested task in your workspace, and reply with [AGY_IPC to="{source}"]...[END_AGY_IPC].)'
    )
    return envelope


def format_return_envelope(
    source: str,
    target: str,
    body: str,
    status: str = "SUCCESS",
) -> str:
    """Format return summary to be injected back into the calling Agent."""
    return (
        f'[AGY_INCOMING from="{source}" to="{target}" status="{status}"]\n'
        f"{body.strip()}\n"
        f"[END_AGY_INCOMING]"
    )


def format_pinned_accordion_card(
    target_project: str,
    source_project: str,
    task_preview: str,
    tools_log: List[str],
    status_text: str,
    is_done: bool = False,
    is_error: bool = False,
    duration_seconds: float = 0.0,
    final_reply: str = "",
) -> str:
    """
    Format a native Telegram expandable accordion card (<blockquote expandable>).
    Designed for pinned message in-place streaming updates.
    """
    proj_esc = html.escape(target_project, quote=False)
    src_esc = html.escape(source_project, quote=False)

    if is_error:
        icon = "🔴"
        badge = f"<i>Failed ({duration_seconds:.1f}s)</i>"
    elif is_done:
        icon = "🟢"
        badge = f"<i>Completed ({duration_seconds:.1f}s)</i>"
    else:
        icon = "🟡"
        badge = f"<i>In Progress ({duration_seconds:.1f}s)</i>"

    dur_str = f" • ⏱ {duration_seconds:.1f}s" if duration_seconds > 0 else ""
    header = f"📌 <b>[Background Task · {proj_esc}]</b> {icon} {badge}"

    # Body lines inside expandable block
    body_lines: List[str] = []
    
    # Task instruction
    short_task = task_preview.strip().replace("\n", " ")
    if len(short_task) > 120:
        short_task = short_task[:117] + "..."
    body_lines.append(f"<b>Task from {src_esc}:</b> {html.escape(short_task, quote=False)}")
    body_lines.append("———")

    # Recent tool logs (last 6 steps)
    if tools_log:
        recent = tools_log[-6:]
        for t in recent:
            body_lines.append(f"• {t.strip()}")
        body_lines.append("———")

    # Status or final reply
    if is_error:
        body_lines.append(f"❌ <b>Error:</b> {html.escape(status_text, quote=False)}")
    elif is_done and final_reply:
        clean_rep = final_reply.strip()
        if len(clean_rep) > 500:
            clean_rep = clean_rep[:497] + "..."
        body_lines.append(f"<b>Result:</b>\n{html.escape(clean_rep, quote=False)}")
    else:
        body_lines.append(f"⏱ <i>{html.escape(status_text, quote=False)}</i>")

    inner_content = "\n".join(body_lines)
    return f"{header}\n\n<blockquote expandable>\n{inner_content}\n</blockquote>"
