# Károly Tamás szakdolgozati prototípusa (2026).
"""Local SQLite storage for submitted cases and their reversible mappings.

This database lives under the excluded runtime directory. It contains sensitive
token values in plaintext, so the file and its parent are private to the user.
"""

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sqlite3
from types import MappingProxyType

from gateway.approval import FrozenPayload, Outcome
from gateway.demo_input import PreparedDemo
from gateway.demo_response import DemoResponse, DemoTask
from gateway.operator_config import ModelProfile
from gateway.role_access import Actor, CaseAccess, Risk, Role
from gateway.session_lifecycle import PreparedRequest
from gateway.token_mapping import RequestBinding, TokenizedRequest


class CaseStoreError(RuntimeError):
    """Stored case data cannot be read or verified."""


@dataclass(frozen=True, slots=True)
class StoredCase:
    case_id: str
    demo: PreparedDemo
    mapping: TokenizedRequest
    label: str
    outcome: Outcome | None
    decision_identity: str | None
    decision_role: Role | None
    expires_at_wall: float | None
    result: str | None
    origin: dict | None


def _encode(demo: PreparedDemo, mapping: TokenizedRequest, origin: dict | None = None) -> str:
    payload = demo.payload
    case = payload.case
    if mapping.binding != case.prepared.binding or (
        mapping.source, mapping.request, mapping.tokens
    ) != (case.prepared.source, case.prepared.request, case.prepared.tokens):
        raise CaseStoreError("Case mapping does not match its frozen payload")
    body = {
        "schema": 1,
        "binding": asdict(mapping.binding),
        "source": mapping.source,
        "request": mapping.request,
        "values": dict(mapping._values),
        "owner_identity": case.owner.identity,
        "reviewer_identity": case.reviewer.identity if case.reviewer else None,
        "risk": case.risk.value,
        "reason_code": case.reason_code,
        "version": payload.version,
        "task_template": payload.task_template,
        "policy_version": payload.policy_version,
        "adapter_config_version": payload.adapter_config_version,
        "profile": asdict(payload.profile) if payload.profile else None,
        "external_https": payload.external_https,
        "digest": payload.digest,
        "response": {
            "task": demo.response.task.value,
            "document_token": demo.response.document_token,
        } if demo.response else None,
        "origin": origin,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _decode(case_id: str, raw: str, label: str, outcome: str | None,
            decision_identity: str | None, decision_role: str | None,
            expires_at_wall: float | None, result: str | None) -> StoredCase:
    try:
        data = json.loads(raw)
        if data["schema"] != 1:
            raise ValueError("Unknown case schema")
        binding = RequestBinding(**data["binding"])
        if binding.request_id != case_id:
            raise ValueError("Case ID mismatch")
        values = data["values"]
        if not isinstance(values, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in values.items()
        ):
            raise ValueError("Invalid token mapping")
        mapping = TokenizedRequest(data["source"], data["request"], binding, MappingProxyType(values))
        owner = Actor(data["owner_identity"], binding.session_id, Role.REQUESTER)
        reviewer = (
            Actor(data["reviewer_identity"], "assigned-reviewer", Role.REVIEWER)
            if data["reviewer_identity"] is not None else None
        )
        prepared = PreparedRequest(binding, mapping.source, mapping.request, mapping.tokens)
        case = CaseAccess(owner, reviewer, Risk(data["risk"]), data["reason_code"], prepared)
        profile = ModelProfile(**data["profile"]) if data["profile"] else None
        payload = FrozenPayload(
            case, data["version"], data["task_template"], data["policy_version"],
            data["adapter_config_version"], profile=profile,
            external_https=data["external_https"],
        )
        if payload.digest != data["digest"]:
            raise ValueError("Payload digest mismatch")
        response_data = data["response"]
        response = (
            DemoResponse(DemoTask(response_data["task"]), response_data["document_token"])
            if response_data is not None else None
        )
        return StoredCase(
            case_id, PreparedDemo(payload, response), mapping, label,
            Outcome(outcome) if outcome is not None else None,
            decision_identity, Role(decision_role) if decision_role is not None else None,
            expires_at_wall, result, data.get("origin"),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CaseStoreError(f"Invalid local case record: {case_id}") from exc


class CaseStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDONLY, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    label TEXT NOT NULL,
                    outcome TEXT,
                    decision_identity TEXT,
                    decision_role TEXT,
                    expires_at_wall REAL,
                    result TEXT
                )
            """)

    @contextmanager
    def _db(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                yield db
        except sqlite3.Error as exc:
            raise CaseStoreError("Local case database unavailable") from exc

    def load(self) -> list[StoredCase]:
        with self._db() as db:
            rows = db.execute(
                "SELECT case_id, payload_json, label, outcome, decision_identity, "
                "decision_role, expires_at_wall, result FROM cases ORDER BY rowid"
            ).fetchall()
        return [_decode(*row) for row in rows]

    def add(self, demo: PreparedDemo, mapping: TokenizedRequest, label: str,
            origin: dict | None = None) -> None:
        case_id = demo.payload.case.prepared.binding.request_id
        with self._db() as db:
            db.execute(
                "INSERT INTO cases (case_id, payload_json, label) VALUES (?, ?, ?)",
                (case_id, _encode(demo, mapping, origin), label),
            )

    def set_decision(self, case_id: str, outcome: Outcome, actor: Actor, expires_at_wall: float) -> None:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE cases SET outcome=?, decision_identity=?, decision_role=?, expires_at_wall=? "
                "WHERE case_id=?",
                (outcome.value, actor.identity, actor.role.value, expires_at_wall, case_id),
            )
            if cursor.rowcount != 1:
                raise CaseStoreError("Case record unavailable")

    def set_result(self, case_id: str, result: str) -> None:
        with self._db() as db:
            cursor = db.execute("UPDATE cases SET result=? WHERE case_id=?", (result, case_id))
            if cursor.rowcount != 1:
                raise CaseStoreError("Case record unavailable")
