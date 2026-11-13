# Károly Tamás szakdolgozati prototípusa (2026).
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gateway.approval import DecisionUnavailable, Outcome
from gateway.demo_flow import DemoFlow
from gateway.demo_response import DemoResponse, DemoTask
from gateway.role_access import AccessDenied, Actor, Role
from gateway.send_gate import SendBlocked
from gateway.session_lifecycle import SessionStore


class DemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sessions = SessionStore(clock=lambda: self.now)
        self.owner = Actor("requester", self.sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", self.sessions.open_session(), Role.REVIEWER)
        folder = self.enterContext(TemporaryDirectory())
        self.audit = Path(folder) / "audit.sqlite3"
        self.flow = DemoFlow(self.sessions, self.audit, clock=lambda: self.now)

    def decide(self, actor, case_id, outcome=Outcome.APPROVED):
        preview = self.flow.preview(actor, case_id)
        self.flow.decide(actor, case_id, version=preview.version, digest=preview.digest, outcome=outcome)
        return preview

    def test_both_flows_reach_real_local_adapter_with_frozen_bytes(self):
        actual = []
        adapter_for = DemoResponse.adapter_for

        def instrument(response, payload):
            adapter = adapter_for(response, payload)

            def observe(body):
                actual.append(memoryview(body).tobytes())
                return adapter(body)

            return observe

        with patch.object(DemoResponse, "adapter_for", instrument):
            doc = self.flow.start(DemoTask.DOCUMENT_SUMMARY, self.owner)
            doc_preview = self.decide(self.owner, doc)
            self.assertEqual(
                self.flow.send(self.owner, doc),
                "Teszt Luca átvette a SYN-17 mintadobozt. Péntekre állapotjelentést ígért.",
            )
            with self.assertRaises(SendBlocked):
                self.flow.send(self.owner, doc)

            mail = self.flow.start(DemoTask.MAIL_DRAFT, self.owner, reviewer=self.reviewer)
            mail_preview = self.decide(self.reviewer, mail)
            with self.assertRaises(AccessDenied):
                self.flow.send(self.reviewer, mail)
            self.assertEqual(
                self.flow.send(self.owner, mail),
                "Köszönöm a javaslatot! Csütörtök megfelelő. A pontos időpontot később egyeztetjük.",
            )

        self.assertEqual(actual, [doc_preview.body, mail_preview.body])
        with closing(sqlite3.connect(self.audit)) as db:
            reservations = db.execute("SELECT request_id FROM send_reservations").fetchall()
        self.assertCountEqual(reservations, [(doc,), (mail,)])

    def test_wrong_actor_stale_preview_and_returned_decision_block_send(self):
        case_id = self.flow.start(DemoTask.MAIL_DRAFT, self.owner, reviewer=self.reviewer)
        preview = self.flow.preview(self.reviewer, case_id)
        with self.assertRaises(AccessDenied):
            self.decide(self.owner, case_id)
        with self.assertRaises(DecisionUnavailable):
            self.flow.decide(
                self.reviewer, case_id, version=preview.version, digest="stale",
                outcome=Outcome.APPROVED,
            )
        self.decide(self.reviewer, case_id, Outcome.RETURNED)
        with self.assertRaises(DecisionUnavailable):
            self.flow.send(self.owner, case_id)
        self.assertFalse(self.audit.exists())

    def test_approval_expiry_blocks_send_while_case_remains(self):
        case_id = self.flow.start(DemoTask.DOCUMENT_SUMMARY, self.owner)
        self.decide(self.owner, case_id)
        self.now = 300
        with self.assertRaises(DecisionUnavailable):
            self.flow.send(self.owner, case_id)
        self.sessions.close_session(self.owner.session_id)
        self.assertEqual(self.flow.progress(self.owner, case_id).status, "Jóváhagyás lejárt")
        with self.assertRaises(DecisionUnavailable):
            self.flow.send(self.owner, case_id)
        self.assertFalse(self.audit.exists())
