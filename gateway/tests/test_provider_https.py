# Károly Tamás szakdolgozati prototípusa (2026).
from dataclasses import replace
import http.client
import socket
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from gateway.operator_config import ModelProfile
from gateway.provider_https import ProviderHTTPS, TransportBlocked, _PinnedHTTPSConnection, _resolve


class ProviderHTTPSTests(unittest.TestCase):
    profile = ModelProfile(
        "profile", "p1", "connection", "c1", "teszt", "summary", "openai-responses",
        "https://model.example.invalid/v1", "synthetic-model", "api-key", "", 800, 30,
        None, None, "text",
    )
    key = "synthetic-test-key"
    body = b'{"input":"protected"}'
    address = ("8.8.8.8", 443)

    def transport(self, profile=None):
        profile = profile or self.profile
        return ProviderHTTPS(frozenset({(profile.protocol, profile.endpoint)}))

    def test_allowlist_and_private_dns_block_before_connection(self):
        with patch("gateway.provider_https._resolve") as resolver:
            with self.assertRaises(TransportBlocked):
                ProviderHTTPS().send(self.profile, self.body, api_key=self.key)
            resolver.assert_not_called()
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", self.address),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("gateway.provider_https.socket.getaddrinfo", return_value=records):
            with patch("gateway.provider_https._PinnedHTTPSConnection") as connection:
                with self.assertRaisesRegex(TransportBlocked, "tiltott"):
                    self.transport().send(self.profile, self.body, api_key=self.key)
                connection.assert_not_called()

    def test_exact_body_header_and_pinned_address_for_each_protocol(self):
        for protocol, suffix in (
            ("openai-responses", "/responses"),
            ("openai-chat-compatible", "/chat/completions"),
            ("anthropic-messages", "/messages"),
        ):
            with self.subTest(protocol=protocol):
                profile = replace(self.profile, protocol=protocol)
                with patch("gateway.provider_https._resolve", return_value=(socket.AF_INET, self.address)):
                    with patch("gateway.provider_https._PinnedHTTPSConnection") as constructor:
                        connection = constructor.return_value
                        response = connection.getresponse.return_value
                        response.status = 200
                        response.read.return_value = b'{"result":"safe"}'
                        self.assertEqual(self.transport(profile).send(
                            profile, self.body, api_key=self.key
                        ), b'{"result":"safe"}')
                        self.assertEqual(constructor.call_args.args[:4], (
                            "model.example.invalid", 443, socket.AF_INET, self.address,
                        ))
                        args, kwargs = connection.request.call_args
                        self.assertEqual(args, ("POST", "/v1" + suffix))
                        self.assertIs(kwargs["body"], self.body)
                        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + self.key)
                        self.assertEqual("anthropic-version" in kwargs["headers"], protocol == "anthropic-messages")
                        connection.finish.assert_called_once()

    def test_redirect_errors_oversize_and_echo_do_not_return_credentials(self):
        for status, reply in (
            (302, b'{"Location":"https://other.example.invalid"}'),
            (401, self.key.encode()),
            (200, b"x" * 256_001),
            (200, b'{"result":"synthetic-test-\\u006bey"}'),
        ):
            with self.subTest(status=status, size=len(reply)):
                with patch("gateway.provider_https._resolve", return_value=(socket.AF_INET, self.address)):
                    with patch("gateway.provider_https._PinnedHTTPSConnection") as constructor:
                        connection = constructor.return_value
                        response = connection.getresponse.return_value
                        response.status = status
                        response.read.return_value = reply
                        with self.assertRaises(TransportBlocked) as error:
                            self.transport().send(self.profile, self.body, api_key=self.key)
                        self.assertNotIn(self.key, str(error.exception))
                        connection.request.assert_called_once()
                        if status != 200:
                            response.read.assert_not_called()
        with patch("gateway.provider_https._resolve") as resolver:
            with self.assertRaises(TransportBlocked):
                self.transport().send(self.profile, b'{"input":"synthetic-test-\\u006bey"}', api_key=self.key)
            resolver.assert_not_called()

    def test_timeout_and_wire_errors_hide_details(self):
        with patch("gateway.provider_https._resolve", side_effect=TimeoutError(self.key)):
            with self.assertRaisesRegex(TransportBlocked, "időkorlát") as error:
                self.transport().send(self.profile, self.body, api_key=self.key)
            self.assertNotIn(self.key, str(error.exception))
        with patch("gateway.provider_https._resolve", return_value=(socket.AF_INET, self.address)):
            with patch("gateway.provider_https._PinnedHTTPSConnection") as constructor:
                constructor.return_value.request.side_effect = http.client.HTTPException(self.key)
                with self.assertRaises(TransportBlocked) as error:
                    self.transport().send(self.profile, self.body, api_key=self.key)
                self.assertNotIn(self.key, str(error.exception))

    def test_socket_uses_numeric_address_and_tls_uses_dns_name(self):
        with patch("gateway.provider_https.socket.socket") as socket_factory:
            with patch("gateway.provider_https.ssl.create_default_context") as tls:
                with patch("gateway.provider_https.Timer"):
                    connection = _PinnedHTTPSConnection(
                        "model.example.invalid", 443, socket.AF_INET, self.address, monotonic() + 10
                    )
                    connection.connect()
                    raw = socket_factory.return_value
                    raw.connect.assert_called_once_with(self.address)
                    tls.return_value.wrap_socket.assert_called_once_with(raw, server_hostname="model.example.invalid")
                    connection.finish()
