"""gateway/ipc_router.py — Decentralized Cross-Project Communication Router & Protocol Engine.

Implements the AGY_IPC protocol:
- Extracts [AGY_IPC to="..."]...[END_AGY_IPC] blocks from Agent text.
- Wraps messages in [AGY_INCOMING from="..." to="..." ...] envelope headers.
- Formats Telegram pinned expandable accordion cards (<blockquote expandable>).
- Manages cross-project task dispatching, loop prevention, and return injection.
"""

from __future__ import annotations

import html
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from gateway.message_formatter import markdown_to_telegram_html

DEFAULT_MAX_HOPS = 3


def generate_trace_id() -> str:
    """Generate a short correlation trace ID for turn and cross-project IPC spans."""
    return uuid.uuid4().hex[:16]


def generate_span_id() -> str:
    """Generate a span ID for an individual agent turn or IPC call."""
    return uuid.uuid4().hex[:12]


@dataclass
class IPCCall:
    """Represents an outbound cross-project call emitted by an Agent."""

    target_project: str
    content: str
    source_project: str = ""
    hop_count: int = 1
    raw_block: str = ""
    trace_id: str = ""
    visited_projects: Tuple[str, ...] = ()


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
    trace_id: str = ""
    span_id: str = ""
    hop_count: int = 1
    blocked_reason: str = ""


def extract_ipc_calls(
    text: str,
    default_source: str = "",
    default_trace_id: str = "",
    default_visited: Optional[Iterable[str]] = None,
) -> Tuple[str, List[IPCCall]]:
    """
    Extract all [AGY_IPC to="..."]...[END_AGY_IPC] blocks from text.
    Returns (cleaned_text_without_ipc_blocks, list_of_ipc_calls).
    """
    if not text or "[AGY_IPC" not in text:
        return text, []

    pattern = re.compile(
        r'\[AGY_IPC\s+([^\]]+)\]\s*(.*?)\s*\[END_AGY_IPC\]',
        re.DOTALL | re.IGNORECASE,
    )

    calls: List[IPCCall] = []
    base_visited = tuple(default_visited) if default_visited else ()

    def repl(match: re.Match) -> str:
        attrs_str = match.group(1)
        to_m = re.search(r'(?:^|\s)to=["\']?([a-zA-Z0-9_\-\.]+)["\']?', attrs_str, re.IGNORECASE)
        if not to_m:
            return match.group(0)

        target = to_m.group(1).strip()
        hop_m = re.search(r'(?:^|\s)hop=["\']?(\d+)["\']?', attrs_str, re.IGNORECASE)
        hop_count = int(hop_m.group(1)) if hop_m else 1
        trace_m = re.search(r'(?:^|\s)trace_id=["\']?([a-zA-Z0-9_\-]+)["\']?', attrs_str, re.IGNORECASE)
        trace_val = trace_m.group(1).strip() if trace_m else default_trace_id
        path_m = re.search(r'(?:^|\s)path=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
        visited = tuple(p.strip() for p in path_m.group(1).split("->") if p.strip()) if path_m else base_visited
        body = match.group(2).strip()

        calls.append(
            IPCCall(
                target_project=target,
                content=body,
                source_project=default_source,
                hop_count=hop_count,
                raw_block=match.group(0),
                trace_id=trace_val,
                visited_projects=visited,
            )
        )
        return ""

    cleaned = pattern.sub(repl, text)
    # Clean up any leftover whitespace or empty newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def validate_ipc_call(
    call: IPCCall,
    max_hops: int = DEFAULT_MAX_HOPS,
    visited_projects: Optional[Iterable[str]] = None,
) -> Optional[Tuple[str, str]]:
    """
    Validate an IPC call against cycle and max-hop guardrails.
    Returns (reason_code, message) if blocked, or None if permitted.
    """
    visited_list = [p for p in (visited_projects or call.visited_projects or ()) if p]
    if call.source_project and call.source_project not in visited_list:
        visited_list.insert(0, call.source_project)

    if call.source_project and call.target_project == call.source_project:
        return (
            "CYCLE_DETECTED",
            f"Self-delegation blocked: '{call.source_project}' cannot invoke itself.",
        )

    if call.target_project in visited_list:
        chain = " -> ".join(visited_list + [call.target_project])
        return (
            "CYCLE_DETECTED",
            f"IPC cycle detected: '{call.target_project}' already visited ({chain}).",
        )

    if call.hop_count > max_hops:
        return (
            "MAX_HOPS_EXCEEDED",
            f"IPC max hop limit exceeded (hop={call.hop_count} > max_hops={max_hops}).",
        )

    return None


