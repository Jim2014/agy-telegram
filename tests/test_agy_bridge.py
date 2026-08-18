"""Unit tests for gateway.agy_bridge."""

import asyncio
import json
import pytest
from gateway.agy_bridge import AgyBridge, AgyEvent


def test_build_command():
    bridge = AgyBridge(agy_bin="/usr/bin/agy", default_working_dir="/workspace")

    # Fresh session
    cmd1 = bridge.build_command(prompt="hello")
    assert cmd1 == [
        "/usr/bin/agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--new-project",
        "--add-dir",
        "/workspace",
    ]

    # Resume session with options
    cmd2 = bridge.build_command(
        prompt="continue",
        conversation_id="conv-123",
        model="gemini-2.5-pro",
        effort="high",
        working_dir="/custom/dir",
    )
    assert "--conversation" in cmd2
    assert "conv-123" in cmd2
    assert "--model" in cmd2
    assert "gemini-2.5-pro" in cmd2
    assert "--effort" in cmd2
    assert "high" in cmd2
    assert "--add-dir" in cmd2
    assert "/custom/dir" in cmd2


def test_parse_line():
    bridge = AgyBridge()

    # Test init line
    init_line = json.dumps({"event": "init", "conversation_id": "abc-123", "init": {"cwd": "/tmp"}})
    ev_init = bridge._parse_line(init_line)
    assert ev_init.type == "init"
    assert ev_init.conversation_id == "abc-123"

    # Test tool line
    tool_line = json.dumps({
        "event": "step_update",
        "step_update": {
            "conversation_id": "abc-123",
            "step_index": 2,
            "step_type": "tool",
            "tool_name": "list_dir",
            "state": "ACTIVE",
            "tool_info": {"parameters": {"DirectoryPath": "/tmp"}},
        },
    })
    ev_tool = bridge._parse_line(tool_line)
    assert ev_tool.type == "tool"
    assert ev_tool.tool_name == "list_dir"
    assert ev_tool.tool_state == "ACTIVE"

    # Test text delta line
    delta_line = json.dumps({
        "event": "step_update",
        "step_update": {
            "conversation_id": "abc-123",
            "step_type": "agent_response",
            "text_delta": "chunk1",
        },
    })
    ev_delta = bridge._parse_line(delta_line)
    assert ev_delta.type == "text_delta"
    assert ev_delta.text_delta == "chunk1"

    # Test result line
    result_line = json.dumps({
        "event": "result",
        "result": {
            "conversation_id": "abc-123",
            "status": "SUCCESS",
            "response": "Final text output",
            "duration_seconds": 1.25,
            "usage": {"total_tokens": 1200},
        },
    })
    ev_res = bridge._parse_line(result_line)
    assert ev_res.type == "result"
    assert ev_res.response == "Final text output"
    assert ev_res.duration_seconds == 1.25
    assert ev_res.usage == {"total_tokens": 1200}

    # Test invalid json line
    ev_raw = bridge._parse_line("non-json output string")
    assert ev_raw.type == "raw"
    assert ev_raw.raw_text == "non-json output string"


@pytest.mark.asyncio
async def test_mocked_streaming(monkeypatch):
    """Test streaming execution with a mock subprocess emitting JSON lines."""
    bridge = AgyBridge()

    mock_lines = [
        b'{"event":"init","conversation_id":"mock-conv-1"}\n',
        b'{"event":"step_update","step_update":{"step_type":"tool","tool_name":"run_command","state":"ACTIVE"}}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Hello"}}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":" World"}}\n',
        b'{"event":"result","result":{"conversation_id":"mock-conv-1","status":"SUCCESS","response":"Hello World","duration_seconds":0.5,"usage":{"total_tokens":100}}}\n',
    ]

    class MockStdin:
        def __init__(self):
            self.written = []
            self.closed = False

        def write(self, data):
            self.written.append(data)

        async def drain(self):
            pass

        def close(self):
            self.closed = True

        def is_closing(self):
            return self.closed

    class MockStream:
        def __init__(self, lines):
            self.lines = list(lines)

        async def readline(self):
            if self.lines:
                return self.lines.pop(0)
            return b""

        async def read(self):
            return b""

    class MockProcess:
        def __init__(self):
            self.stdin = MockStdin()
            self.stdout = MockStream(mock_lines)
            self.stderr = MockStream([])
            self.returncode = None
            self.pid = 12345

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    async def mock_subprocess_exec(*args, **kwargs):
        return MockProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_subprocess_exec)

    events = []
    async for ev in bridge.run_streaming(prompt="test"):
        events.append(ev)

    assert len(events) == 5
    assert events[0].type == "init"
    assert events[0].conversation_id == "mock-conv-1"
    assert events[1].type == "tool"
    assert events[2].type == "text_delta"
    assert events[2].text_delta == "Hello"
    assert events[3].type == "text_delta"
    assert events[3].text_delta == " World"
    assert events[4].type == "result"
    assert events[4].response == "Hello World"


@pytest.mark.asyncio
async def test_steer_method(monkeypatch):
    """Test steer method writes properly formatted JSON to subprocess stdin."""
    bridge = AgyBridge()

    # When no process is active
    assert await bridge.steer("test steering") is False

    class MockStdin:
        def __init__(self):
            self.written = []
            self.closed = False

        def write(self, data):
            self.written.append(data)

        async def drain(self):
            pass

        def close(self):
            self.closed = True

        def is_closing(self):
            return self.closed

    class MockProcess:
        def __init__(self):
            self.stdin = MockStdin()
            self.returncode = None
            self.pid = 9999

    proc = MockProcess()
    bridge._current_process = proc

    res = await bridge.steer("Please focus on Python")
    assert res is True
    assert bridge._pending_turns == 1
    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    assert payload == {"type": "user", "message": {"content": "Please focus on Python"}}
