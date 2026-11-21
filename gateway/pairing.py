"""DM pairing store and CLI approval tool for deny-by-default Telegram access control."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PairingManager:
    """Manages pending DM pairing codes and approved chat IDs with JSON persistence."""

    def __init__(self, pairing_file: Optional[str | Path] = None) -> None:
        self.pairing_file = Path(pairing_file) if pairing_file else Path("runtime/pairing.json")
        self._approved: Dict[str, Dict[str, Any]] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Load pairing state from JSON file if present."""
        if not self.pairing_file.is_file():
            self._approved = {}
            self._pending = {}
            return
        try:
            data = json.loads(self.pairing_file.read_text(encoding="utf-8"))
            self._approved = data.get("approved", {}) if isinstance(data.get("approved"), dict) else {}
            self._pending = data.get("pending", {}) if isinstance(data.get("pending"), dict) else {}
            self._purge_expired_pending()
        except Exception:
            self._approved = {}
            self._pending = {}

    def save(self) -> None:
        """Persist pairing state atomically with restricted 0600 permissions."""
        self.pairing_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "approved": self._approved,
            "pending": self._pending,
        }
        tmp_path = self.pairing_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.pairing_file)
        try:
            os.chmod(self.pairing_file, 0o600)
        except OSError:
            pass

    def _purge_expired_pending(self) -> None:
        """Drop expired pending pairing requests."""
        now = time.time()
        expired = [c for c, rec in self._pending.items() if float(rec.get("expires_at", 0)) <= now]
        for c in expired:
            self._pending.pop(c, None)

    def is_approved(self, chat_id: int) -> bool:
        """Return True if chat_id has been approved via DM pairing."""
        return str(chat_id) in self._approved

    def create_or_get_pending(
        self,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        ttl_seconds: int = 3600,
    ) -> Tuple[str, bool]:
        """
        Return (code, is_new) for an unauthorized chat_id.
        Reuses an active unexpired code for the same chat_id to prevent code flooding.
        """
        self._purge_expired_pending()
        now = time.time()

        for code, rec in self._pending.items():
            if int(rec.get("chat_id", -1)) == int(chat_id):
                return code, False

        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        while code in self._pending:
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))

        # Cap pending queue to 50 entries
        if len(self._pending) >= 50:
            oldest = sorted(self._pending.items(), key=lambda kv: float(kv[1].get("created_at", 0)))
            for old_code, _ in oldest[: len(self._pending) - 49]:
                self._pending.pop(old_code, None)

        self._pending[code] = {
            "code": code,
            "chat_id": int(chat_id),
            "username": username,
            "first_name": first_name,
            "created_at": now,
            "expires_at": now + ttl_seconds,
        }
        self.save()
        return code, True

    def approve_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Approve a pending pairing code, authorize the chat_id, and return its record."""
        self._purge_expired_pending()
        norm = (code or "").strip().upper()
        rec = self._pending.pop(norm, None)
        if not rec:
            return None

        cid = int(rec["chat_id"])
        approved_rec = {
            "chat_id": cid,
            "username": rec.get("username", ""),
            "first_name": rec.get("first_name", ""),
            "approved_at": time.time(),
            "code": norm,
        }
        self._approved[str(cid)] = approved_rec
        self.save()
        return approved_rec

    def reject_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Dismiss a pending pairing code without approving."""
        self._purge_expired_pending()
        norm = (code or "").strip().upper()
        rec = self._pending.pop(norm, None)
        if rec:
            self.save()
        return rec

    def revoke_chat(self, chat_id: int) -> bool:
        """Revoke pairing authorization for chat_id."""
        removed = self._approved.pop(str(chat_id), None) is not None
        if removed:
            self.save()
        return removed

    def list_entries(self) -> Dict[str, Any]:
        """Return all approved and unexpired pending pairing entries."""
        self._purge_expired_pending()
        return {
            "approved": list(self._approved.values()),
            "pending": list(self._pending.values()),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage agy-telegram DM pairing approvals")
    parser.add_argument("--file", default="runtime/pairing.json", help="Path to pairing.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List approved and pending pairing requests")
    p_app = sub.add_parser("approve", help="Approve a pending pairing code")
    p_app.add_argument("code", help="8-character pairing code")
    p_rev = sub.add_parser("revoke", help="Revoke an approved chat ID")
    p_rev.add_argument("chat_id", type=int, help="Numeric Telegram chat ID")
    args = parser.parse_args()

    mgr = PairingManager(pairing_file=args.file)
    if args.action == "list":
        entries = mgr.list_entries()
        print(f"Approved chats ({len(entries['approved'])}):")
        for a in entries["approved"]:
            print(f"  • {a['chat_id']} ({a.get('first_name', '')} @{a.get('username', '')})")
        print(f"Pending requests ({len(entries['pending'])}):")
        for p in entries["pending"]:
            print(f"  • [{p['code']}] chat_id={p['chat_id']} ({p.get('first_name', '')} @{p.get('username', '')})")
    elif args.action == "approve":
        rec = mgr.approve_code(args.code)
        if rec:
            print(f"✅ Approved chat_id {rec['chat_id']} (code {rec['code']}).")
        else:
            print(f"❌ Pairing code '{args.code}' not found or expired.")
            sys.exit(1)
    elif args.action == "revoke":
        if mgr.revoke_chat(args.chat_id):
            print(f"🛑 Revoked chat_id {args.chat_id}.")
        else:
            print(f"ℹ️ chat_id {args.chat_id} was not in approved list.")


if __name__ == "__main__":
    main()
