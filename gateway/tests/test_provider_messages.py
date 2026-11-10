from dataclasses import replace
import json
import unittest

from gateway.operator_config import ModelProfile
from gateway.provider_messages import ProviderMessageError, json_bytes, local_response, request_body, response_text


class ProviderMessagesTests(unittest.TestCase):
    profile = ModelProfile(
        "profile", "p1", "connection", "c1", "teszt", "summary", "openai-responses",
        "https://model.example.invalid/v1", "synthetic-model", "api-key", "", 800, 30,
        None, None, "text",
    )

    def test_request_mapping_and_round_trip_fixture_for_each_protocol(self):
        for protocol, token_field in (
            ("openai-responses", "max_output_tokens"),
            ("openai-chat-compatible", "max_completion_tokens"),
            ("anthropic-messages", "max_tokens"),
        ):
            with self.subTest(protocol=protocol):
                profile = replace(self.profile, protocol=protocol)
                wire = request_body(profile, "[SZEMÉLY]", "Foglald össze.")
                body = json.loads(wire)
                self.assertEqual(body[token_field], 800)
                self.assertFalse(body["stream"])
                self.assertNotIn("temperature", body)
                content = body["input"] if "input" in body else body["messages"][-1]["content"]
                self.assertEqual(json.loads(content), {"source": "[SZEMÉLY]", "request": "Foglald össze."})
                if protocol != "anthropic-messages":
                    self.assertFalse(body["store"])
                self.assertIn("helyi", response_text(protocol, local_response(protocol, wire)))

    def test_invalid_replies_fail_closed(self):
        for protocol, reply in (
            ("openai-responses", {"status": "incomplete", "output": []}),
            ("openai-responses", {"status": "completed", "output": [{"type": "function_call"}]}),
            ("openai-chat-compatible", {"choices": [{"finish_reason": "length"}]}),
            ("anthropic-messages", {"role": "assistant", "stop_reason": "end_turn",
                                    "stop_details": {"type": "refusal"},
                                    "content": [{"type": "text", "text": "refusal"}]}),
        ):
            with self.subTest(protocol=protocol), self.assertRaises(ProviderMessageError):
                response_text(protocol, json_bytes(reply))
        with self.assertRaises(ProviderMessageError):
            response_text("anthropic-messages", b"x" * 256_001)
