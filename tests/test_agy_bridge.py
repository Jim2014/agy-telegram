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


def test_build_command_omits_effort_for_orchestrator_and_pinned_models():
    from gateway.agy_bridge import model_supports_effort

    bridge = AgyBridge(agy_bin="/usr/bin/agy", default_working_dir="/workspace")

    # Orchestrator and effort-suffixed models must NOT pass --effort
    for model_id in (
        "gemini-pro-orchestrator-neon",
        "gemini-pro-orchestrator-g1-pro",
        "gemini-4-neon-xhigh",
        "claude-opus-5-5-low",
        "gpt-oss-120b-medium",
    ):
        assert model_supports_effort(model_id) is False
        cmd = bridge.build_command(prompt="hi", model=model_id, effort="high")
        assert "--model" in cmd and model_id in cmd
        assert "--effort" not in cmd

    # Standard configurable models DO pass --effort
    for model_id in ("gemini-4-neon", "gemini-4-hydrogen", "gemini-3.8-flash", None):
        assert model_supports_effort(model_id) is True
        cmd = bridge.build_command(prompt="hi", model=model_id, effort="high")
        assert "--effort" in cmd and "high" in cmd


def test_parse_line_surfaces_result_error():
    bridge = AgyBridge()
    err_line = json.dumps({
        "event": "result",
        "result": {
            "conversation_id": "",
            "status": "ERROR",
            "response": "",
            "error": 'invalid model selection: --effort is not supported for model "gemini-pro-orchestrator-neon"',
        },
    })
    ev = bridge._parse_line(err_line)
    assert ev.type == "result"
    assert ev.status == "ERROR"
    assert "invalid model selection" in ev.response
    assert "gemini-pro-orchestrator-neon" in ev.response


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
    assert payload == {"event": "user", "message": {"content": "Please focus on Python"}}


