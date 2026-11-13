# Károly Tamás szakdolgozati prototípusa (2026).
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gateway.approval import DecisionStore, FrozenPayload, Outcome
from gateway.demo_response import DemoResponse, DemoTask
from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, Role, RoleAccess
from gateway.send_gate import LocalSendGate, SQLiteSendAudit
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import CanonicalText, MaskType, Section, Selection
from gateway.token_mapping import InvalidToken, PersonSelection


class DemoResponseTests(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionStore()
        self.owner = Actor("owner", self.sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", self.sessions.open_session(), Role.REVIEWER)
        self.decisions = DecisionStore(RoleAccess(self.sessions))
        folder = self.enterContext(TemporaryDirectory())
        self.audit = SQLiteSendAudit(Path(folder) / "audit.sqlite3")

    def document(self, session_id):
        source = CanonicalText(Section.SOURCE, "Teszt Luca és Teszt Luca")
        return self.sessions.prepare(
            session_id, source, CanonicalText(Section.REQUEST, "Összegzés"),
            source_selections=tuple(
                PersonSelection(Selection(Section.SOURCE, source.digest, start, end, MaskType.PERSON), ref)
                for start, end, ref in ((0, 10, "R1"), (14, 24, "R2"))
            ),
        )

    def payload(self, prepared, task, risk=Risk.YELLOW):
        case = CaseAccess(self.owner, self.reviewer, risk, "DEMO", prepared)
        payload = FrozenPayload(case, 1, task.value, "policy-v1", "adapter-v1")
        actor = self.owner if risk is Risk.YELLOW else self.reviewer
        self.decisions.record(
            actor, payload, viewed_version=1, viewed_digest=payload.digest, outcome=Outcome.APPROVED
        )
        return payload

    def send(self, demo, payload, adapter=None):
        gate = LocalSendGate(self.decisions, self.audit, adapter or demo.adapter_for(payload))
        return demo.send_and_finish(gate, self.sessions, self.owner, payload, payload.body)

    def test_document_restores_only_configured_referent(self):
        prepared = self.document(self.owner.session_id)
        first, second = prepared.source.split(" és ")
        self.assertNotEqual(first, second)
        payload = self.payload(prepared, DemoTask.DOCUMENT_SUMMARY)
        demo = DemoResponse(DemoTask.DOCUMENT_SUMMARY, first)
        self.assertEqual(
            self.send(demo, payload),
            "Teszt Luca átvette a SYN-17 mintadobozt. Péntekre állapotjelentést ígért.",
        )

    def test_document_blocks_unauthorized_foreign_and_malformed_tokens(self):
        other_session = self.sessions.open_session()
        foreign = self.document(other_session).source.split(" és ")[0]
        for variant in ("other_referent", "other_request", "other_session", "unknown", "malformed"):
            with self.subTest(variant=variant):
                prepared = self.document(self.owner.session_id)
                first, second = prepared.source.split(" és ")
                invalid = {
                    "other_referent": second,
                    "other_request": self.document(self.owner.session_id).source.split(" és ")[0],
                    "other_session": foreign,
                    "unknown": first[:-8] + "999999]]",
                    "malformed": first[:-1],
                }[variant]
                payload = self.payload(prepared, DemoTask.DOCUMENT_SUMMARY)
                demo = DemoResponse(DemoTask.DOCUMENT_SUMMARY, first)
                with self.assertRaises(InvalidToken):
                    self.send(demo, payload, lambda _: f"{first} {invalid}")

    def mail(self):
        prepared = self.sessions.prepare(
            self.owner.session_id, CanonicalText(Section.SOURCE, "Üdv, [SZEMÉLY]"),
            CanonicalText(Section.REQUEST, "Írj rövid választ."),
        )
        return self.payload(prepared, DemoTask.MAIL_DRAFT, Risk.ORANGE)

    def test_mail_returns_local_draft_without_restoration(self):
        payload = self.mail()
        self.assertEqual(
            self.send(DemoResponse(DemoTask.MAIL_DRAFT), payload),
            "Köszönöm a javaslatot! Csütörtök megfelelő. A pontos időpontot később egyeztetjük.",
        )

    def test_mail_rejects_token_markers_and_nontext(self):
        demo = DemoResponse(DemoTask.MAIL_DRAFT)
        for response in ("Válasz [[A:SZEMELY:hibás", b"response"):
            with self.subTest(response=response), self.assertRaises(InvalidToken):
                self.send(demo, self.mail(), lambda _: response)

    def test_owner_session_closure_during_adapter_blocks_result(self):
        def close_session(_):
            self.sessions.close_session(self.owner.session_id)
            return "Helyi válasz."

        with self.assertRaises(AccessDenied):
            self.send(DemoResponse(DemoTask.MAIL_DRAFT), self.mail(), close_session)
