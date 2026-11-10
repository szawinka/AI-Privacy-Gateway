from hashlib import sha256
import unittest

from gateway.text_processing import (
    CanonicalText,
    InvalidSelection,
    MaskType,
    Section,
    Selection,
    mask_request,
)


def span(section, start, end, kind=MaskType.PERSON):
    return Selection(section.section, section.digest, start, end, kind)


class TextProcessingTests(unittest.TestCase):
    def test_document_repeated_names(self):
        source = CanonicalText(
            Section.SOURCE,
            "A Teszt Műhely próbáján Teszt Luca átvette a SYN-17 mintadobozt. "
            "Teszt Luca péntekre ígért állapotjelentést. "
            "A vendégként szereplő másik Teszt Luca csak megfigyelő volt.",
        )
        request = CanonicalText(
            Section.REQUEST,
            "Foglald össze két mondatban, mi történt és mi a következő lépés.",
        )
        name = "Teszt Luca"
        starts = [i for i in range(len(source.text)) if source.text.startswith(name, i)]
        self.assertEqual(len(starts), 3)
        selections = tuple(span(source, i, i + len(name)) for i in reversed(starts))
        self.assertEqual(
            mask_request(source, request, source_selections=selections),
            (
                "A Teszt Műhely próbáján [SZEMÉLY] átvette a SYN-17 mintadobozt. "
                "[SZEMÉLY] péntekre ígért állapotjelentést. "
                "A vendégként szereplő másik [SZEMÉLY] csak megfigyelő volt.",
                request.text,
            ),
        )

    def test_mail_masks_each_section_including_suffix(self):
        source = CanonicalText(
            Section.SOURCE,
            "Kedves csapat! A SYN-42 bemutató próbáját áttehetnénk csütörtökre? "
            "A tesztkapcsolat demo42@example.invalid. Üdv, Teszt Mira",
        )
        request = CanonicalText(
            Section.REQUEST,
            "Írj rövid választ Teszt Mirának: csütörtök megfelelő, pontos időpontot még ne ígérj.",
        )
        email, name, inflected = "demo42@example.invalid", "Teszt Mira", "Teszt Mirának"
        e, n, r = source.text.index(email), source.text.index(name), request.text.index(inflected)
        self.assertEqual(
            mask_request(
                source,
                request,
                source_selections=(span(source, e, e + len(email), MaskType.EMAIL),
                                   span(source, n, n + len(name))),
                request_selections=(span(request, r, r + len(inflected)),),
            ),
            (
                "Kedves csapat! A SYN-42 bemutató próbáját áttehetnénk csütörtökre? "
                "A tesztkapcsolat [E-MAIL]. Üdv, [SZEMÉLY]",
                "Írj rövid választ [SZEMÉLY]: csütörtök megfelelő, pontos időpontot még ne ígérj.",
            ),
        )

    def test_canonical_codepoint_offsets_and_hash(self):
        source = CanonicalText(Section.SOURCE, "\ufeff🎈A\u0301r\r\n  B\r")
        request = CanonicalText(Section.REQUEST, "  ő ")
        expected = "🎈Ár\n  B\n"
        self.assertEqual(source.text, expected)
        self.assertEqual(source.digest, sha256(expected.encode("utf-8")).hexdigest())
        self.assertEqual(
            mask_request(source, request, source_selections=(span(source, 1, 3),)),
            ("🎈[SZEMÉLY]\n  B\n", "  ő "),
        )

    def test_only_one_initial_bom_removed(self):
        self.assertEqual(CanonicalText(Section.SOURCE, "\ufeff\ufeffx").text, "\ufeffx")

    def test_invalid_bounds_rejected(self):
        source = CanonicalText(Section.SOURCE, "abcd")
        request = CanonicalText(Section.REQUEST, "")
        for start, end in ((-1, 2), (1, 1), (3, 2), (0, 5), (False, 2), (0, 2.0)):
            with self.subTest(start=start, end=end), self.assertRaises(InvalidSelection):
                mask_request(source, request, source_selections=(span(source, start, end),))

    def test_overlap_rejected_but_adjacent_allowed(self):
        source = CanonicalText(Section.SOURCE, "abcd")
        request = CanonicalText(Section.REQUEST, "")
        with self.assertRaises(InvalidSelection):
            mask_request(source, request, source_selections=(span(source, 0, 3), span(source, 2, 4)))
        self.assertEqual(
            mask_request(source, request, source_selections=(span(source, 0, 2), span(source, 2, 4))),
            ("[SZEMÉLY][SZEMÉLY]", ""),
        )

    def test_stale_selection_rejected_even_with_valid_offsets(self):
        source = CanonicalText(Section.SOURCE, "abcd")
        old_request = CanonicalText(Section.REQUEST, "old")
        new_request = CanonicalText(Section.REQUEST, "new")
        with self.assertRaises(InvalidSelection):
            mask_request(source, new_request, source_selections=(span(source, 0, 1),),
                         request_selections=(span(old_request, 0, 1),))

    def test_equal_text_does_not_allow_cross_section_selection(self):
        source = CanonicalText(Section.SOURCE, "same")
        request = CanonicalText(Section.REQUEST, "same")
        self.assertEqual(source.digest, request.digest)
        with self.assertRaises(InvalidSelection):
            mask_request(source, request, request_selections=(span(source, 0, 1),))

    def test_unknown_mask_type_rejected(self):
        source = CanonicalText(Section.SOURCE, "abcd")
        request = CanonicalText(Section.REQUEST, "")
        with self.assertRaises(InvalidSelection):
            mask_request(source, request, source_selections=(span(source, 0, 2, "<b>"),))
