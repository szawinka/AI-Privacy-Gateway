from dataclasses import replace
from hashlib import sha256
import unittest

from gateway.approval import DecisionStore, DecisionUnavailable, FrozenPayload, Outcome
from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, Role, RoleAccess
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import CanonicalText, Section


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sessions = SessionStore(clock=lambda: self.now)
        self.access = RoleAccess(self.sessions)
        self.decisions = DecisionStore(self.access, clock=lambda: self.now)
        self.owner = Actor("owner", self.sessions.open_session(), Role.REQUESTER)
        self.reviewer = Actor("reviewer", self.sessions.open_session(), Role.REVIEWER)
        self.source = CanonicalText(Section.SOURCE, "Árvíztűrő tükörfúrógép")
        self.request = CanonicalText(Section.REQUEST, "Összegzés")
        prepared = self.sessions.prepare(self.owner.session_id, self.source, self.request)
        case = CaseAccess(self.owner, self.reviewer, Risk.ORANGE, "DEMO", prepared)
        self.payload = FrozenPayload(case, 1, "summary-v1", "policy-v1", "adapter-v1")

    def record(self, payload=None, actor=None, outcome=Outcome.APPROVED):
        payload = payload or self.payload
        return self.decisions.record(
            actor or self.reviewer,
            payload,
            viewed_version=payload.version,
            viewed_digest=payload.digest,
            outcome=outcome,
        )

    def require(self, payload=None, body=None):
        payload = payload or self.payload
        self.decisions.require_approval(self.owner, payload, payload.body if body is None else body)

    def test_canonical_bytes_and_approval(self):
        expected = (
            '{"adapter_config_version":"adapter-v1","policy_version":"policy-v1",'
            '"request":"Összegzés","source":"Árvíztűrő tükörfúrógép",'
            '"task_template":"summary-v1"}'
        ).encode("utf-8")
        self.assertEqual(self.payload.body, expected)
        self.assertEqual(self.payload.digest, sha256(expected).hexdigest())
        self.record()
        self.require(body=expected)

    def test_stale_preview_and_wrong_decision_actor_rejected(self):
        changed = replace(self.payload, version=2, task_template="summary-v2")
        for version, digest in ((1, changed.digest), (2, self.payload.digest)):
            with self.subTest(version=version), self.assertRaises(DecisionUnavailable):
                self.decisions.record(
                    self.reviewer, changed, viewed_version=version,
                    viewed_digest=digest, outcome=Outcome.APPROVED,
                )
        with self.assertRaises(AccessDenied):
            self.record(actor=self.owner)
        low_risk = replace(self.payload, case=replace(self.payload.case, risk=Risk.YELLOW))
        self.record(low_risk, actor=self.owner)
        self.require(low_risk)

    def test_changed_content_configuration_or_adapter_bytes_rejected(self):
        self.record()
        changed_case = replace(
            self.payload.case,
            prepared=replace(self.payload.case.prepared, request="Módosított kérés"),
        )
        for changed in (
            replace(self.payload, case=changed_case),
            replace(self.payload, policy_version="policy-v2"),
            replace(self.payload, adapter_config_version="adapter-v2"),
            replace(self.payload, version=2),
        ):
            with self.subTest(changed=changed), self.assertRaises(DecisionUnavailable):
                self.require(changed)
        with self.assertRaises(DecisionUnavailable):
            self.require(body=self.payload.body + b" ")

    def test_return_requires_new_version_and_decision(self):
        self.record(outcome=Outcome.RETURNED)
        with self.assertRaises(DecisionUnavailable):
            self.require()
        with self.assertRaises(DecisionUnavailable):
            self.record()
        revision = replace(self.payload, version=2, task_template="summary-v2")
        with self.assertRaises(DecisionUnavailable):
            self.require(revision)
        self.record(revision)
        self.require(revision)
        with self.assertRaises(DecisionUnavailable):
            self.record()

    def test_approval_expires_at_five_minutes_and_cannot_be_replayed(self):
        self.record()
        self.now = 299.999
        self.require()
        self.now = 300
        with self.assertRaises(DecisionUnavailable):
            self.require()
        with self.assertRaises(DecisionUnavailable):
            self.record()

    def test_session_expiry_precedes_approval_expiry(self):
        self.now = 899
        self.record()
        self.require()
        self.now = 900
        with self.assertRaises(AccessDenied):
            self.require()

    def test_closure_new_request_and_restart_invalidate_approval(self):
        self.record()
        fresh_request = self.sessions.prepare(self.owner.session_id, self.source, self.request)
        changed = replace(self.payload, case=replace(self.payload.case, prepared=fresh_request))
        self.assertEqual(changed.digest, self.payload.digest)
        with self.assertRaises(DecisionUnavailable):
            self.require(changed)
        restarted = DecisionStore(self.access, clock=lambda: self.now)
        with self.assertRaises(DecisionUnavailable):
            restarted.require_approval(self.owner, self.payload, self.payload.body)
        self.sessions.close_request(self.owner.session_id, self.payload.case.prepared.binding)
        with self.assertRaises(AccessDenied):
            self.require()
