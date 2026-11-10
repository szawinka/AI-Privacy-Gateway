import asyncio
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlencode, urlsplit

from gateway.web_demo import create_app


class WebDemoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.root = Path(folder)
        self.app = create_app(
            self.root / "audit.sqlite3", "test-owner", "test-reviewer", operator_code="test-operator"
        )

    async def request(self, method, path, *, cookie="", data=None, origin=None, host="127.0.0.1:8765"):
        headers = [(b"host", host.encode())]
        if method == "POST":
            headers.extend([
                (b"origin", (origin or f"http://{host}").encode()),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ])
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        target = urlsplit(path)
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": target.path,
            "query_string": target.query.encode(), "headers": headers, "server": ("127.0.0.1", 8765),
        }
        received = False
        events = []

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": urlencode(data or {}).encode()}
            await asyncio.Future()

        async def send(event):
            events.append(event)

        await self.app(scope, receive, send)
        start = events[0]
        body = b"".join(event.get("body", b"") for event in events[1:]).decode()
        return start["status"], dict(start["headers"]), body

    async def login(self, code):
        status, headers, _ = await self.request("POST", "/login", data={"code": code, "username": "kero" if code == "test-owner" else "ellenor"})
        self.assertEqual(status, 303)
        cookie = headers[b"set-cookie"].decode()
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        return cookie.split(";", 1)[0]

    async def start(self, owner, task):
        status, headers, _ = await self.request("POST", "/cases", cookie=owner, data={"task": task})
        self.assertEqual(status, 303)
        return headers[b"location"].decode()

    @staticmethod
    def approval(html):
        return {
            "version": "1", "outcome": "approved",
            "digest": re.search(r'name="digest" value="([a-f0-9]{64})"', html)[1],
        }

    async def test_both_demo_paths_with_separate_cookie_sessions(self):
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        self.assertNotEqual(owner, reviewer)
        doc = await self.start(owner, "A-DOC-01")
        status, headers, html = await self.request("GET", doc, cookie=owner)
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertNotIn("Teszt Luca", html)
        self.assertEqual((await self.request(
            "POST", doc + "/decision", cookie=owner, data=self.approval(html)
        ))[0], 200)
        status, _, html = await self.request("POST", doc + "/send", cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn("Teszt Luca átvette", html)
        self.assertEqual((await self.request("POST", doc + "/send", cookie=owner))[0], 403)

        mail = await self.start(owner, "A-MAIL-01")
        status, _, html = await self.request("GET", mail, cookie=reviewer)
        self.assertEqual(status, 200)
        self.assertIn("[E-MAIL]", html)
        self.assertNotIn("Teszt Mira", html)
        self.assertNotIn("demo42@example.invalid", html)
        self.assertNotIn("Helyi futtatás", html)
        decision = self.approval(html)
        for method, route, cookie, fields in (
            ("POST", mail + "/decision", owner, decision),
            ("GET", mail, await self.login("test-reviewer"), None),
            ("POST", mail + "/send", reviewer, None),
        ):
            self.assertEqual((await self.request(method, route, cookie=cookie, data=fields))[0], 403)
        self.assertEqual((await self.request(
            "POST", mail + "/decision", cookie=reviewer, data=decision
        ))[0], 200)
        status, _, html = await self.request("POST", mail + "/send", cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn("Csütörtök megfelelő", html)

    async def test_login_origin_host_and_logout(self):
        self.assertEqual((await self.request(
            "POST", "/login", data={"code": "wrong"}
        ))[0], 403)
        self.assertEqual((await self.request(
            "POST", "/login", data={"code": "test-owner"}, origin="http://example.invalid"
        ))[0], 403)
        self.assertEqual((await self.request("GET", "/", host="example.invalid"))[0], 400)
        owner = await self.login("test-owner")
        self.assertEqual((await self.request(
            "POST", "/cases", cookie=owner, data={"task": "A-MAIL-01"}
        ))[0], 409)
        doc = await self.start(owner, "A-DOC-01")
        self.assertEqual((await self.request("POST", "/logout", cookie=owner))[0], 303)
        self.assertEqual((await self.request("GET", doc, cookie=owner))[0], 401)


    async def test_own_input_masks_unicode_spans_and_escapes_html(self):
        owner = await self.login("test-owner")
        source = "😀 Teszt Mira <script>"
        request = "Írj Teszt Mirának."
        status, headers, html = await self.request(
            "POST", "/own-input", cookie=owner, data={"source": source, "request": request}
        )
        self.assertEqual(status, 200)
        self.assertIn(b"script-src 'self'", headers[b"content-security-policy"])
        self.assertIn("&lt;script&gt;", html)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', html)[1]
        selections = [
            {"section": "source", "kind": "PERSON", "start": 2, "end": 12},
            {"section": "request", "kind": "PERSON", "start": 4, "end": 17},
        ]
        fields = {"source": source, "request": request, "stamp": stamp,
                  "selections": json.dumps(selections)}
        status, _, html = await self.request("POST", "/own-input/preview", cookie=owner, data=fields)
        self.assertEqual(status, 200)
        self.assertEqual(html.count("[SZEMÉLY]"), 2)
        self.assertNotIn("Teszt Mira", html)
        self.assertIn("&lt;script&gt;", html)

        changed = dict(fields, source="x" + source)
        self.assertEqual((await self.request(
            "POST", "/own-input/preview", cookie=owner, data=changed
        ))[0], 400)
        other_owner = await self.login("test-owner")
        self.assertEqual((await self.request(
            "POST", "/own-input/preview", cookie=other_owner, data=fields
        ))[0], 400)
        reviewer = await self.login("test-reviewer")
        self.assertEqual((await self.request("GET", "/own-input", cookie=reviewer))[0], 403)

    async def test_own_input_blocks_unmasked_repetitions_and_overlaps(self):
        owner = await self.login("test-owner")
        source = "Teszt Luca és Teszt Luca"
        request = "Foglald össze."
        _, _, html = await self.request(
            "POST", "/own-input", cookie=owner, data={"source": source, "request": request}
        )
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', html)[1]
        first = {"section": "source", "kind": "PERSON", "start": 0, "end": 10}
        for selections in ([first], [first, dict(first, start=1)]):
            status, _, html = await self.request(
                "POST", "/own-input/preview", cookie=owner,
                data={"source": source, "request": request, "stamp": stamp,
                      "selections": json.dumps(selections)},
            )
            self.assertEqual(status, 400)
            self.assertNotIn("Teszt Luca", html)

    async def test_recognizer_setup_displays_installation_status_to_requester(self):
        from unittest.mock import patch

        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        for ready in (False, True):
            with patch("gateway.web_demo.nlp_installed", return_value=ready), patch("gateway.web_demo.nlp_active", return_value=ready):
                status, _, html = await self.request("GET", "/recognizer-setup", cookie=owner)
                self.assertEqual(status, 200)
                self.assertNotIn("Telepítés indítása", html)
                self.assertEqual("fordulj az Üzemeltetőhöz" in html, not ready)
                self.assertIn("telepítve van" if ready else "nincs telepítve", html)
        self.assertEqual((await self.request("GET", "/recognizer-setup", cookie=reviewer))[0], 403)

    async def test_recognizer_install_requires_operator_and_consent(self):
        from unittest.mock import patch

        path = "/operator/recognizer"
        owner = await self.login("test-owner")
        status, headers, _ = await self.request(
            "POST", "/operator/login", data={"username": "uzemelteto", "code": "test-operator"},
        )
        self.assertEqual(status, 303)
        operator = headers[b"set-cookie"].decode().split(";", 1)[0]
        with patch("gateway.operator_web.installed", return_value=False), patch("gateway.operator_web.install") as install:
            self.assertEqual((await self.request("GET", path, cookie=owner))[0], 401)
            self.assertEqual((await self.request("POST", path, cookie=owner, data={"consent": "yes"}))[0], 401)
            self.assertIn("Telepítés indítása", (await self.request("GET", path, cookie=operator))[2])
            self.assertEqual((await self.request("POST", path, cookie=operator))[0], 400)
            install.assert_not_called()
            self.assertEqual((await self.request("POST", path, cookie=operator, data={"consent": "yes"}))[0], 303)
            install.assert_called_once_with()
        with (
            patch("gateway.operator_web.installed", return_value=True),
            patch("gateway.operator_web.active", return_value=True),
            patch("gateway.operator_web.install") as install,
            patch("gateway.operator_web.remove") as remove,
            patch("gateway.operator_web.set_active") as set_active,
        ):
            _, _, html = await self.request("GET", path, cookie=operator)
            for text in ("hu_core_news_md", "Kikapcsolás", "Újratelepítés", "Modell eltávolítása"):
                self.assertIn(text, html)
            for operation in ("reinstall", "remove"):
                self.assertEqual((await self.request("POST", path, cookie=operator, data={"operation": operation}))[0], 400)
            install.assert_not_called()
            remove.assert_not_called()
            for operation in ("disable", "reinstall", "remove"):
                self.assertEqual((await self.request(
                    "POST", path, cookie=operator, data={"operation": operation, "consent": "yes"},
                ))[0], 303)
            set_active.assert_called_once_with(False)
            install.assert_called_once_with(replace=True)
            remove.assert_called_once_with()


    async def test_inbox_lists_only_assigned_cases_and_tracks_progress(self):
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        case = await self.start(owner, "A-MAIL-01")
        for cookie in (owner, reviewer):
            _, _, html = await self.request("GET", "/", cookie=cookie)
            self.assertIn(case, html)
            self.assertIn("Döntésre vár", html)
            self.assertNotIn("Teszt Mira", html)
        for code in ("test-owner", "test-reviewer"):
            _, _, html = await self.request("GET", "/", cookie=await self.login(code))
            self.assertNotIn(case, html)
        _, _, preview = await self.request("GET", case, cookie=reviewer)
        self.assertEqual((await self.request(
            "POST", case + "/decision", cookie=reviewer, data=self.approval(preview),
        ))[0], 200)
        self.assertIn("Futtatás megnyitása", (await self.request("GET", "/", cookie=owner))[2])
        self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 200)
        self.assertIn("Futtatás befejezve", (await self.request("GET", "/", cookie=owner))[2])

    async def test_name_suggestions_seed_preview_and_manual_fallback(self):
        from html import unescape
        from unittest.mock import patch

        owner = await self.login("test-owner")
        fields = {"source": "😀 Kovács Anna dolgozik.", "request": "Írj Kovács Annának."}
        proposals = [
            {"section": "source", "kind": "PERSON", "start": 2, "end": 13},
            {"section": "request", "kind": "PERSON", "start": 4, "end": 18},
        ]
        with patch("gateway.own_input_web.suggest_names", return_value=proposals):
            status, _, html = await self.request("POST", "/own-input", cookie=owner, data=fields)
        self.assertEqual(status, 200)
        self.assertIn("Automatikus névjavaslatok: 2", html)
        selections = unescape(re.search(r'name="selections" value="([^"]*)"', html)[1])
        self.assertEqual(json.loads(selections), proposals)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', html)[1]
        status, _, html = await self.request(
            "POST", "/own-input/preview", cookie=owner,
            data=dict(fields, stamp=stamp, selections=selections),
        )
        self.assertEqual(status, 200)
        self.assertEqual(html.count("[SZEMÉLY]"), 2)
        for effect, note in (
            (None, "nincs telepítve"),
            (RuntimeError("private detail"), "futása sikertelen"),
        ):
            with self.subTest(note=note), patch(
                "gateway.own_input_web.suggest_names",
                return_value=None, side_effect=effect,
            ):
                status, _, html = await self.request("POST", "/own-input", cookie=owner, data=fields)
                self.assertEqual(status, 200)
                self.assertIn(note, html)
                self.assertIn('name="selections" value="[]"', html)
                self.assertIn("Személy megjelölése", html)
                self.assertNotIn("private detail", html)

    async def test_sensitive_dictionary_save_search_reuse_edit_and_delete(self):
        from html import unescape
        from unittest.mock import patch

        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        value = "BELSŐ-42"
        fields = {"source": f"Kód: {value}", "request": "Foglald össze."}
        selection = {"section": "source", "kind": "SENSITIVE", "start": 5, "end": 13}
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            status, _, page = await self.request("POST", "/own-input", cookie=owner, data=fields)
        self.assertEqual(status, 200)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]
        payload = dict(fields, stamp=stamp, selections=json.dumps([selection]))
        self.assertEqual((await self.request("GET", "/sensitive-values", cookie=reviewer))[0], 403)
        self.assertEqual((await self.request(
            "POST", "/own-input/remember", cookie=owner, data=dict(payload, stamp="invalid")
        ))[0], 400)
        self.assertEqual((await self.request(
            "POST", "/own-input/remember", cookie=owner, data=payload
        ))[0], 200)
        self.assertEqual((await self.request(
            "POST", "/own-input/remember", cookie=owner, data=payload
        ))[0], 400)

        _, _, page = await self.request(
            "POST", "/sensitive-values/search", cookie=owner, data={"query": "belső"}
        )
        self.assertIn(value, page)
        route = re.search(r'action="(/sensitive-values/[0-9]+)"', page)[1]
        _, _, page = await self.request(
            "POST", "/sensitive-values/search", cookie=owner, data={"query": "egyéb"}
        )
        self.assertNotIn(value, page)

        with patch("gateway.own_input_web.suggest_names", return_value=None):
            _, _, page = await self.request("POST", "/own-input", cookie=owner, data=fields)
        self.assertEqual(json.loads(unescape(
            re.search(r'name="selections" value="([^"]*)"', page)[1]
        )), [selection])
        status, _, page = await self.request(
            "POST", "/own-input/preview", cookie=owner,
            data=dict(payload, stamp=re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]),
        )
        self.assertEqual(status, 200)
        self.assertIn("[ÉRZÉKENY]", page)
        self.assertNotIn(value, page)

        status, _, page = await self.request(
            "POST", route, cookie=owner, data={"value": "<bizalmas>", "kind": "SENSITIVE"}
        )
        self.assertEqual(status, 200)
        self.assertIn("&lt;bizalmas&gt;", page)
        self.assertNotIn(value, page)
        status, _, page = await self.request("POST", route + "/delete", cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn("Találatok: 0", page)

    async def test_own_token_preview_groups_explicit_matches_and_blocks_residue(self):
        from unittest.mock import patch

        owner = await self.login("test-owner")
        fields = {"source": "BELSŐ és BELSŐ és BELSŐ", "request": "Kérés."}
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            status, _, html = await self.request("POST", "/own-input", cookie=owner, data=fields)
        self.assertEqual(status, 200)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', html)[1]
        selections = [
            {"section": "source", "kind": "SENSITIVE", "start": start, "end": start + 5,
             "group": group}
            for start, group in ((0, "R1"), (9, "R1"), (18, ""))
        ]
        endpoint = "/own-input/token-preview"
        for chosen, expected in ((selections[:2], 400), (selections, 200)):
            status, _, html = await self.request(
                "POST", endpoint, cookie=owner,
                data=dict(fields, stamp=stamp, selections=json.dumps(chosen)),
            )
            self.assertEqual(status, expected)
            self.assertNotIn("BELSŐ", html)
        tokens = re.findall(r"\[\[A:ERZEKENY:[a-f0-9]{32}:[0-9]{6}\]\]", html)
        self.assertEqual(len(tokens), 3)
        self.assertEqual(tokens[0], tokens[1])
        self.assertNotEqual(tokens[0], tokens[2])


    async def test_own_text_requires_assigned_approval_and_only_runs_once(self):
        from unittest.mock import patch

        owner = await self.login("test-owner")
        fields = {"source": "BELSŐ-42 egyeztetés", "request": "Foglald össze."}
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            _, _, page = await self.request("POST", "/own-input", cookie=owner, data=fields)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]
        payload = dict(fields, stamp=stamp, selections=json.dumps([
            {"section": "source", "kind": "SENSITIVE", "start": 0, "end": 8},
        ]))
        self.assertEqual((await self.request(
            "POST", "/own-input/token-preview?review=1", cookie=owner, data=payload
        ))[0], 409)
        reviewer = await self.login("test-reviewer")
        for route, marker in (("preview", "[ÉRZÉKENY]"), ("token-preview", "[[A:ERZEKENY:")):
            with self.subTest(route=route):
                status, headers, _ = await self.request(
                    "POST", f"/own-input/{route}?review=1", cookie=owner, data=payload
                )
                self.assertEqual(status, 303)
                case = headers[b"location"].decode()
                status, _, page = await self.request("GET", case, cookie=reviewer)
                self.assertEqual(status, 200)
                self.assertIn(marker, page)
                self.assertNotIn("BELSŐ-42", page)
                self.assertNotIn("Helyi kapupróba", page)
                self.assertEqual((await self.request(
                    "POST", case + "/send", cookie=owner
                ))[0], 403)
                decision = self.approval(page)
                self.assertEqual((await self.request(
                    "POST", case + "/decision", cookie=owner, data=decision
                ))[0], 403)
                self.assertEqual((await self.request(
                    "POST", case + "/decision", cookie=reviewer, data=decision
                ))[0], 200)
                self.assertEqual((await self.request(
                    "POST", case + "/send", cookie=reviewer
                ))[0], 403)
                status, _, page = await self.request("POST", case + "/send", cookie=owner)
                self.assertEqual(status, 200)
                self.assertIn("A helyi kapupróba sikerült.", page)
                self.assertEqual((await self.request(
                    "POST", case + "/send", cookie=owner
                ))[0], 403)


    async def test_operator_roles_configuration_and_separate_secret_storage(self):
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        for cookie in (owner, reviewer):
            self.assertEqual((await self.request(
                "GET", "/operator/connections", cookie=cookie
            ))[0], 401)
        self.assertEqual((await self.request(
            "POST", "/operator/login", data={"code": "test-owner"}
        ))[0], 403)
        status, headers, _ = await self.request(
            "POST", "/operator/login", data={"code": "test-operator", "username": "uzemelteto"}
        )
        self.assertEqual(status, 303)
        cookie_header = headers[b"set-cookie"].decode()
        self.assertIn("Path=/operator", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        operator = cookie_header.split(";", 1)[0]
        self.assertEqual((await self.request("GET", "/own-input", cookie=operator))[0], 401)

        route = "/operator/connections"
        key = "test-only-key-0123456789"
        connection = {
            "name": "Tesztkapcsolat", "protocol": "openai-responses",
            "endpoint": "https://models.example.invalid/v1", "model": "synthetic-model",
            "auth": "api-key", "api_key": key, "enabled": "yes",
        }
        self.assertEqual((await self.request("POST", route, cookie=operator, data=connection))[0], 303)
        status, _, page = await self.request("GET", route, cookie=operator)
        self.assertEqual(status, 200)
        self.assertIn("Mentett API-kulcs: van", page)
        self.assertNotIn(key, page)
        self.assertNotIn(key.encode(), (self.root / "operator/profiles.sqlite3").read_bytes())
        self.assertTrue((self.root / "operator/secrets/credentials.sqlite3").is_file())
        connection_route = re.search(r'action="(/operator/connections/[a-f0-9]{32})"', page)[1]
        self.assertEqual((await self.request(
            "POST", connection_route, cookie=operator,
            data=dict(connection, endpoint="https://other.example.invalid/v1", api_key=""),
        ))[0], 400)
        self.assertEqual((await self.request(
            "POST", connection_route, cookie=operator, data=dict(connection, model="new-model", api_key="")
        ))[0], 303)

        profiles = "/operator/profiles"
        profile = {"name": "Összefoglalás", "task": "summary",
                   "connection_id": connection_route.rsplit("/", 1)[1],
                   "max_tokens": "800", "timeout_seconds": "30", "temperature": "0.3"}
        self.assertEqual((await self.request("POST", profiles, cookie=operator, data=profile))[0], 303)
        _, _, page = await self.request("GET", profiles, cookie=operator)
        profile_route = re.search(r'action="(/operator/profiles/[a-f0-9]{32})"', page)[1]
        self.assertEqual((await self.request(
            "POST", profile_route, cookie=operator, data=dict(profile, top_p="0.5")
        ))[0], 400)
        self.assertEqual((await self.request(
            "POST", profile_route, cookie=operator, data=dict(profile, max_tokens="900")
        ))[0], 303)
        self.assertEqual((await self.request("POST", connection_route + "/delete", cookie=operator))[0], 400)
        self.assertEqual((await self.request("POST", profile_route + "/delete", cookie=operator))[0], 303)
        self.assertEqual((await self.request(
            "POST", connection_route, cookie=operator,
            data=dict(connection, api_key="", enabled="", remove_key="yes"),
        ))[0], 303)
        _, _, page = await self.request("GET", route, cookie=operator)
        self.assertNotIn("Mentett API-kulcs: van", page)
        self.assertEqual((await self.request("POST", connection_route + "/delete", cookie=operator))[0], 303)
        status, headers, _ = await self.request("POST", "/operator/logout", cookie=operator)
        self.assertEqual(status, 303)
        self.assertEqual(headers[b"location"], b"/")
        self.assertEqual((await self.request("GET", profiles, cookie=operator))[0], 401)

    async def test_configured_case_freezes_profile_and_rejects_revised_settings(self):
        from html import unescape
        from unittest.mock import patch

        from gateway.operator_config import OperatorConfig
        from gateway.provider_messages import json_bytes

        config = OperatorConfig(self.root / "operator")
        connection_fields = {
            "name": "Külső modell teszt", "protocol": "anthropic-messages",
            "endpoint": "https://model.example.invalid/v1", "model": "synthetic-model",
            "auth": "api-key", "api_key": "synthetic-key-never-in-preview", "enabled": "yes",
        }
        connection_id = config.save_connection(connection_fields)
        profile_fields = {
            "name": "Levélprofil", "task": "mail-draft", "connection_id": connection_id,
            "max_tokens": "800", "timeout_seconds": "30",
        }
        profile_id = config.save_profile(profile_fields)
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        fields = {"source": "BELSŐ-42 egyeztetés", "request": "Írj rövid választ."}
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            status, _, page = await self.request("POST", "/own-input", cookie=owner, data=fields)
        self.assertEqual(status, 200)
        self.assertIn("Levélprofil", page)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]
        data = dict(fields, stamp=stamp, profile_id=profile_id, selections=json.dumps([
            {"section": "source", "kind": "SENSITIVE", "start": 0, "end": 8},
        ]))

        async def create_and_approve():
            status, headers, _ = await self.request(
                "POST", "/own-input/token-preview?review=1", cookie=owner, data=data
            )
            self.assertEqual(status, 303)
            case = headers[b"location"].decode()
            _, _, page = await self.request("GET", case, cookie=reviewer)
            self.assertNotIn(connection_fields["api_key"], page)
            self.assertNotIn("BELSŐ-42", page)
            preview = unescape(re.search(r"<pre>(.*?)</pre>", page, re.S)[1]).encode("utf-8")
            status, _, _ = await self.request(
                "POST", case + "/decision", cookie=reviewer, data=self.approval(page)
            )
            self.assertEqual(status, 200)
            wire = unescape(re.search(r'<pre id="provider-body">(.*?)</pre>', page, re.S)[1]).encode("utf-8")
            return case, preview, wire

        case, preview, wire = await create_and_approve()
        frozen = json.loads(preview)
        self.assertEqual(frozen["task_template"], "own-mail-draft-v1")
        self.assertEqual(frozen["model_profile"]["model"], "synthetic-model")
        self.assertEqual(frozen["model_profile"]["max_tokens"], 800)
        from hashlib import sha256

        self.assertEqual(frozen["provider_request_sha256"], sha256(wire).hexdigest())
        self.assertEqual(json.loads(wire)["max_tokens"], 800)
        token = re.search(r"\[\[A:ERZEKENY:[^]]+\]\]", frozen["source"])[0]

        def reply(text):
            return json_bytes({"role": "assistant", "stop_reason": "end_turn",
                               "content": [{"type": "text", "text": text}]})

        with patch("gateway.demo_flow.local_response", return_value=reply(f"Válasz: {token}")) as adapter:
            status, _, page = await self.request("POST", case + "/send", cookie=owner)
            self.assertEqual(status, 200)
            self.assertIn("Válasz: BELSŐ-42", page)
            adapter.assert_called_once_with("anthropic-messages", wire)
        case, _, _ = await create_and_approve()
        with patch("gateway.demo_flow.local_response", return_value=reply(token[:-1])):
            status, _, page = await self.request("POST", case + "/send", cookie=owner)
            self.assertEqual(status, 403)
            self.assertNotIn("BELSŐ-42", page)

        for revision in ("profile", "key", "deleted"):
            with self.subTest(revision=revision):
                case, _, _ = await create_and_approve()
                if revision == "profile":
                    config.save_profile(dict(profile_fields, max_tokens="900"), profile_id)
                elif revision == "key":
                    config.save_connection(dict(connection_fields, api_key="replacement-key"), connection_id)
                else:
                    config.delete_profile(profile_id)
                with patch("gateway.demo_flow.local_response") as adapter:
                    status, _, _ = await self.request("POST", case + "/send", cookie=owner)
                    self.assertEqual(status, 403)
                    adapter.assert_not_called()

    async def test_opt_in_https_only_sends_approved_body_and_local_key_to_allowed_target(self):
        from contextlib import closing
        from html import unescape
        import socket
        import sqlite3
        from unittest.mock import patch

        from gateway.operator_config import OperatorConfig
        from gateway.provider_https import ProviderHTTPS

        endpoint = "https://model.example.invalid/v1"
        protocol = "openai-responses"
        secret = "test-secret-used-only-in-header"
        transport = ProviderHTTPS(frozenset({(protocol, endpoint)}))
        audit_path = self.root / "audit.sqlite3"
        self.app = create_app(audit_path, "test-owner", "test-reviewer", provider_https=transport)
        config = OperatorConfig(self.root / "operator")
        connection = {
            "name": "HTTPS teszt", "protocol": protocol, "endpoint": endpoint,
            "model": "synthetic-model", "auth": "api-key", "api_key": secret, "enabled": "yes",
        }
        connection_id = config.save_connection(connection)
        profile_fields = {
            "name": "Összefoglalás", "task": "summary", "connection_id": connection_id,
            "max_tokens": "800", "timeout_seconds": "30",
        }
        profile = config.save_profile(profile_fields)
        other_connection = config.save_connection(dict(connection, endpoint="https://other.example.invalid/v1"))
        denied_profile = config.save_profile(dict(profile_fields, connection_id=other_connection))
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        fields = {"source": "BELSŐ-42 egyeztetés", "request": "Foglald össze."}
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            _, _, page = await self.request("POST", "/own-input", cookie=owner, data=fields)
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]
        fields.update(stamp=stamp, selections=json.dumps([
            {"section": "source", "kind": "SENSITIVE", "start": 0, "end": 8},
        ]))
        route = "/own-input/token-preview?review=1"
        with patch("gateway.provider_https._resolve", return_value=(socket.AF_INET, ("8.8.8.8", 443))) as resolver:
            with patch("gateway.provider_https._PinnedHTTPSConnection") as constructor:
                status, _, _ = await self.request(
                    "POST", route, cookie=owner, data=dict(fields, profile_id=denied_profile)
                )
                self.assertEqual(status, 403)
                status, headers, _ = await self.request(
                    "POST", route, cookie=owner, data=dict(fields, profile_id=profile)
                )
                self.assertEqual(status, 303)
                case = headers[b"location"].decode()
                _, _, page = await self.request("GET", case, cookie=reviewer)
                self.assertIn("Külső HTTPS-küldés", page)
                self.assertNotIn(secret, page)
                self.assertNotIn("BELSŐ-42", page)
                wire = unescape(re.search(r'<pre id="provider-body">(.*?)</pre>', page, re.S)[1]).encode()
                self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 403)
                resolver.assert_not_called()
                self.assertEqual((await self.request(
                    "POST", case + "/decision", cookie=reviewer, data=self.approval(page)
                ))[0], 200)
                self.assertEqual((await self.request("POST", case + "/send", cookie=reviewer))[0], 403)
                token = re.search(rb"\[\[A:ERZEKENY:[^]]+\]\]", wire)[0].decode()
                simulated = constructor.return_value
                reply = simulated.getresponse.return_value
                reply.status = 200
                reply.read.return_value = json.dumps({"status": "completed", "output": [{
                    "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": f"Összefoglaló: {token}"}],
                }]}).encode()
                status, _, page = await self.request("POST", case + "/send", cookie=owner)
                self.assertEqual(status, 200)
                self.assertIn("Összefoglaló: BELSŐ-42", page)
                self.assertNotIn(secret, page)
                self.assertEqual(simulated.request.call_args.args, ("POST", "/v1/responses"))
                self.assertEqual(simulated.request.call_args.kwargs["body"], wire)
                self.assertEqual(simulated.request.call_args.kwargs["headers"]["Authorization"], f"Bearer {secret}")
                self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 403)
                resolver.assert_called_once()
                simulated.request.assert_called_once()
        with closing(sqlite3.connect(audit_path)) as audit:
            rows = audit.execute("SELECT reason_code FROM send_reservations").fetchall()
        self.assertEqual(rows, [("HTTPS_SEND_AUTHORIZED",)])
        self.assertNotIn(secret.encode(), audit_path.read_bytes())

    async def test_three_login_tiles_prefill_only_in_isolated_guided_demo(self):
        from gateway.operator_config import OperatorConfig
        from gateway.provider_https import ProviderHTTPS

        audit = self.root / "audit.sqlite3"
        private = OperatorConfig(self.root / "operator")
        private.save_connection({
            "name": "Privát beállítás", "protocol": "openai-responses",
            "endpoint": "https://model.example.invalid/v1", "model": "synthetic",
            "auth": "api-key", "api_key": "private-test-key", "enabled": "yes",
        })
        logins = (
            ("/login/kero", "kero", "test-owner"),
            ("/login/ellenor", "ellenor", "test-reviewer"),
            ("/operator", "uzemelteto", "test-operator"),
        )
        for path, username, code in logins:
            status, _, page = await self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(f'value="{username}"', page)
            self.assertNotIn(f'value="{code}"', page)

        with self.assertRaises(ValueError):
            create_app(audit, "test-owner", "test-reviewer", guided_demo=True, provider_https=ProviderHTTPS())
        self.app = create_app(
            audit, "test-owner", "test-reviewer", operator_code="test-operator", guided_demo=True
        )
        _, _, page = await self.request("GET", "/")
        self.assertEqual(page.count('class="tile"'), 3)
        self.assertNotIn('name="username"', page)
        self.assertNotIn('name="code"', page)
        for path, username, code in logins:
            self.assertIn(f'href="{path}"', page)
            self.assertNotIn(code, page)
            status, _, form = await self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(f'value="{username}"', form)
            self.assertIn(f'value="{code}"', form)
            for other_path, _, other_code in logins:
                if other_path != path:
                    self.assertNotIn(other_code, form)
        self.assertEqual((await self.request(
            "POST", "/login", data={"username": "ellenor", "code": "test-owner"}
        ))[0], 403)
        for code, role in (("test-owner", "Kérő"), ("test-reviewer", "Ellenőr")):
            cookie = await self.login(code)
            _, _, page = await self.request("GET", "/", cookie=cookie)
            self.assertIn(f"Szerep: {role}", page)
        status, headers, _ = await self.request(
            "POST", "/operator/login", data={"username": "uzemelteto", "code": "test-operator"}
        )
        self.assertEqual(status, 303)
        operator = headers[b"set-cookie"].decode().split(";", 1)[0]
        _, _, page = await self.request("GET", "/operator/connections", cookie=operator)
        self.assertNotIn("Privát beállítás", page)
        self.assertNotIn('name="api_key"', page)
        self.assertEqual((await self.request(
            "POST", "/operator/connections", cookie=operator,
            data={"auth": "api-key", "api_key": "must-not-be-saved"},
        ))[0], 400)
