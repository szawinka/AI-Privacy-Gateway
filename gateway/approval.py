# Károly Tamás szakdolgozati prototípusa (2026).
"""Process-local decisions for a frozen, server-built payload."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from threading import Lock
from time import monotonic

from gateway.operator_config import ModelProfile
from gateway.provider_messages import json_bytes, request_body
from gateway.role_access import Actor, CaseAccess, RoleAccess
from gateway.token_mapping import RequestBinding


class DecisionUnavailable(PermissionError):
    """The submitted preview or recorded decision cannot authorize this payload."""


class Outcome(Enum):
    APPROVED = "approved"
    RETURNED = "returned"


@dataclass(frozen=True, slots=True)
class FrozenPayload:
    case: CaseAccess = field(repr=False)
    version: int
    task_template: str = field(repr=False)
    policy_version: str
    adapter_config_version: str
    profile: ModelProfile | None = None
    external_https: bool = False
    body: bytes = field(init=False, repr=False)
    digest: str = field(init=False, repr=False)
    provider_body: bytes | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("Payload version must be a positive integer")
        fields = {
            "source": self.case.prepared.source,
            "request": self.case.prepared.request,
            "task_template": self.task_template,
            "policy_version": self.policy_version,
            "adapter_config_version": self.adapter_config_version,
        }
        if any(not isinstance(value, str) for value in fields.values()):
            raise TypeError("Payload fields must be strings")
        if self.profile is not None:
            provider_body = request_body(self.profile, fields["source"], fields["request"])
            fields["model_profile"] = asdict(self.profile)
            if self.external_https:
                fields["delivery"] = "external-https"
            fields["provider_request_sha256"] = sha256(provider_body).hexdigest()
            object.__setattr__(self, "provider_body", provider_body)
        body = json_bytes(fields)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "digest", sha256(body).hexdigest())


    @property
    def adapter_body(self) -> bytes:
        return self.body if self.provider_body is None else self.provider_body


@dataclass(frozen=True, slots=True)
class Decision:
    actor: Actor = field(repr=False)
    version: int
    digest: str = field(repr=False)
    outcome: Outcome
    expires_at: float


class DecisionStore:
    """Record decisions and validate them before the later send gate."""

    TTL_SECONDS = 5 * 60

    def __init__(self, access: RoleAccess, *, clock: Callable[[], float] = monotonic):
        self._access = access
        self._clock = clock
        self._decisions: dict[RequestBinding, Decision] = {}
        self._lock = Lock()

    def record(
        self,
        actor: Actor,
        payload: FrozenPayload,
        *,
        viewed_version: int,
        viewed_digest: str,
        outcome: Outcome,
    ) -> Decision:
        if not isinstance(outcome, Outcome):
            raise TypeError("Invalid decision outcome")
        with self._lock:
            self._access.require_decision_actor(actor, payload.case)
            if (viewed_version, viewed_digest) != (payload.version, payload.digest):
                raise DecisionUnavailable("Preview does not match the current payload")
            binding = payload.case.prepared.binding
            previous = self._decisions.get(binding)
            if previous is not None and payload.version <= previous.version:
                raise DecisionUnavailable("A decision already exists for this or a newer version")
            decision = Decision(
                actor, payload.version, payload.digest, outcome, self._clock() + self.TTL_SECONDS
            )
            self._decisions[binding] = decision
            return decision

    def status(self, payload: FrozenPayload) -> tuple[Outcome | None, bool]:
        """Report the recorded outcome and whether its approval window has expired."""
        with self._lock:
            decision = self._decisions.get(payload.case.prepared.binding)
            if decision is None or (decision.version, decision.digest) != (payload.version, payload.digest):
                return None, False
            return decision.outcome, self._clock() >= decision.expires_at

    def restore_decision(
        self, payload: FrozenPayload, *, identity: str, role, outcome: Outcome,
        remaining_seconds: float,
    ) -> None:
        """Recreate a locally recorded decision after a process restart."""
        case = payload.case
        expected = case.reviewer if case.risk.value in ("orange", "red") else case.owner
        if expected is None or role is not expected.role or identity != expected.identity:
            raise ValueError("Stored decision actor does not match the case")
        decision = Decision(
            Actor(identity, "restored-decision", role), payload.version, payload.digest,
            outcome, self._clock() + remaining_seconds,
        )
        with self._lock:
            self._decisions[case.prepared.binding] = decision

    def forget_if_same(self, payload: FrozenPayload, decision: Decision) -> None:
        """Undo an in-memory decision if its durable write failed."""
        binding = payload.case.prepared.binding
        with self._lock:
            if self._decisions.get(binding) is decision:
                del self._decisions[binding]

    def require_approval(self, requester: Actor, payload: FrozenPayload, body: bytes) -> None:
        """Validate the current decision and the exact proposed adapter bytes."""
        with self._lock:
            self._access.require_owner(requester, payload.case)
            decision = self._decisions.get(payload.case.prepared.binding)
            if (
                decision is None
                or decision.outcome is not Outcome.APPROVED
                or (decision.version, decision.digest) != (payload.version, payload.digest)
                or type(body) is not bytes
                or body != payload.adapter_body
            ):
                raise DecisionUnavailable("Approval unavailable for this payload")
            # record() already authenticated the decider; a later logout must not
            # invalidate their still-current approval of these exact bytes.
            if self._clock() >= decision.expires_at:
                raise DecisionUnavailable("Approval expired")
