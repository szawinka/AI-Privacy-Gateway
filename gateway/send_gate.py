# Károly Tamás szakdolgozati prototípusa (2026).
"""One-time admission to the local adapter with a content-free SQLite audit."""

from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from gateway.approval import DecisionStore, FrozenPayload
from gateway.role_access import Actor


class SendBlocked(PermissionError):
    """A durable reservation could not be made."""


class SQLiteSendAudit:
    def __init__(self, path: Path, *, reason_code: str = "LOCAL_SEND_AUTHORIZED"):
        self._path = path
        self._reason_code = reason_code

    def reserved_cases(self) -> set[tuple[str, str]]:
        if not self._path.is_file():
            return set()
        with closing(sqlite3.connect(self._path)) as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'send_reservations'"
            ).fetchone():
                return set()
            return set(db.execute("SELECT request_id, version FROM send_reservations"))

    def reserve(self, request_id: str, version: int) -> None:
        """Commit the audit event and claim the request version in one transaction."""
        try:
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS send_reservations (
                        request_id TEXT NOT NULL,
                        version TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        state TEXT NOT NULL,
                        reason_code TEXT NOT NULL,
                        PRIMARY KEY (request_id, version)
                    )
                    """
                )
                db.execute(
                    "INSERT INTO send_reservations VALUES (?, ?, ?, ?, ?)",
                    (
                        request_id, str(version), datetime.now(UTC).isoformat(),
                        "reserved", self._reason_code,
                    ),
                )
        except sqlite3.IntegrityError:
            raise SendBlocked("Request version already reserved") from None
        except sqlite3.Error:
            raise SendBlocked("Send audit unavailable") from None


class LocalSendGate[AdapterResult]:
    def __init__(
        self, decisions: DecisionStore, audit: SQLiteSendAudit, adapter: Callable[[bytes], AdapterResult]
    ):
        self._decisions = decisions
        self._audit = audit
        self._adapter = adapter

    def send(self, requester: Actor, payload: FrozenPayload, body: bytes) -> AdapterResult:
        """Admit the exact server-selected current payload to a trusted local adapter."""
        self._decisions.require_approval(requester, payload, body)
        self._audit.reserve(payload.case.prepared.binding.request_id, payload.version)
        # SQLite can wait for another writer. Recheck before admitting the adapter.
        self._decisions.require_approval(requester, payload, body)
        return self._adapter(body)
