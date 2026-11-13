# Károly Tamás szakdolgozati prototípusa (2026).
from dataclasses import fields, replace
import unittest

from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, Role, RoleAccess
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import CanonicalText, MaskType, Section, Selection
from gateway.token_mapping import PersonSelection


class RoleAccessTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sessions = SessionStore(clock=lambda: self.now)
        self.access = RoleAccess(self.sessions)
        self.owner = Actor("owner", self.sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", self.sessions.open_session(), Role.REVIEWER)
        source = CanonicalText(Section.SOURCE, "Teszt Luca")
        request = CanonicalText(Section.REQUEST, "Foglald össze.")
        prepared = self.sessions.prepare(
            self.owner.session_id, source, request,
            source_selections=(
                PersonSelection(
                    Selection(Section.SOURCE, source.digest, 0, len(source.text), MaskType.PERSON),
                    "R1",
                ),
            ),
        )
        self.case = CaseAccess(self.owner, self.reviewer, Risk.ORANGE, "DEMO_ORANGE", prepared)

    def test_owner_access_and_reviewer_only_sees_projection(self):
        self.access.require_owner(self.owner, self.case)
        preview = self.access.reviewer_preview(self.reviewer, self.case)
        self.assertEqual(preview.source, self.case.prepared.source)
        self.assertNotIn("Teszt Luca", preview.source)
        self.assertEqual(preview.reason_code, "DEMO_ORANGE")
        self.assertEqual({f.name for f in fields(preview)}, {"source", "request", "reason_code"})
        with self.assertRaises(AccessDenied):
            self.access.require_owner(self.reviewer, self.case)

    def test_owner_and_reviewer_can_log_in_later_with_same_identity(self):
        other_owner = Actor("other", self.sessions.open_session(), Role.REQUESTER)
        same_owner_new_session = replace(self.owner, session_id=self.sessions.open_session())
        same_reviewer_new_session = replace(self.reviewer, session_id=self.sessions.open_session())
        with self.assertRaises(AccessDenied):
            self.access.require_owner(other_owner, self.case)
        self.access.require_owner(same_owner_new_session, self.case)
        self.access.reviewer_preview(same_reviewer_new_session, self.case)
        self.access.require_case_access(same_reviewer_new_session, self.case)
        other_reviewer = replace(same_reviewer_new_session, identity="other-reviewer")
        with self.assertRaises(AccessDenied):
            self.access.reviewer_preview(other_reviewer, self.case)
        with self.assertRaises(AccessDenied):
            self.access.require_case_access(other_reviewer, self.case)

    def test_decision_actor_for_all_four_levels(self):
        for risk in Risk:
            with self.subTest(risk=risk):
                case = replace(self.case, risk=risk)
                permitted, denied = (self.owner, self.reviewer) if risk in (
                    Risk.GREEN, Risk.YELLOW
                ) else (self.reviewer, self.owner)
                self.access.require_decision_actor(permitted, case)
                with self.assertRaises(AccessDenied):
                    self.access.require_decision_actor(denied, case)

    def test_unassigned_or_same_person_reviewer_is_rejected(self):
        with self.assertRaises(AccessDenied):
            self.access.require_decision_actor(self.reviewer, replace(self.case, reviewer=None))
        with self.assertRaises(AccessDenied):
            replace(self.case, reviewer=replace(self.reviewer, identity=self.owner.identity))
        with self.assertRaises(AccessDenied):
            replace(self.case, reviewer=replace(self.reviewer, session_id=self.owner.session_id))

    def test_closed_request_disables_both_roles(self):
        self.sessions.close_request(self.owner.session_id, self.case.prepared.binding)
        with self.assertRaises(AccessDenied):
            self.access.require_owner(self.owner, self.case)
        with self.assertRaises(AccessDenied):
            self.access.reviewer_preview(self.reviewer, self.case)

    def test_expired_reviewer_or_closed_owner_blocks_review(self):
        self.now = 899
        self.access.reviewer_preview(self.reviewer, self.case)
        self.now = 900
        with self.assertRaises(AccessDenied):
            self.access.require_decision_actor(self.reviewer, self.case)
        reviewer = replace(self.reviewer, session_id=self.sessions.open_session())
        self.sessions.close_session(self.owner.session_id)
        with self.assertRaises(AccessDenied):
            self.access.reviewer_preview(reviewer, replace(self.case, reviewer=reviewer))
