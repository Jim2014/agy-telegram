"""Unit tests for gateway.pairing DM pairing store and CLI."""

import subprocess
import sys

from gateway.pairing import PairingManager


def test_pairing_lifecycle(tmp_path):
    pfile = tmp_path / "pairing.json"
    mgr = PairingManager(pairing_file=pfile)
    assert mgr.is_approved(777) is False

    # Creating pending reuses the same code for duplicate attempts
    code1, is_new1 = mgr.create_or_get_pending(777, username="bob", first_name="Bob")
    code2, is_new2 = mgr.create_or_get_pending(777, username="bob", first_name="Bob")
    assert len(code1) == 8
    assert is_new1 is True
    assert is_new2 is False
    assert code1 == code2

    entries = mgr.list_entries()
    assert len(entries["pending"]) == 1
    assert len(entries["approved"]) == 0

    # Approve and verify persistence + 0600 permissions
    rec = mgr.approve_code(code1.lower())
    assert rec is not None
    assert rec["chat_id"] == 777
    assert (pfile.stat().st_mode & 0o777) == 0o600

    mgr2 = PairingManager(pairing_file=pfile)
    assert mgr2.is_approved(777) is True
    assert len(mgr2.list_entries()["pending"]) == 0

    # Revoke
    assert mgr2.revoke_chat(777) is True
    assert mgr2.is_approved(777) is False


def test_pairing_reject_and_expiry(tmp_path):
    pfile = tmp_path / "pairing.json"
    mgr = PairingManager(pairing_file=pfile)

    code, _ = mgr.create_or_get_pending(888, ttl_seconds=-1)
    assert mgr.approve_code(code) is None  # Expired immediately

    code2, is_new = mgr.create_or_get_pending(889, ttl_seconds=600)
    assert is_new is True
    rej = mgr.reject_code(code2)
    assert rej is not None
    assert mgr.is_approved(889) is False
    assert mgr.approve_code(code2) is None


def test_pairing_cli(tmp_path):
    pfile = tmp_path / "cli_pairing.json"
    mgr = PairingManager(pairing_file=pfile)
    code, _ = mgr.create_or_get_pending(555, username="cliuser", first_name="CLI")

    res_list = subprocess.run(
        [sys.executable, "-m", "gateway.pairing", "--file", str(pfile), "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert code in res_list.stdout

    res_app = subprocess.run(
        [sys.executable, "-m", "gateway.pairing", "--file", str(pfile), "approve", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Approved chat_id 555" in res_app.stdout
    assert PairingManager(pairing_file=pfile).is_approved(555) is True
