"""Subprocess bridge for running agy CLI and parsing its streaming output."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgyEvent:
    """An event emitted by the agy stream-json protocol."""

    type: str  # "init", "tool", "text_delta", "checkpoint", "result", "error", "raw"
    conversation_id: Optional[str] = None
    step_index: Optional[int] = None
    tool_name: Optional[str] = None
    tool_state: Optional[str] = None  # "ACTIVE", "DONE", "ERROR"
    tool_info: Dict[str, Any] = field(default_factory=dict)
    text_delta: str = ""
    response: str = ""
    status: str = ""  # "SUCCESS", "ERROR", etc.
    duration_seconds: Optional[float] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class AgyBridge:
    """Manages executing the agy CLI and streaming its JSON events."""

    def __init__(
        self,
        agy_bin: str = "/Users/jim/.local/bin/agy",
        default_working_dir: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.agy_bin = agy_bin
        self.default_working_dir = default_working_dir or os.getcwd()
        self.timeout_seconds = timeout_seconds
        self._current_process: Optional[asyncio.subprocess.Process] = None
        self._is_stopped = False

    def build_command(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> List[str]:
        """Construct command line arguments for running agy."""
        cmd = [
            self.agy_bin,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]

        if conversation_id:
            cmd.extend(["--conversation", conversation_id])
        else:
            cmd.append("--new-project")

        effective_dir = working_dir or self.default_working_dir
        if effective_dir:
            cmd.extend(["--add-dir", effective_dir])

        if model:
            cmd.extend(["--model", model])

        if effort:
            cmd.extend(["--effort", effort])

        cmd.extend(["--print", prompt])
        return cmd

    async def run_streaming(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> AsyncIterator[AgyEvent]:
        """Run agy CLI and yield streaming AgyEvent instances."""
        self._is_stopped = False
        cmd = self.build_command(
            prompt=prompt,
            conversation_id=conversation_id,
            model=model,
            effort=effort,
            working_dir=working_dir,
        )
        effective_dir = working_dir or self.default_working_dir

        logger.info("Executing agy command: %s (cwd=%s)", " ".join(cmd[:6]) + " ...", effective_dir)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_dir,
            )
            self._current_process = process
        except FileNotFoundError:
            err_msg = f"agy binary not found at: {self.agy_bin}"
            logger.error(err_msg)
            yield AgyEvent(type="error", status="ERROR", response=err_msg)
            return
        except Exception as e:
            err_msg = f"Failed to start agy process: {e}"
            logger.error(err_msg)
            yield AgyEvent(type="error", status="ERROR", response=err_msg)
            return

        assert process.stdout is not None
        assert process.stderr is not None

        try:
            # Stream lines from stdout
            while True:
                line_bytes = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=self.timeout_seconds,
                )
                if not line_bytes:
                    break

                line_str = line_bytes.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                event = self._parse_line(line_str)
                if event:
                    yield event

            await process.wait()

            # Check if non-zero exit and no result event was generated
            if process.returncode != 0 and not self._is_stopped:
                stderr_bytes = await process.stderr.read()
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                logger.warning("agy exited with code %d. Stderr: %s", process.returncode, stderr_text)
                if stderr_text:
                    yield AgyEvent(
                        type="error",
                        status="ERROR",
                        response=f"agy error (exit code {process.returncode}): {stderr_text}",
                    )

        except asyncio.TimeoutError:
            logger.error("agy process timed out after %d seconds", self.timeout_seconds)
            await self.stop()
            yield AgyEvent(
                type="error",
                status="TIMEOUT",
                response=f"⏱ agy process timed out after {self.timeout_seconds} seconds.",
            )
        except asyncio.CancelledError:
            logger.info("agy streaming task was cancelled")
            await self.stop()
            raise
        finally:
            self._current_process = None

    def _parse_line(self, line: str) -> Optional[AgyEvent]:
        """Parse a single line from stream-json output."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return AgyEvent(type="raw", raw_text=line)

        event_name = data.get("event")
        if event_name == "init":
            conv_id = data.get("conversation_id")
            return AgyEvent(
                type="init",
                conversation_id=conv_id,
                payload=data.get("init", {}),
            )

        elif event_name == "step_update":
            step = data.get("step_update", {})
            step_type = step.get("step_type")
            conv_id = step.get("conversation_id")
            step_idx = step.get("step_index")

            if step_type == "tool":
                return AgyEvent(
                    type="tool",
                    conversation_id=conv_id,
                    step_index=step_idx,
                    tool_name=step.get("tool_name"),
                    tool_state=step.get("state"),
                    tool_info=step.get("tool_info", {}),
                    duration_seconds=step.get("duration_seconds"),
                    payload=step,
                )
            elif step_type == "agent_response":
                return AgyEvent(
                    type="text_delta",
                    conversation_id=conv_id,
                    step_index=step_idx,
                    text_delta=step.get("text_delta", ""),
                    duration_seconds=step.get("duration_seconds"),
                    usage=step.get("usage", {}),
                    payload=step,
                )
            elif step_type == "checkpoint":
                return AgyEvent(
                    type="checkpoint",
                    conversation_id=conv_id,
                    step_index=step_idx,
                    duration_seconds=step.get("duration_seconds"),
                    usage=step.get("usage", {}),
                    payload=step,
                )
            elif step_type == "user_input":
                return AgyEvent(
                    type="user_input",
                    conversation_id=conv_id,
                    step_index=step_idx,
                    payload=step,
                )
            else:
                return AgyEvent(
                    type="step_update",
                    conversation_id=conv_id,
                    step_index=step_idx,
                    payload=step,
                )

        elif event_name == "result":
            res = data.get("result", {})
            return AgyEvent(
                type="result",
                conversation_id=res.get("conversation_id"),
                status=res.get("status", "SUCCESS"),
                response=res.get("response", ""),
                duration_seconds=res.get("duration_seconds"),
                usage=res.get("usage", {}),
                payload=res,
            )

        return AgyEvent(type="raw", raw_text=line, payload=data)

    async def stop(self) -> None:
        """Interrupt and terminate the running agy process."""
        self._is_stopped = True
        proc = self._current_process
        if proc and proc.returncode is None:
            logger.info("Stopping running agy subprocess (PID: %s)...", proc.pid)
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("Error stopping process: %s", e)


if __name__ == "__main__":
    async def main() -> None:
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Say hello in 3 words"
        print(f"Testing AgyBridge with prompt: {prompt!r}")
        bridge = AgyBridge()
        async for event in bridge.run_streaming(prompt=prompt):
            if event.type == "init":
                print(f"[INIT] Conv ID: {event.conversation_id}")
            elif event.type == "tool":
                print(f"[TOOL] {event.tool_name} ({event.tool_state}): {event.tool_info}")
            elif event.type == "text_delta":
                print(f"[TEXT] {event.text_delta}", end="", flush=True)
            elif event.type == "result":
                print(f"\n[RESULT] Status: {event.status}, Tokens: {event.usage.get('total_tokens')}, Time: {event.duration_seconds}s")
            elif event.type == "error":
                print(f"\n[ERROR] {event.response}")

    asyncio.run(main())