def format_incoming_envelope(
    source: str,
    target: str,
    body: str,
    time_str: Optional[str] = None,
    hop_count: int = 1,
    trace_id: Optional[str] = None,
    visited_projects: Optional[Iterable[str]] = None,
) -> str:
    """Wrap task instructions in a standardized AGY_INCOMING metadata envelope."""
    if not time_str:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    extra_attrs = ""
    if trace_id:
        extra_attrs += f' trace_id="{trace_id}"'
    if visited_projects:
        chain = "->".join(p for p in visited_projects if p)
        if chain:
            extra_attrs += f' path="{chain}"'

    envelope = (
        f'[AGY_INCOMING from="{source}" to="{target}" time="{time_str}" hop="{hop_count}"{extra_attrs}]\n'
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
    trace_id: Optional[str] = None,
) -> str:
    """Format return summary to be injected back into the calling Agent."""
    trace_attr = f' trace_id="{trace_id}"' if trace_id else ""
    return (
        f'[AGY_INCOMING from="{source}" to="{target}" status="{status}"{trace_attr}]\n'
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
    trace_id: str = "",
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

    header = f"📌 <b>[Background Task · {proj_esc}]</b> {icon} {badge}"

    # Body lines inside expandable block
    body_lines: List[str] = []

    # Task instruction
    short_task = task_preview.strip().replace("\n", " ")
    if len(short_task) > 120:
        short_task = short_task[:117] + "..."
    body_lines.append(f"<b>Task from {src_esc}:</b> {html.escape(short_task, quote=False)}")
    if trace_id:
        body_lines.append(f"🔎 <b>Trace:</b> <code>{html.escape(trace_id[:8], quote=False)}</code>")
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
        rendered_rep = markdown_to_telegram_html(clean_rep)
        # Telegram forbids nested <blockquote> inside <blockquote expandable>
        rendered_rep = re.sub(r"</?blockquote(?:\s+expandable)?>", "", rendered_rep).strip()
        body_lines.append(f"<b>Result:</b>\n{rendered_rep}")
    else:
        body_lines.append(f"⏱ <i>{html.escape(status_text, quote=False)}</i>")

    inner_content = "\n".join(body_lines)
    return f"{header}\n\n<blockquote expandable>\n{inner_content}\n</blockquote>"


def log_ipc_event(
    source: str,
    target: str,
    event_type: str,
    details: str,
    log_file: Optional[Path] = None,
    trace_id: str = "",
) -> None:
    """Append a structured trace entry to runtime/ipc.log for auditing cross-project calls."""
    if log_file is None:
        log_file = Path("runtime/ipc.log")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        trace_prefix = f"[trace={trace_id[:8]}] " if trace_id else ""
        entry = f"[{ts}] [{event_type.upper()}] [{source} -> {target}] {trace_prefix}{details.strip()}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def log_trace_span(
    trace_id: str,
    span_id: str,
    operation: str,
    status: str,
    duration_ms: float,
    parent_span_id: Optional[str] = None,
    source_project: str = "",
    target_project: str = "",
    hop: int = 0,
    tokens: int = 0,
    model: str = "",
    permission_preset: str = "",
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    trace_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append a structured OpenTelemetry-style JSONL span record to runtime/traces.jsonl."""
    record: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "ts_epoch": round(time.time(), 3),
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "operation": operation,
        "status": status,
        "duration_ms": round(float(duration_ms), 1),
        "source_project": source_project,
        "target_project": target_project,
        "hop": hop,
        "tokens": tokens,
        "model": model,
        "permission_preset": permission_preset,
    }
    if error:
        record["error"] = error
    if metadata:
        record["metadata"] = metadata

    target_file = trace_file if trace_file is not None else Path("runtime/traces.jsonl")
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return record


if __name__ == "__main__":
    import tempfile

    tid = generate_trace_id()
    _, parsed = extract_ipc_calls('[AGY_IPC to="learner" hop="1"]Check tests[END_AGY_IPC]', default_source="admin", default_trace_id=tid)
    assert len(parsed) == 1 and validate_ipc_call(parsed[0]) is None
    assert validate_ipc_call(IPCCall(target_project="admin", content="loop", source_project="admin"))[0] == "CYCLE_DETECTED"
    assert validate_ipc_call(IPCCall(target_project="news", content="deep", source_project="admin", hop_count=4), max_hops=3)[0] == "MAX_HOPS_EXCEEDED"
    with tempfile.TemporaryDirectory() as tmpd:
        tf = Path(tmpd) / "traces.jsonl"
        span = log_trace_span(trace_id=tid, span_id=generate_span_id(), operation="self_test", status="SUCCESS", duration_ms=12.3, trace_file=tf)
        assert tf.is_file() and tid in tf.read_text(encoding="utf-8")
        assert span["status"] == "SUCCESS"
    print("IPC router & trace self-test passed!")

