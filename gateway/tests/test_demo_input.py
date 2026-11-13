# Károly Tamás szakdolgozati prototípusa (2026).
from hashlib import sha256
import json
import re
import unittest
from unittest.mock import patch

from gateway.approval import DecisionStore, Outcome
from gateway.demo_input import prepare_demo
from gateway.demo_response import DemoTask
from gateway.role_access import AccessDenied, Actor, Role, RoleAccess
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import InvalidSelection


class DemoInputTests(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionStore()
        self.access = RoleAccess(self.sessions)
        self.owner = Actor("requester", self.sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", self.sessions.open_session(), Role.REVIEWER)

    def test_document_preserves_distinct_referents_and_freezes_payload(self):
        demo = prepare_demo(self.sessions, DemoTask.DOCUMENT_SUMMARY, self.owner)
        preview = demo.preview(self.access, self.owner)
        decoded = json.loads(preview.body)
        tokens = re.findall(r"\[\[A:SZEMELY:[^\]]+\]\]", decoded["source"])
        first, repeated, other = tokens
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, other)
        self.assertEqual(demo.response.document_token, first)
        self.assertNotIn("Teszt Luca", decoded["source"])
        self.assertEqual(preview.body, demo.payload.body)
        self.assertEqual(preview.digest, sha256(preview.body).hexdigest())

    def test_mail_masks_both_sections_and_reviewer_can_decide_from_preview(self):
        demo = prepare_demo(self.sessions, DemoTask.MAIL_DRAFT, self.owner, reviewer=self.reviewer)
        preview = demo.preview(self.access, self.reviewer)
        decoded = json.loads(preview.body)
        self.assertIn("A tesztkapcsolat [E-MAIL]. Üdv, [SZEMÉLY]", decoded["source"])
        self.assertIn("választ [SZEMÉLY]:", decoded["request"])
        for value in ("Teszt Mira", "demo42@example.invalid"):
            self.assertNotIn(value, preview.body.decode("utf-8"))
        self.assertEqual(demo.response.allowed_tokens(demo.payload.case.prepared), frozenset())
        decisions = DecisionStore(self.access)
        for actor in (self.owner, self.reviewer):
            with self.subTest(actor=actor.role):
                if actor is self.owner:
                    with self.assertRaises(AccessDenied):
                        decisions.record(
                            actor, demo.payload, viewed_version=preview.version,
                            viewed_digest=preview.digest, outcome=Outcome.APPROVED,
                        )
                else:
                    decisions.record(
                        actor, demo.payload, viewed_version=preview.version,
                        viewed_digest=preview.digest, outcome=Outcome.APPROVED,
                    )
        decisions.require_approval(self.owner, demo.payload, preview.body)

    def test_unassigned_reviewer_cannot_see_mail_preview(self):
        with self.assertRaises(AccessDenied):
            prepare_demo(self.sessions, DemoTask.MAIL_DRAFT, self.owner)
        demo = prepare_demo(self.sessions, DemoTask.MAIL_DRAFT, self.owner, reviewer=self.reviewer)
        stranger = Actor("other", self.sessions.open_session(), Role.REVIEWER)
        with self.assertRaises(AccessDenied):
            demo.preview(self.access, stranger)

    def test_remaining_fixture_value_prevents_preview(self):
        with patch("gateway.session_lifecycle.mask_request", return_value=("Teszt Mira", "Kérés")):
            with self.assertRaises(InvalidSelection):
                prepare_demo(self.sessions, DemoTask.MAIL_DRAFT, self.owner, reviewer=self.reviewer)
