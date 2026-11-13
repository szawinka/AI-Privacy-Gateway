# Károly Tamás szakdolgozati prototípusa (2026).
"""Authorization rules for trusted, server-resolved demo identities."""

from dataclasses import dataclass, field
from enum import Enum

from gateway.session_lifecycle import PreparedRequest, SessionStore


class AccessDenied(PermissionError):
    """The actor is not permitted to perform this operation."""


class Role(Enum):
    REQUESTER = "requester"
    REVIEWER = "reviewer"


class Risk(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True, slots=True)
class Actor:
    """Server-side identity resolved after login; never deserialize from request data."""

    identity: str
    session_id: str = field(repr=False)
    role: Role


@dataclass(frozen=True, slots=True)
class CaseAccess:
    owner: Actor
    reviewer: Actor | None
    risk: Risk
    reason_code: str
    prepared: PreparedRequest = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self.owner.role is not Role.REQUESTER
            or self.owner.session_id != self.prepared.binding.session_id
            or not isinstance(self.risk, Risk)
        ):
            raise AccessDenied("Invalid owner or risk")
        if self.reviewer is not None and (
            self.reviewer.role is not Role.REVIEWER
            or self.reviewer.identity == self.owner.identity
            or self.reviewer.session_id == self.owner.session_id
        ):
            raise AccessDenied("Reviewer must be a distinct identity and session")


@dataclass(frozen=True, slots=True)
class ReviewerPreview:
    source: str = field(repr=False)
    request: str = field(repr=False)
    reason_code: str


class RoleAccess:
    def __init__(self, sessions: SessionStore):
        self._sessions = sessions

    def _require_live(self, actor: Actor, case: CaseAccess) -> None:
        if not self._sessions.is_active(actor.session_id) or not self._sessions.is_current(
            case.prepared.binding
        ):
            raise AccessDenied("Session or request unavailable")

    def require_owner(self, actor: Actor, case: CaseAccess) -> None:
        """Check access to operations reserved for the case owner."""
        self._require_live(actor, case)
        if actor.role is not Role.REQUESTER or actor.identity != case.owner.identity:
            raise AccessDenied("Owner access required")

    def _require_reviewer(self, actor: Actor, case: CaseAccess) -> None:
        self._require_live(actor, case)
        if not self.is_assigned_reviewer(actor, case):
            raise AccessDenied("Assigned reviewer access required")

    @staticmethod
    def is_assigned_reviewer(actor: Actor, case: CaseAccess) -> bool:
        """The assignment identifies a reviewer account, not one login session."""
        return (
            actor.role is Role.REVIEWER
            and case.reviewer is not None
            and actor.identity == case.reviewer.identity
        )

    def require_case_access(self, actor: Actor, case: CaseAccess) -> None:
        if actor.role is Role.REQUESTER:
            self.require_owner(actor, case)
        else:
            self._require_reviewer(actor, case)

    def reviewer_preview(self, actor: Actor, case: CaseAccess) -> ReviewerPreview:
        self._require_reviewer(actor, case)
        return ReviewerPreview(case.prepared.source, case.prepared.request, case.reason_code)

    def require_decision_actor(self, actor: Actor, case: CaseAccess) -> None:
        """Authorize who may decide; this does not create an approval."""
        if case.risk in (Risk.ORANGE, Risk.RED):
            self._require_reviewer(actor, case)
        else:
            self.require_owner(actor, case)
