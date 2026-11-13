# Károly Tamás szakdolgozati prototípusa (2026).
"""Prepare the two fixed synthetic fixtures and their reviewable payloads."""

from dataclasses import dataclass, field

from gateway.approval import FrozenPayload
from gateway.demo_response import DemoResponse, DemoTask
from gateway.role_access import AccessDenied, Actor, CaseAccess, Risk, Role, RoleAccess
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import CanonicalText, InvalidSelection, MaskType, Section, Selection
from gateway.token_mapping import PersonSelection


_DOC_SOURCE = (
    "A Teszt Műhely próbáján Teszt Luca átvette a SYN-17 mintadobozt. "
    "Teszt Luca péntekre ígért állapotjelentést. "
    "A vendégként szereplő másik Teszt Luca csak megfigyelő volt."
)
_DOC_REQUEST = "Foglald össze két mondatban, mi történt és mi a következő lépés."
_MAIL_SOURCE = (
    "Kedves csapat! A SYN-42 bemutató próbáját áttehetnénk csütörtökre? "
    "A tesztkapcsolat demo42@example.invalid. Üdv, Teszt Mira"
)
_MAIL_REQUEST = (
    "Írj rövid választ Teszt Mirának: csütörtök megfelelő, pontos időpontot még ne ígérj."
)


@dataclass(frozen=True, slots=True)
class DemoPreview:
    version: int
    digest: str = field(repr=False)
    body: bytes = field(repr=False)
    reason_code: str
    provider_body: bytes | None = field(default=None, repr=False)
    external_https: bool = False


@dataclass(frozen=True, slots=True)
class PreparedDemo:
    payload: FrozenPayload = field(repr=False)
    response: DemoResponse | None

    def preview(self, access: RoleAccess, actor: Actor) -> DemoPreview:
        case = self.payload.case
        access.require_case_access(actor, case)
        return DemoPreview(
            self.payload.version, self.payload.digest, self.payload.body, case.reason_code,
            self.payload.provider_body, self.payload.external_https,
        )


def _span(section: CanonicalText, value: str, kind: MaskType, start: int = 0) -> Selection:
    offset = section.text.index(value, start)
    return Selection(section.section, section.digest, offset, offset + len(value), kind)


def _reject_residue(protected: tuple[str, ...], *sections: str) -> None:
    if any(value in text for text in sections for value in protected):
        raise InvalidSelection("Protected fixture value remains after treatment")


def prepare_demo(
    sessions: SessionStore, task: DemoTask, owner: Actor, *, reviewer: Actor | None = None
) -> PreparedDemo:
    """Build a fixed fixture using trusted identities and explicit synthetic selections."""
    if owner.role is not Role.REQUESTER or (reviewer is not None and (
        reviewer.role is not Role.REVIEWER
        or reviewer.identity == owner.identity
        or reviewer.session_id == owner.session_id
    )):
        raise AccessDenied("Invalid demo identities")

    if task is DemoTask.DOCUMENT_SUMMARY:
        source = CanonicalText(Section.SOURCE, _DOC_SOURCE)
        request = CanonicalText(Section.REQUEST, _DOC_REQUEST)
        name = "Teszt Luca"
        first = _span(source, name, MaskType.PERSON)
        second = _span(source, name, MaskType.PERSON, first.end)
        third = _span(source, name, MaskType.PERSON, second.end)
        prepared = sessions.prepare(
            owner.session_id, source, request,
            source_selections=(
                PersonSelection(first, "R1"), PersonSelection(second, "R1"),
                PersonSelection(third, "R2"),
            ),
        )
        risk, reason = Risk.YELLOW, "DEMO_DOC_TOKEN"
        protected = (name,)
    elif task is DemoTask.MAIL_DRAFT:
        if reviewer is None:
            raise AccessDenied("MAIL requires an assigned reviewer")
        source = CanonicalText(Section.SOURCE, _MAIL_SOURCE)
        request = CanonicalText(Section.REQUEST, _MAIL_REQUEST)
        name, address, inflected = "Teszt Mira", "demo42@example.invalid", "Teszt Mirának"
        prepared = sessions.prepare_masked(
            owner.session_id, source, request,
            source_selections=(
                _span(source, name, MaskType.PERSON), _span(source, address, MaskType.EMAIL),
            ),
            request_selections=(_span(request, inflected, MaskType.PERSON),),
        )
        risk, reason = Risk.ORANGE, "DEMO_MAIL_MASK"
        protected = (name, address, inflected)
    else:
        raise ValueError("Unknown demo task")

    try:
        _reject_residue(protected, prepared.source, prepared.request)
    except InvalidSelection:
        sessions.close_request(owner.session_id, prepared.binding)
        raise

    # The first selected span starts at the same offset after treatment.
    document_token = None
    if task is DemoTask.DOCUMENT_SUMMARY:
        document_token = prepared.source[first.start:].split(" ", 1)[0]
    case = CaseAccess(owner, reviewer, risk, reason, prepared)
    return PreparedDemo(
        FrozenPayload(case, 1, task.value, "demo-a-v0.1", "local-demo-v1"),
        DemoResponse(task, document_token),
    )
