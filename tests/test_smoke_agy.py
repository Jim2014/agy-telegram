"""Integration smoke test with real agy CLI execution."""

import os
import shutil
import pytest
from gateway.agy_bridge import AgyBridge
from gateway.config import Config


@pytest.mark.asyncio
async def test_real_agy_smoke():
    cfg = Config.load()
    agy_bin = cfg.get_resolved_agy_bin()
    if not os.path.exists(agy_bin) and not shutil.which("agy"):
        pytest.skip(f"agy binary not found at {agy_bin}, skipping live smoke test")

    bridge = AgyBridge(agy_bin=agy_bin, default_working_dir=cfg.agy_working_dir)
    events = []
    async for event in bridge.run_streaming(prompt="Respond with 'smoke-ok' in one word"):
        events.append(event)

    types = [e.type for e in events]
    assert "init" in types
    # Must reach result or text_delta
    assert "result" in types or "text_delta" in types

    # Verify conversation ID captured in init
    init_event = next(e for e in events if e.type == "init")
    assert init_event.conversation_id is not None
    assert len(init_event.conversation_id) > 0
