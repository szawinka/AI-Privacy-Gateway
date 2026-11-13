# Károly Tamás szakdolgozati prototípusa (2026).
import json
import unittest

from gateway.own_input import OwnInput
from gateway.session_lifecycle import SessionStore, SessionUnavailable
from gateway.token_mapping import InvalidToken


class OwnInputTokenTests(unittest.TestCase):
    def test_email_and_generic_tokens_restore_only_for_live_bound_request(self):
        draft = OwnInput.from_fields("😀 a@example.invalid", "Kód ABC-42.")
        source, request = draft.token_selections(json.dumps([
            {"section": "source", "kind": "EMAIL", "start": 2, "end": 19},
            {"section": "request", "kind": "SENSITIVE", "start": 4, "end": 10},
        ]))
        sessions = SessionStore()
        owner = sessions.open_session()
        prepared = sessions.prepare(
            owner, draft.source, draft.request, source_selections=source,
            request_selections=request, check_residue=True,
        )
        email = next(token for token in prepared.tokens if ":EMAIL:" in token)
        generic = next(token for token in prepared.tokens if ":ERZEKENY:" in token)
        response = f"{email} / {generic}"
        self.assertEqual(
            sessions.restore(owner, prepared.binding, response, allowed=prepared.tokens),
            "a@example.invalid / ABC-42",
        )
        with self.assertRaises(InvalidToken):
            sessions.restore(owner, prepared.binding, response, allowed=frozenset({email}))
        with self.assertRaises(InvalidToken):
            sessions.restore(owner, prepared.binding, email[:-1], allowed=prepared.tokens)
        with self.assertRaises(SessionUnavailable):
            sessions.restore(sessions.open_session(), prepared.binding, response, allowed=prepared.tokens)
        sessions.close_request(owner, prepared.binding)
        with self.assertRaises(SessionUnavailable):
            sessions.restore(owner, prepared.binding, response, allowed=prepared.tokens)
