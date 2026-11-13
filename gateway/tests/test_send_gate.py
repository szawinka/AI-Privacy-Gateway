# Károly Tamás szakdolgozati prototípusa (2026).
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from gateway.approval import DecisionStore, DecisionUnavailable, FrozenPayload, Outcome
from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, Role, RoleAccess
from gateway.send_gate import LocalSendGate, SendBlocked, SQLiteSendAudit
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import CanonicalText, Section


class SendGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        sessions = SessionStore(clock=lambda: self.now)
        self.owner = Actor("owner", sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", sessions.open_session(), Role.REVIEWER)
        prepared = sessions.prepare(
            self.owner.session_id, CanonicalText(Section.SOURCE, "Kezelt tesztszöveg"),
            CanonicalText(Section.REQUEST, "Összegzés"),
        )
        case = CaseAccess(self.owner, self.reviewer, Risk.ORANGE, "DEMO", prepared)
        self.payload = FrozenPayload(case, 1, "summary", "policy-v1", "adapter-v1")
        self.decisions = DecisionStore(RoleAccess(sessions), clock=lambda: self.now)
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / "audit.sqlite3"
        self.calls = []
        self.gate = LocalSendGate(self.decisions, SQLiteSendAudit(self.path), self.adapter)

    def approve(self):
        self.decisions.record(
            self.reviewer, self.payload, viewed_version=self.payload.version,
            viewed_digest=self.payload.digest, outcome=Outcome.APPROVED,
        )

    def adapter(self, body):
        self.calls.append(body)
        return "Helyi demóválasz"

    def send(self):
        return self.gate.send(self.owner, self.payload, self.payload.body)

    def test_audit_commits_before_adapter_and_contains_only_metadata(self):
        self.approve()

        def observe(body):
            with closing(sqlite3.connect(self.path)) as db:
                cursor = db.execute("SELECT * FROM send_reservations")
                self.assertEqual([c[0] for c in cursor.description], [
                    "request_id", "version", "occurred_at", "state", "reason_code",
                ])
                request_id, version, occurred_at, state, reason_code = cursor.fetchone()
                self.assertEqual(request_id, self.payload.case.prepared.binding.request_id)
                self.assertEqual((version, state, reason_code), (
                    "1", "reserved", "LOCAL_SEND_AUTHORIZED",
                ))
                self.assertTrue(occurred_at.endswith("+00:00"))
            return self.adapter(body)

        gate = LocalSendGate(self.decisions, SQLiteSendAudit(self.path), observe)
        self.assertEqual(gate.send(self.owner, self.payload, self.payload.body), "Helyi demóválasz")
        self.assertEqual(self.calls, [self.payload.body])

    def test_two_simultaneous_gates_share_one_reservation(self):
        self.approve()
        start = Barrier(2)

        def attempt():
            gate = LocalSendGate(self.decisions, SQLiteSendAudit(self.path), self.adapter)
            start.wait(timeout=5)
            try:
                return gate.send(self.owner, self.payload, self.payload.body)
            except SendBlocked:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as executor:
            attempts = [executor.submit(attempt) for _ in range(2)]
            self.assertCountEqual([a.result() for a in attempts], ["Helyi demóválasz", "blocked"])
        self.assertEqual(self.calls, [self.payload.body])

    def test_missing_approval_wrong_actor_and_modified_payload_are_blocked(self):
        with self.assertRaises(DecisionUnavailable):
            self.send()
        self.approve()
        with self.assertRaises(AccessDenied):
            self.gate.send(self.reviewer, self.payload, self.payload.body)
        with self.assertRaises(DecisionUnavailable):
            self.gate.send(self.owner, self.payload, self.payload.body + b" ")
        revised = replace(self.payload, version=2, task_template="revised")
        with self.assertRaises(DecisionUnavailable):
            self.gate.send(self.owner, revised, revised.body)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())

    def test_sqlite_write_failure_blocks_adapter(self):
        self.approve()
        gate = LocalSendGate(
            self.decisions, SQLiteSendAudit(self.path / "missing" / "audit.sqlite3"), self.adapter,
        )
        with self.assertRaises(SendBlocked):
            gate.send(self.owner, self.payload, self.payload.body)
        self.assertEqual(self.calls, [])

    def test_adapter_failure_leaves_reservation_and_blocks_retry(self):
        self.approve()

        def fail(body):
            self.calls.append(body)
            raise RuntimeError("Local adapter failed")

        gate = LocalSendGate(self.decisions, SQLiteSendAudit(self.path), fail)
        with self.assertRaises(RuntimeError):
            gate.send(self.owner, self.payload, self.payload.body)
        with self.assertRaises(SendBlocked):
            self.send()
        self.assertEqual(self.calls, [self.payload.body])

    def test_expiry_during_audit_blocks_adapter_after_commit(self):
        self.approve()
        test = self

        class SlowAudit(SQLiteSendAudit):
            def reserve(self, request_id, version):
                super().reserve(request_id, version)
                test.now = 300

        gate = LocalSendGate(self.decisions, SlowAudit(self.path), self.adapter)
        with self.assertRaises(DecisionUnavailable):
            gate.send(self.owner, self.payload, self.payload.body)
        self.assertEqual(self.calls, [])
