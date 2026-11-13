# Károly Tamás szakdolgozati prototípusa (2026).
"""In-memory reversible tokens for the local A-stage demo."""

from dataclasses import dataclass, field
import re
from secrets import token_hex
from types import MappingProxyType
from typing import Mapping

from gateway.text_processing import (
    CanonicalText, MaskType, Section, Selection, validated_selections,
)


_TOKEN_KINDS = {MaskType.PERSON: "SZEMELY", MaskType.EMAIL: "EMAIL", MaskType.SENSITIVE: "ERZEKENY"}
_KIND_PATTERN = "|".join(_TOKEN_KINDS.values())
_RESERVED = re.compile(rf"\[\[A|A:(?:{_KIND_PATTERN}):")
_TOKEN = re.compile(rf"\[\[A:(?:{_KIND_PATTERN}):[0-9a-f]{{32}}:[0-9]{{6}}\]\]")


class InvalidToken(ValueError):
    """The token operation cannot safely return an output."""


def reject_reserved_markers(text: str) -> None:
    """Reject any reserved token marker in text that must be token-free."""
    if _RESERVED.search(text):
        raise InvalidToken("Reserved token marker in token-free text")


@dataclass(frozen=True, slots=True)
class RequestBinding:
    session_id: str
    request_id: str
    source_digest: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class TokenSelection:
    span: Selection
    referent: str


class PersonSelection(TokenSelection):
    """Person-only selection used by the fixed document demo."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class TokenizedRequest:
    source: str = field(repr=False)
    request: str = field(repr=False)
    binding: RequestBinding = field(repr=False)
    _values: Mapping[str, str] = field(repr=False)

    @property
    def tokens(self) -> frozenset[str]:
        return frozenset(self._values)

    def require_no_residue(self) -> None:
        for text in (self.source, self.request):
            if any(value in part for part in _TOKEN.split(text) for value in self._values.values()):
                raise InvalidToken("A selected value remains outside the tokens")

    def restore(
        self, response: str, *, binding: RequestBinding, allowed: frozenset[str]
    ) -> str:
        if binding != self.binding:
            raise InvalidToken("Token mapping belongs to another session, request or version")
        if not allowed <= self.tokens:
            raise InvalidToken("Authorization includes an unknown token")

        parts = []
        cursor = 0
        while marker := _RESERVED.search(response, cursor):
            token = _TOKEN.match(response, marker.start())
            if token is None or token.group() not in allowed:
                raise InvalidToken("Malformed, unknown or unauthorized response token")
            parts.extend((response[cursor:marker.start()], self._values[token.group()]))
            cursor = token.end()
        parts.append(response[cursor:])
        return "".join(parts)


def tokenize_request(
    source: CanonicalText,
    request: CanonicalText,
    *,
    binding: RequestBinding,
    source_selections: tuple[TokenSelection, ...] = (),
    request_selections: tuple[TokenSelection, ...] = (),
) -> TokenizedRequest:
    if (
        source.section is not Section.SOURCE
        or request.section is not Section.REQUEST
        or not isinstance(binding.session_id, str)
        or not binding.session_id.strip()
        or not isinstance(binding.request_id, str)
        or not binding.request_id.strip()
        or (source.digest, request.digest) != (binding.source_digest, binding.request_digest)
    ):
        raise InvalidToken("Invalid session, request or text version binding")
    reject_reserved_markers(source.text)
    reject_reserved_markers(request.text)

    def checked(section, selected):
        ordered = validated_selections(section, tuple(item.span for item in selected))
        references = {}
        for item in selected:
            if (
                (isinstance(item, PersonSelection) and item.span.mask_type is not MaskType.PERSON)
                or not isinstance(item.referent, str)
                or not item.referent.strip()
            ):
                raise InvalidToken("A token selection requires a valid type and referent")
            references[item.span] = item.referent
        return ordered, references

    source_spans = checked(source, source_selections)
    request_spans = checked(request, request_selections)
    namespace = token_hex(16)
    by_value: dict[tuple[MaskType, str, str], str] = {}
    values: dict[str, str] = {}

    def render(section, checked_spans):
        spans, references = checked_spans
        parts = []
        cursor = 0
        for span in spans:
            value = section.text[span.start:span.end]
            key = span.mask_type, references[span], value
            token = by_value.get(key)
            if token is None:
                serial = len(values) + 1
                if serial > 999999:
                    raise InvalidToken("Token serial space exhausted")
                token = f"[[A:{_TOKEN_KINDS[span.mask_type]}:{namespace}:{serial:06d}]]"
                by_value[key] = token
                values[token] = value
            parts.extend((section.text[cursor:span.start], token))
            cursor = span.end
        parts.append(section.text[cursor:])
        return "".join(parts)

    return TokenizedRequest(
        render(source, source_spans),
        render(request, request_spans),
        binding,
        MappingProxyType(values),
    )
