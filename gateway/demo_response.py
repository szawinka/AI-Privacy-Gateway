"""Local A-stage demo answers and requester-only response handling."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from gateway.approval import FrozenPayload
from gateway.role_access import Actor, RoleAccess
from gateway.send_gate import LocalSendGate
from gateway.session_lifecycle import PreparedRequest, SessionStore
from gateway.token_mapping import InvalidToken, reject_reserved_markers


class DemoTask(Enum):
    DOCUMENT_SUMMARY = "A-DOC-01"
    MAIL_DRAFT = "A-MAIL-01"


@dataclass(frozen=True, slots=True)
class DemoResponse:
    """Server-selected task and the sole DOC token eligible for restoration."""

    task: DemoTask
    document_token: str | None = field(default=None, repr=False)

    def allowed_tokens(self, prepared: PreparedRequest) -> frozenset[str]:
        if self.task is DemoTask.DOCUMENT_SUMMARY:
            if self.document_token is None or self.document_token not in prepared.tokens:
                raise InvalidToken("Document response token is not bound to the request")
            return frozenset({self.document_token})
        if self.task is DemoTask.MAIL_DRAFT and self.document_token is None and not prepared.tokens:
            return frozenset()
        raise InvalidToken("Invalid demo response configuration")

    def adapter_for(self, payload: FrozenPayload) -> Callable[[bytes], str]:
        """Create a deterministic offline adapter for the approved demo payload."""
        self._validate(payload)

        def respond(body: bytes) -> str:
            if body != payload.body:
                raise InvalidToken("Unexpected demo payload")
            if self.task is DemoTask.DOCUMENT_SUMMARY:
                return (
                    f"{self.document_token} átvette a SYN-17 mintadobozt. "
                    "Péntekre állapotjelentést ígért."
                )
            return (
                "Köszönöm a javaslatot! Csütörtök megfelelő. "
                "A pontos időpontot később egyeztetjük."
            )

        return respond

    def _validate(self, payload: FrozenPayload) -> frozenset[str]:
        if payload.task_template != self.task.value:
            raise InvalidToken("Response task does not match the approved payload")
        return self.allowed_tokens(payload.case.prepared)

    def send_and_finish(
        self,
        gate: LocalSendGate,
        sessions: SessionStore,
        requester: Actor,
        payload: FrozenPayload,
        body: bytes,
    ) -> str:
        allowed = self._validate(payload)
        response = gate.send(requester, payload, body)
        RoleAccess(sessions).require_owner(requester, payload.case)
        if not isinstance(response, str):
            raise InvalidToken("Demo response must be text")
        if self.task is DemoTask.MAIL_DRAFT:
            reject_reserved_markers(response)
            return response
        return sessions.restore(
            requester.session_id, payload.case.prepared.binding, response, allowed=allowed
        )
