"""Tests for restart.sh CLI argument validation and safe flag handling."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESTART_SCRIPT = REPO_ROOT / "restart.sh"


def test_restart_script_help():
    """Verify --help prints usage and exits cleanly without restarting."""
    res = subprocess.run(
        ["bash", str(RESTART_SCRIPT), "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0
    assert "Usage: ./restart.sh" in res.stdout
    assert "--status" in res.stdout


def test_restart_script_rejects_unknown_flag():
    """Verify unknown flags fail fast instead of falling through to a gateway restart."""
    res = subprocess.run(
        ["bash", str(RESTART_SCRIPT), "--definitely-invalid-flag"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 1
    assert "Unknown command or flag" in res.stdout
    assert "Restarting gateway" not in res.stdout


def test_restart_script_status_alias():
    """Verify --status behaves identically to status without triggering a restart."""
    res = subprocess.run(
        ["bash", str(RESTART_SCRIPT), "--status"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0
    assert "Gateway:" in res.stdout
    assert "Restarting gateway" not in res.stdout
