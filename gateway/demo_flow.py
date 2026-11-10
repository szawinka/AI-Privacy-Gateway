"""Server-side workflow for the two local demo fixtures."""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from threading import Lock
from time import monotonic

from gateway.approval import DecisionStore, FrozenPayload, Outcome
from gateway.demo_input import DemoPreview, PreparedDemo, prepare_demo
from gateway.demo_response import DemoTask
from gateway.operator_config import ConfigError, ModelProfile, OperatorConfig
from gateway.provider_https import ProviderHTTPS
from gateway.provider_messages import local_response, response_text
from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, RoleAccess
from gateway.send_gate import LocalSendGate, SQLiteSendAudit
from gateway.session_lifecycle import PreparedRequest, SessionStore
from gateway.token_mapping import InvalidToken, reject_reserved_markers


@dataclass(frozen=True, slots=True)
class CaseProgress:
    case_id: str
    kind: str
    status: str
    next_role: str
    action: str
    can_decide: bool = False
    can_send: bool = False


class DemoFlow:
    """Resolve opaque case identifiers to server-owned payloads and response settings."""

    def __init__(
        self, sessions: SessionStore, audit_path: Path, *, clock: Callable[[], float] = monotonic,
        operator_config: OperatorConfig | None = None, provider_https: ProviderHTTPS | None = None,
    ):
        self._sessions = sessions
        self._operator_config = operator_config
        self._provider_https = provider_https
        self._access = RoleAccess(sessions)
        self._decisions = DecisionStore(self._access, clock=clock)
        self._audit = SQLiteSendAudit(audit_path)
        self._https_audit = SQLiteSendAudit(audit_path, reason_code="HTTPS_SEND_AUTHORIZED")
        self._cases: dict[str, PreparedDemo] = {}
        self._lock = Lock()
        self._completed: set[str] = set()

    def start(self, task: DemoTask, owner: Actor, *, reviewer: Actor | None = None) -> str:
        demo = prepare_demo(self._sessions, task, owner, reviewer=reviewer)
        return self._register(demo)

    def start_own(
        self, owner: Actor, reviewer: Actor, prepared: PreparedRequest,
        *, profile: ModelProfile | None = None,
    ) -> str:
        if not self._sessions.is_active(reviewer.session_id):
            raise AccessDenied("Reviewer session unavailable")
        case = CaseAccess(owner, reviewer, Risk.ORANGE, "OWN_REVIEW_REQUIRED", prepared)
        self._access.require_owner(owner, case)
        if profile is None:
            payload = FrozenPayload(case, 1, "own-local-check", "own-review-v1", "local-check-v1")
        else:
            if self._provider_https is not None and not self._provider_https.allows(profile):
                raise AccessDenied("Provider target is not approved")
            payload = FrozenPayload(
                case, 1, f"own-{profile.task}-v1", "own-review-v1",
                f"{profile.connection_revision}:{profile.profile_revision}", profile=profile,
                external_https=self._provider_https is not None,
            )
        return self._register(PreparedDemo(payload, None))

    def _register(self, demo: PreparedDemo) -> str:
        binding = demo.payload.case.prepared.binding
        with self._lock:
            self._cases = {
                key: case for key, case in self._cases.items()
                if self._sessions.is_current(case.payload.case.prepared.binding)
            }
            self._completed.intersection_update(self._cases)
            self._cases[binding.request_id] = demo
        return binding.request_id

    def _case(self, case_id: str) -> PreparedDemo:
        with self._lock:
            demo = self._cases.get(case_id)
            if demo is None:
                raise AccessDenied("Demo case unavailable")
            if not self._sessions.is_current(demo.payload.case.prepared.binding):
                del self._cases[case_id]
                self._completed.discard(case_id)
                raise AccessDenied("Demo case unavailable")
            return demo

    def preview(self, actor: Actor, case_id: str) -> DemoPreview:
        return self._case(case_id).preview(self._access, actor)

    def inbox(self, actor: Actor) -> list[CaseProgress]:
        with self._lock:
            ids = [
                case_id for case_id, demo in reversed(self._cases.items())
                if actor in (demo.payload.case.owner, demo.payload.case.reviewer)
            ]
        if not ids:
            return []
        reservations = self._audit.reserved_cases()
        rows = []
        for case_id in ids:
            try:
                rows.append(self.progress(actor, case_id, reservations=reservations))
            except AccessDenied:
                continue
        return rows

    def progress(self, actor: Actor, case_id: str, *, reservations=None) -> CaseProgress:
        demo = self._case(case_id)
        payload = demo.payload
        case = payload.case
        owner = actor == case.owner
        self._access.require_case_access(actor, case)
        kind = {
            "DEMO_DOC_TOKEN": "Dokumentumdemó",
            "DEMO_MAIL_MASK": "Levéldemó",
        }.get(case.reason_code, "Saját szöveg")
        if reservations is None:
            reservations = self._audit.reserved_cases()
        with self._lock:
            if case_id in self._completed:
                return CaseProgress(case_id, kind, "Futtatás befejezve", "—", "Ügy megtekintése")
        if (case_id, str(payload.version)) in reservations:
            return CaseProgress(case_id, kind, "Futtatás lefoglalva", "—", "Ügy megtekintése")
        outcome, expired = self._decisions.status(payload)
        if outcome is Outcome.RETURNED:
            return CaseProgress(case_id, kind, "Visszaadva javításra", "Kérő", "Új ügy indítása" if owner else "Ügy megtekintése")
        if expired or (case.reviewer and not self._sessions.is_active(case.reviewer.session_id)):
            return CaseProgress(case_id, kind, "Jóváhagyás lejárt" if expired else "Ellenőri munkamenet lezárult",
                                "Kérő", "Új ügy indítása" if owner else "Ügy megtekintése")
        if outcome is Outcome.APPROVED:
            return CaseProgress(case_id, kind, "Jóváhagyva", "Kérő",
                                "Futtatás megnyitása" if owner else "Ügy megtekintése", can_send=owner)
        decider = case.reviewer or case.owner
        can_decide = actor == decider
        return CaseProgress(case_id, kind, "Döntésre vár", "Ellenőr" if case.reviewer else "Kérő",
                            "Megnyitás döntéshez" if can_decide else "Ügy megtekintése", can_decide=can_decide)

    def decide(
        self, actor: Actor, case_id: str, *, version: int, digest: str, outcome: Outcome
    ) -> None:
        payload = self._case(case_id).payload
        if payload.profile is not None:
            self._access.require_decision_actor(actor, payload.case)
            with self._current_profile(payload.profile):
                self._decisions.record(
                    actor, payload, viewed_version=version, viewed_digest=digest, outcome=outcome
                )
            return
        self._decisions.record(
            actor, payload,
            viewed_version=version, viewed_digest=digest, outcome=outcome,
        )

    @contextmanager
    def _current_profile(self, profile: ModelProfile):
        if self._operator_config is None:
            raise AccessDenied("Model configuration is unavailable")
        try:
            with self._operator_config.current_profile(profile) as read_key:
                yield read_key
        except PermissionError:
            raise
        except (ConfigError, OSError, sqlite3.Error):
            raise AccessDenied("Configured model profile unavailable or changed") from None

    def send(self, requester: Actor, case_id: str) -> str:
        demo = self._case(case_id)
        payload = demo.payload
        if demo.response is None:
            if payload.profile is None:
                gate = LocalSendGate(self._decisions, self._audit, self._local_receipt)
                result = gate.send(requester, payload, payload.body)
            else:
                result = self._send_configured(requester, payload)
            self._access.require_owner(requester, payload.case)
        else:
            gate = LocalSendGate(self._decisions, self._audit, demo.response.adapter_for(payload))
            result = demo.response.send_and_finish(gate, self._sessions, requester, payload, payload.body)
        with self._lock:
            self._completed.add(case_id)
        return result

    def _send_configured(self, requester: Actor, payload: FrozenPayload) -> str:
        profile = payload.profile
        self._decisions.require_approval(requester, payload, payload.adapter_body)
        with self._current_profile(profile) as read_key:
            if payload.external_https:
                transport = self._provider_https
                if transport is None or not transport.allows(profile):
                    raise AccessDenied("Provider target is not approved")
                gate = LocalSendGate(
                    self._decisions, self._https_audit,
                    lambda body: transport.send(profile, body, api_key=read_key()),
                )
            else:
                gate = LocalSendGate(
                    self._decisions, self._audit, lambda body: local_response(profile.protocol, body)
                )
            reply = gate.send(requester, payload, payload.adapter_body)
        self._access.require_owner(requester, payload.case)
        result = response_text(profile.protocol, reply)
        prepared = payload.case.prepared
        try:
            if prepared.tokens:
                return self._sessions.restore(
                    requester.session_id, prepared.binding, result, allowed=prepared.tokens
                )
            reject_reserved_markers(result)
            return result
        except InvalidToken:
            raise AccessDenied("Provider response contains invalid tokens") from None

    @staticmethod
    def _local_receipt(_body: bytes) -> str:
        return "A helyi kapupróba sikerült. Ez egy rögzített visszaigazolás."
