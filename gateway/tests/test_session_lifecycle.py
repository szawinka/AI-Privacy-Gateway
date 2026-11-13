# Károly Tamás szakdolgozati prototípusa (2026).
from dataclasses import replace
import unittest

from gateway.session_lifecycle import SessionStore, SessionUnavailable
from gateway.text_processing import CanonicalText, MaskType, Section, Selection
from gateway.token_mapping import InvalidToken, PersonSelection


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = SessionStore(clock=lambda: self.now)
        self.session = self.store.open_session()
        self.source = CanonicalText(Section.SOURCE, "Teszt Luca")
        self.request = CanonicalText(Section.REQUEST, "Foglald össze.")
        self.selection = PersonSelection(
            Selection(Section.SOURCE, self.source.digest, 0, len(self.source.text), MaskType.PERSON),
            "R1",
        )
        self.prepared = self.prepare()

    def prepare(self):
        return self.store.prepare(
            self.session, self.source, self.request, source_selections=(self.selection,),
        )

    def restore(self):
        item = self.prepared
        return self.store.restore(self.session, item.binding, item.source, allowed=item.tokens)

    def test_inactivity_boundary_and_removal(self):
        self.now = 900
        with self.assertRaises(SessionUnavailable):
            self.restore()
        self.assertEqual(self.store.sweep(), 0)  # Already removed on lookup.
        with self.assertRaises(SessionUnavailable):
            self.prepare()

    def test_successful_use_renews_idle_but_not_maximum(self):
        for moment in (899, 1798, 2697, 3596, 3599):
            self.now = moment
            self.assertEqual(self.restore(), self.source.text)
        self.now = 3600
        with self.assertRaises(SessionUnavailable):
            self.restore()

    def test_invalid_restore_does_not_refresh_idle(self):
        self.now = 899
        with self.assertRaises(InvalidToken):
            self.store.restore(self.session, self.prepared.binding, self.prepared.source, allowed=frozenset())
        self.now = 900
        with self.assertRaises(SessionUnavailable):
            self.restore()

    def test_request_close_keeps_sibling(self):
        sibling = self.prepare()
        self.store.close_request(self.session, self.prepared.binding)
        with self.assertRaises(SessionUnavailable):
            self.restore()
        self.assertEqual(
            self.store.restore(self.session, sibling.binding, sibling.source, allowed=sibling.tokens),
            self.source.text,
        )

    def test_explicit_session_close_and_fresh_store(self):
        self.store.close_session(self.session)
        self.store.close_session(self.session)
        with self.assertRaises(SessionUnavailable):
            self.restore()
        self.store = SessionStore(clock=lambda: self.now)
        with self.assertRaises(SessionUnavailable):
            self.restore()

    def test_other_session_and_version_rejected(self):
        other = self.store.open_session()
        for session, binding in (
            (other, self.prepared.binding),
            (self.session, replace(self.prepared.binding, source_digest="stale")),
        ):
            with self.subTest(session=session, binding=binding), self.assertRaises(SessionUnavailable):
                self.store.restore(session, binding, self.prepared.source, allowed=self.prepared.tokens)

    def test_sweep_drops_expired_sessions_without_touching_live_ones(self):
        self.now = 899
        fresh = self.store.open_session()
        self.now = 900
        self.assertEqual(self.store.sweep(), 1)
        with self.assertRaises(SessionUnavailable):
            self.restore()
        self.store.prepare(fresh, self.source, self.request)
        self.assertEqual(self.store.sweep(), 0)