@pytest.mark.asyncio
async def test_system_prompt_injection_first_turn(monkeypatch, tmp_path):
    """Test that system prompt is injected on the first turn when no declarative rules exist."""
    bridge = AgyBridge(system_prompt="[SYSTEM] Follow markdown conventions.", default_working_dir=str(tmp_path))

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
            self.stdout = MockStream([
                b'{"event":"init","conversation_id":"conv-new-1"}\n',
                b'{"event":"result","result":{"status":"SUCCESS","response":"Done"}}\n',
            ])
            self.stderr = MockStream([])
            self.returncode = 0
            self.pid = 11111

        async def wait(self):
            return 0

    proc = MockProcess()
    async def mock_exec(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    events = []
    async for ev in bridge.run_streaming(prompt="Please write a summary", conversation_id=None, working_dir=str(tmp_path)):
        events.append(ev)

    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    expected_content = "[SYSTEM] Follow markdown conventions.\n\n---\n\nPlease write a summary"
    assert payload == {"event": "user", "message": {"content": expected_content}}


@pytest.mark.asyncio
async def test_system_prompt_subsequent_turn_injected(monkeypatch):
    """Test that system prompt is consistently injected on subsequent turns when configured."""
    bridge = AgyBridge(system_prompt="[SYSTEM] Follow markdown conventions.")

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
            self.stdout = MockStream([
                b'{"event":"result","result":{"status":"SUCCESS","response":"Continued"}}\n',
            ])
            self.stderr = MockStream([])
            self.returncode = 0
            self.pid = 22222

        async def wait(self):
            return 0

    proc = MockProcess()
    async def mock_exec(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    events = []
    async for ev in bridge.run_streaming(prompt="Next turn question", conversation_id="conv-existing-99"):
        events.append(ev)

    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    # Should contain system prompt prepended
    assert payload == {"event": "user", "message": {"content": "[SYSTEM] Follow markdown conventions.\n\n---\n\nNext turn question"}}


@pytest.mark.asyncio
async def test_system_prompt_empty_or_none(monkeypatch):
    """Test that empty or None system prompt does not add separator or prefix."""
    bridge = AgyBridge(system_prompt=None)

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
            self.stdout = MockStream([
                b'{"event":"result","result":{"status":"SUCCESS","response":"Ok"}}\n',
            ])
            self.stderr = MockStream([])
            self.returncode = 0
            self.pid = 33333

        async def wait(self):
            return 0

    proc = MockProcess()
    async def mock_exec(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    async for _ in bridge.run_streaming(prompt="Raw prompt", conversation_id=None, system_prompt=""):
        pass

    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    assert payload == {"event": "user", "message": {"content": "Raw prompt"}}


def test_is_running():
    bridge = AgyBridge()
    assert bridge.is_running() is False

    class DummyProc:
        returncode = None

    bridge._current_process = DummyProc()
    assert bridge.is_running() is True

    bridge._is_stopped = True
    assert bridge.is_running() is False


@pytest.mark.asyncio
async def test_declarative_rules_clean_prompt(monkeypatch, tmp_path):
    """Test that if workspace has .gemini/rules/telegram-output.md, prompt remains clean without prepended system prompt."""
    rules_dir = tmp_path / ".gemini" / "rules"
    rules_dir.mkdir(parents=True)
    rule_file = rules_dir / "telegram-output.md"
    rule_file.write_text("# Telegram Output Rules")

    bridge = AgyBridge(system_prompt="[SYSTEM] Long system prompt text.")

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
            self.stdout = MockStream([
                b'{"event":"result","result":{"status":"SUCCESS","response":"Clean response"}}\n',
            ])
            self.stderr = MockStream([])
            self.returncode = 0
            self.pid = 44444

        async def wait(self):
            return 0

    proc = MockProcess()
    async def mock_exec(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    # First turn should prepend embedded system prompt cleanly
    async for _ in bridge.run_streaming(prompt="Clean question", conversation_id=None, working_dir=str(tmp_path)):
        pass

    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    assert payload == {"event": "user", "message": {"content": "[SYSTEM] Long system prompt text.\n\n---\n\nClean question"}}



@pytest.mark.asyncio
async def test_multiturn_process_reuse(monkeypatch, tmp_path):
    """Test that consecutive prompt turns reuse the active warm subprocess without respawning."""
    bridge = AgyBridge(keep_alive=True, default_working_dir=str(tmp_path))

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
        def __init__(self):
            self.lines = []

        async def readline(self):
            if self.lines:
                return self.lines.pop(0)
            return b""

        async def read(self):
            return b""

    class MockProcess:
        def __init__(self):
            self.stdin = MockStdin()
            self.stdout = MockStream()
            self.stderr = MockStream()
            self.returncode = None
            self.pid = 55555

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    proc = MockProcess()
    spawn_count = 0

    async def mock_exec(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    # Turn 1
    proc.stdout.lines = [
        b'{"event":"init","conversation_id":"conv-reuse-1"}\n',
        b'{"event":"result","result":{"conversation_id":"conv-reuse-1","status":"SUCCESS","response":"Turn 1 done"}}\n',
    ]
    t1_events = []
    async for ev in bridge.run_streaming(prompt="Turn 1 prompt", conversation_id=None, working_dir=str(tmp_path)):
        t1_events.append(ev)

    assert spawn_count == 1
    assert bridge.is_running() is True
    assert bridge._current_conv_id == "conv-reuse-1"
    assert len(proc.stdin.written) == 1

    # Turn 2 - with matching conversation_id & working_dir -> should REUSE process without mock_exec call!
    proc.stdout.lines = [
        b'{"event":"result","result":{"conversation_id":"conv-reuse-1","status":"SUCCESS","response":"Turn 2 done"}}\n',
    ]
    t2_events = []
    async for ev in bridge.run_streaming(prompt="Turn 2 prompt", conversation_id="conv-reuse-1", working_dir=str(tmp_path)):
        t2_events.append(ev)

    assert spawn_count == 1  # No second spawn! Process was reused!
    assert len(proc.stdin.written) == 2
    payload2 = json.loads(proc.stdin.written[1].decode())
    assert payload2 == {"event": "user", "message": {"content": "Turn 2 prompt"}}

    # Stop bridge cleans up
    await bridge.stop()
    assert bridge.is_running() is False
    assert bridge._current_process is None


@pytest.mark.asyncio
async def test_switch_project_first_message_yields_events(monkeypatch, tmp_path):
    """Test that switching project working_dir stops old warm process and properly yields all events for the new project's first message."""
    bridge = AgyBridge(keep_alive=True)

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
        def __init__(self, lines=None):
            self.lines = list(lines or [])

        async def readline(self):
            if self.lines:
                return self.lines.pop(0)
            return b""

        async def read(self):
            return b""

    class MockProcess:
        def __init__(self, name="proc"):
            self.name = name
            self.stdin = MockStdin()
            self.stdout = MockStream()
            self.stderr = MockStream()
            self.returncode = None
            self.pid = 11111

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    proc1 = MockProcess(name="proc1")
    proc1.pid = 11111
    proc2 = MockProcess(name="proc2")
    proc2.pid = 22222

    processes = [proc1, proc2]

    async def mock_exec(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)

    dir1 = str(tmp_path / "project1")
    dir2 = str(tmp_path / "project2")

    # Turn 1 on Project 1
    proc1.stdout.lines = [
        b'{"event":"init","conversation_id":"conv-proj1"}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Project 1 response"}}\n',
        b'{"event":"result","result":{"conversation_id":"conv-proj1","status":"SUCCESS","response":"Project 1 response"}}\n',
    ]
    p1_events = []
    async for ev in bridge.run_streaming(prompt="Proj 1 prompt", conversation_id=None, working_dir=dir1):
        p1_events.append(ev)

    assert len(p1_events) == 3
    assert p1_events[-1].response == "Project 1 response"
    assert bridge.is_running() is True
    assert bridge._current_working_dir == dir1

    # Now switch to Project 2! This should stop proc1 and yield all events from proc2 on first message!
    proc2.stdout.lines = [
        b'{"event":"init","conversation_id":"conv-proj2"}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Project 2 first response"}}\n',
        b'{"event":"result","result":{"conversation_id":"conv-proj2","status":"SUCCESS","response":"Project 2 first response"}}\n',
    ]
    p2_events = []
    async for ev in bridge.run_streaming(prompt="Proj 2 first prompt", conversation_id=None, working_dir=dir2):
        p2_events.append(ev)

    # Proc 1 must have been terminated and closed
    assert proc1.returncode == 0
    assert proc1.stdin.closed is True

    # Proc 2 must have executed and yielded all events
    assert len(p2_events) == 3
    assert p2_events[0].type == "init"
    assert p2_events[0].conversation_id == "conv-proj2"
    assert p2_events[1].type == "text_delta"
    assert p2_events[1].text_delta == "Project 2 first response"
    assert p2_events[2].type == "result"
    assert p2_events[2].response == "Project 2 first response"

    await bridge.stop()




