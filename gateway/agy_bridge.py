"""Subprocess bridge for running agy CLI, streaming JSON events, and steering running sessions."""

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

    type: str  # "init", "tool", "text_delta", "checkpoint", "result", "turn_complete", "steer", "error", "raw"
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
    """Manages executing the agy CLI, streaming its JSON events, and steering mid-flight sessions."""

    def __init__(
        self,
        agy_bin: str = "/Users/jim/.local/bin/agy",
        default_working_dir: Optional[str] = None,
        timeout_seconds: int = 300,
        system_prompt: Optional[str] = None,
        idle_timeout_seconds: float = 300.0,
        keep_alive: bool = True,
    ) -> None:
        self.agy_bin = agy_bin
        self.default_working_dir = default_working_dir or os.getcwd()
        self.timeout_seconds = timeout_seconds
        self.system_prompt = system_prompt
        self.idle_timeout_seconds = idle_timeout_seconds
        self.keep_alive = keep_alive
        self._current_process: Optional[asyncio.subprocess.Process] = None
        self._is_stopped = False
        self._pending_turns = 0
        self._lock = asyncio.Lock()
        self._steer_history: List[str] = []
        self._idle_task: Optional[asyncio.Task] = None
        self._current_conv_id: Optional[str] = None
        self._current_working_dir: Optional[str] = None
        self._current_model: Optional[str] = None
        self._current_effort: Optional[str] = None

    def is_running(self) -> bool:
        """Check if the agy process is currently running and active."""
        proc = self._current_process
        return proc is not None and proc.returncode is None and not self._is_stopped

    def _cancel_idle_timer(self) -> None:
        """Cancel the idle timeout timer if running."""
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    def _start_idle_timer(self) -> None:
        """Schedule a timer to terminate the idle daemon process after inactivity."""
        self._cancel_idle_timer()
        self._idle_task = asyncio.create_task(self._idle_worker())

    async def _idle_worker(self) -> None:
        """Background worker that shuts down the agy process when idle timeout expires."""
        try:
            await asyncio.sleep(self.idle_timeout_seconds)
            logger.info("Session idle timeout reached (%ss), stopping background agy daemon.", self.idle_timeout_seconds)
            await self.stop()
        except asyncio.CancelledError:
            pass

    def build_command(
        self,
        prompt: Optional[str] = None,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> List[str]:
        """Construct command line arguments for running agy in stream-json mode."""
        cmd = [
            self.agy_bin,
            "--input-format",
            "stream-json",
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

        return cmd

    async def steer(self, message: str) -> bool:
        """Inject a steering message into the active agy session over stdin."""
        msg_str = message.strip()
        if not msg_str:
            return False

        async with self._lock:
            proc = self._current_process
            if not proc or proc.returncode is not None or not proc.stdin or proc.stdin.is_closing():
                logger.warning("Cannot steer: agy process is not active or stdin is closed.")
                return False

            try:
                payload = json.dumps({"event": "user", "message": {"content": msg_str}}) + "\n"
                proc.stdin.write(payload.encode("utf-8"))
                await proc.stdin.drain()
                self._pending_turns += 1
                self._steer_history.append(msg_str)
                logger.info(
                    "Steering message injected to agy process (PID %s, pending turns: %d): %s",
                    proc.pid,
                    self._pending_turns,
                    msg_str[:80],
                )
                return True
            except Exception as e:
                logger.error("Failed to write steering message to agy stdin: %s", e)
                return False

    async def run_streaming(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        working_dir: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AgyEvent]:
        """Run agy CLI with stream-json input/output and yield streaming AgyEvent instances."""
        self._is_stopped = False
        self._steer_history.clear()
        self._cancel_idle_timer()

        effective_dir = working_dir or self.default_working_dir
        can_reuse = (
            self.is_running()
            and self._current_process is not None
            and self._current_process.stdin is not None
            and not self._current_process.stdin.is_closing()
            and (conversation_id is None or self._current_conv_id == conversation_id)
            and self._current_working_dir == effective_dir
            and self._current_model == model
            and self._current_effort == effort
        )

        if can_reuse:
            assert self._current_process is not None
            process = self._current_process
            logger.info("Reusing active warm agy process (PID %s) for new turn", process.pid)
            async with self._lock:
                self._pending_turns = 1
                payload = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
                process.stdin.write(payload.encode("utf-8"))
                await process.stdin.drain()
        else:
            if self.is_running():
                await self.stop()

            cmd = self.build_command(
                prompt=prompt,
                conversation_id=conversation_id,
                model=model,
                effort=effort,
                working_dir=effective_dir,
            )
            logger.info("Executing agy command: %s (cwd=%s)", " ".join(cmd[:6]) + " ...", effective_dir)

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=effective_dir,
                )
                self._current_process = process
                self._current_conv_id = conversation_id
                self._current_working_dir = effective_dir
                self._current_model = model
                self._current_effort = effort
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
            assert process.stdin is not None

            # Determine effective prompt for the turn (always inject embedded system prompt on first turn)
            eff_sys_prompt = system_prompt if system_prompt is not None else self.system_prompt
            is_first_turn = not bool(conversation_id)

            if is_first_turn and eff_sys_prompt and eff_sys_prompt.strip():
                initial_content = f"{eff_sys_prompt.strip()}\n\n---\n\n{prompt}"
            else:
                initial_content = prompt

            # Send initial user prompt turn
            async with self._lock:
                self._pending_turns = 1
                init_payload = json.dumps({"event": "user", "message": {"content": initial_content}}) + "\n"
                process.stdin.write(init_payload.encode("utf-8"))
                await process.stdin.drain()

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
                if not event:
                    continue

                if event.type == "init" and event.conversation_id:
                    self._current_conv_id = event.conversation_id

                if event.type == "result":
                    if event.conversation_id:
                        self._current_conv_id = event.conversation_id

                    async with self._lock:
                        self._pending_turns -= 1
                        has_more_turns = (self._pending_turns > 0)
                        if not self.keep_alive and not has_more_turns and not process.stdin.is_closing():
                            try:
                                process.stdin.close()
                            except Exception:
                                pass

                    if has_more_turns:
                        # Turn complete, but more steered turns are queued
                        yield AgyEvent(
                            type="turn_complete",
                            conversation_id=event.conversation_id,
                            status=event.status,
                            response=event.response,
                            duration_seconds=event.duration_seconds,
                            usage=event.usage,
                            payload=event.payload,
                        )
                        continue
                    else:
                        # Final result for the entire exchange
                        yield event
                        if self.keep_alive:
                            self._start_idle_timer()
                        break
                else:
                    yield event

            if not self.keep_alive:
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
            if not self.keep_alive:
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
        self._cancel_idle_timer()
        self._is_stopped = True
        proc = self._current_process
        if proc and proc.returncode is None:
            logger.info("Stopping running agy subprocess (PID: %s)...", proc.pid)
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
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
        self._current_process = None
        self._current_conv_id = None


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
            elif event.type == "turn_complete":
                print(f"\n[TURN COMPLETE] Response: {event.response}")
            elif event.type == "result":
                print(f"\n[RESULT] Status: {event.status}, Tokens: {event.usage.get('total_tokens')}, Time: {event.duration_seconds}s")
            elif event.type == "error":
                print(f"\n[ERROR] {event.response}")

    asyncio.run(main())
