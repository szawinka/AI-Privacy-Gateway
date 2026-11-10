"""Canonical text and manually selected spans for the local A-stage demo."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from unicodedata import normalize


class Section(Enum):
    SOURCE = "source"
    REQUEST = "request"


class MaskType(Enum):
    PERSON = "[SZEMÉLY]"
    EMAIL = "[E-MAIL]"
    SENSITIVE = "[ÉRZÉKENY]"


class InvalidSelection(ValueError):
    """The selection cannot safely be applied to the supplied text."""


@dataclass(frozen=True, slots=True)
class CanonicalText:
    section: Section
    text: str
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.section, Section):
            raise ValueError("Unknown text section")
        canonical = normalize(
            "NFC", self.text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        )
        object.__setattr__(self, "text", canonical)
        object.__setattr__(self, "digest", sha256(canonical.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class Selection:
    section: Section
    digest: str
    start: int
    end: int
    mask_type: MaskType


def validated_selections(
    section: CanonicalText, selections: tuple[Selection, ...]
) -> tuple[Selection, ...]:
    """Validate all spans and return them in canonical text order."""
    for span in selections:
        if span.section is not section.section or span.digest != section.digest:
            raise InvalidSelection("Selection belongs to another section or text version")
        if (
            type(span.start) is not int
            or type(span.end) is not int
            or not 0 <= span.start < span.end <= len(section.text)
        ):
            raise InvalidSelection("Invalid selection bounds")
        if not isinstance(span.mask_type, MaskType):
            raise InvalidSelection("Unknown mask type")

    ordered = tuple(sorted(selections, key=lambda item: item.start))
    cursor = 0
    for span in ordered:
        if span.start < cursor:
            raise InvalidSelection("Overlapping selections")
        cursor = span.end
    return ordered


def _mask(section: CanonicalText, selections: tuple[Selection, ...]) -> str:
    parts = []
    cursor = 0
    for span in validated_selections(section, selections):
        parts.extend((section.text[cursor:span.start], span.mask_type.value))
        cursor = span.end
    parts.append(section.text[cursor:])
    return "".join(parts)


def mask_request(
    source: CanonicalText,
    request: CanonicalText,
    *,
    source_selections: tuple[Selection, ...] = (),
    request_selections: tuple[Selection, ...] = (),
) -> tuple[str, str]:
    """Return both masked sections, or raise without returning either result."""
    if source.section is not Section.SOURCE or request.section is not Section.REQUEST:
        raise InvalidSelection("Source and request must have distinct section roles")
    return _mask(source, source_selections), _mask(request, request_selections)
