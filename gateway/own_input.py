"""Canonical own-text input and manually selected masking for local preview."""

from dataclasses import dataclass, field
import json
import re

from gateway.text_processing import (
    CanonicalText, InvalidSelection, MaskType, Section, Selection, mask_request,
    validated_selections,
)
from gateway.token_mapping import TokenSelection, reject_reserved_markers


@dataclass(frozen=True, slots=True)
class OwnInput:
    source: CanonicalText = field(repr=False)
    request: CanonicalText = field(repr=False)

    @classmethod
    def from_fields(cls, source: str, request: str):
        if any(not text.strip() or len(text) > 4000 or "\x00" in text for text in (source, request)):
            raise InvalidSelection("Each section requires 1–4000 characters")
        canonical = cls(CanonicalText(Section.SOURCE, source), CanonicalText(Section.REQUEST, request))
        if any(section.text.startswith("\ufeff") for section in (canonical.source, canonical.request)):
            raise InvalidSelection("Repeated leading byte-order mark")
        reject_reserved_markers(canonical.source.text)
        reject_reserved_markers(canonical.request.text)
        return canonical

    def _parsed(self, raw_selections: str) -> dict[Section, tuple[TokenSelection, ...]]:
        try:
            entries = json.loads(raw_selections)
            if not isinstance(entries, list) or not 1 <= len(entries) <= 40:
                raise InvalidSelection("Select between 1 and 40 spans")
            spans: dict[Section, list[TokenSelection]] = {section: [] for section in Section}
            texts = {Section.SOURCE: self.source, Section.REQUEST: self.request}
            for index, entry in enumerate(entries):
                section = Section(entry["section"])
                text = texts[section]
                referent = entry.get("group", "")
                if not isinstance(referent, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,32}", referent):
                    raise InvalidSelection("Invalid token group")
                span = Selection(section, text.digest, entry["start"], entry["end"], MaskType[entry["kind"]])
                spans[section].append(TokenSelection(span, f"group:{referent}" if referent else f"span:{index}"))
        except (KeyError, TypeError, ValueError, RecursionError, AttributeError) as error:
            raise InvalidSelection("Invalid manual selections") from error

        for section, text in texts.items():
            validated_selections(text, tuple(item.span for item in spans[section]))
        return {section: tuple(items) for section, items in spans.items()}

    def token_selections(self, raw_selections: str):
        spans = self._parsed(raw_selections)
        return spans[Section.SOURCE], spans[Section.REQUEST]

    def selections(self, raw_selections: str):
        source, request = self.token_selections(raw_selections)
        return tuple(item.span for item in source), tuple(item.span for item in request)

    def selected_value(self, raw_selections: str) -> tuple[str, str]:
        source_spans, request_spans = self.selections(raw_selections)
        if len(source_spans) + len(request_spans) != 1:
            raise InvalidSelection("Choose exactly one value to remember")
        text, spans = (self.source, source_spans) if source_spans else (self.request, request_spans)
        span = spans[0]
        return text.text[span.start:span.end], span.mask_type.name

    def masked(self, raw_selections: str) -> tuple[str, str]:
        source_spans, request_spans = self.selections(raw_selections)
        treated = mask_request(
            self.source, self.request, source_selections=source_spans, request_selections=request_spans
        )
        selected = {
            text.text[span.start:span.end]
            for text, selections in ((self.source, source_spans), (self.request, request_spans))
            for span in selections
        }
        if any(value in section for value in selected for section in treated):
            raise InvalidSelection("A selected value remains in the preview")
        return treated
