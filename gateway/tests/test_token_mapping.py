from dataclasses import replace
import re
import unittest

from gateway.text_processing import CanonicalText, InvalidSelection, MaskType, Section, Selection
from gateway.token_mapping import (
    InvalidToken, PersonSelection, RequestBinding, tokenize_request,
)


class TokenMappingTests(unittest.TestCase):
    def setUp(self):
        self.source = CanonicalText(
            Section.SOURCE,
            "A Teszt Műhely próbáján Teszt Luca átvette a SYN-17 mintadobozt. "
            "Teszt Luca péntekre ígért állapotjelentést. "
            "A vendégként szereplő másik Teszt Luca csak megfigyelő volt.",
        )
        self.request = CanonicalText(Section.REQUEST, "Készíts összefoglalót Teszt Lucának.")
        self.binding = RequestBinding("session", "request", self.source.digest, self.request.digest)
        self.selections = tuple(
            PersonSelection(Selection(Section.SOURCE, self.source.digest, *m.span(), MaskType.PERSON), ref)
            for m, ref in zip(re.finditer("Teszt Luca", self.source.text), ("R1", "R1", "R2"), strict=True)
        )

    def tokenize(self, binding=None):
        return tokenize_request(
            self.source, self.request, binding=binding or self.binding,
            source_selections=self.selections,
        )

    def test_repeat_and_distinct_referents_and_round_trip(self):
        result = self.tokenize()
        tokens = re.findall(r"\[\[A:SZEMELY:[0-9a-f]{32}:[0-9]{6}\]\]", result.source)
        self.assertEqual(len(tokens), 3)
        self.assertEqual(tokens[0], tokens[1])
        self.assertNotEqual(tokens[0], tokens[2])
        self.assertNotIn("Teszt Luca", result.source)
        self.assertEqual(result.restore(result.source, binding=self.binding, allowed=result.tokens), self.source.text)

    def test_full_inflected_span_restored(self):
        word = "Teszt Lucának"
        start = self.request.text.index(word)
        selected = PersonSelection(
            Selection(Section.REQUEST, self.request.digest, start, start + len(word), MaskType.PERSON),
            "R1",
        )
        result = tokenize_request(self.source, self.request, binding=self.binding,
                                  source_selections=self.selections, request_selections=(selected,))
        self.assertNotIn(word, result.request)
        self.assertEqual(result.restore(result.request, binding=self.binding, allowed=result.tokens), self.request.text)
        self.assertEqual(len(result.tokens), 3)

    def test_wrong_binding(self):
        result = self.tokenize()
        for field in ("session_id", "request_id", "source_digest", "request_digest"):
            with self.subTest(field=field), self.assertRaises(InvalidToken):
                result.restore(result.source, binding=replace(self.binding, **{field: "other"}), allowed=result.tokens)

    def test_same_session_other_request_has_foreign_tokens(self):
        result = self.tokenize()
        foreign = self.tokenize(replace(self.binding, request_id="other"))
        self.assertTrue(result.tokens.isdisjoint(foreign.tokens))
        with self.assertRaises(InvalidToken):
            result.restore(next(iter(foreign.tokens)), binding=self.binding, allowed=result.tokens)

    def test_unapproved_and_corrupt_token_rejected(self):
        result = self.tokenize()
        token = next(iter(result.tokens))
        with self.assertRaises(InvalidToken):
            result.restore(token, binding=self.binding, allowed=frozenset())
        unknown = token[:-8] + "999999]]"
        for bad in (token[:-1], token[2:], unknown):
            with self.subTest(bad=bad), self.assertRaises(InvalidToken):
                result.restore(token + " " + bad, binding=self.binding, allowed=result.tokens)

    def test_reserved_marker_in_input(self):
        source = CanonicalText(Section.SOURCE, "Szöveg [[A:maradvány")
        with self.assertRaises(InvalidToken):
            tokenize_request(source, self.request, binding=replace(self.binding, source_digest=source.digest))

    def test_reuses_selection_validation(self):
        bad = replace(self.selections[0], span=replace(self.selections[0].span, digest="stale"))
        with self.assertRaises(InvalidSelection):
            tokenize_request(self.source, self.request, binding=self.binding, source_selections=(bad,))

    def test_missing_referent_is_rejected(self):
        with self.assertRaises(InvalidToken):
            tokenize_request(self.source, self.request, binding=self.binding,
                             source_selections=(replace(self.selections[0], referent=" "),))
