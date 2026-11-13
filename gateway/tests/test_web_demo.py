# Károly Tamás szakdolgozati prototípusa (2026).
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

    async def request(self, method, path, *, cookie="", data=None, origin=None, host="127.0.0.1:8765", raw=None, content_type="application/x-www-form-urlencoded"):
        headers = [(b"host", host.encode())]
        if method == "POST":
            headers.extend([
                (b"origin", (origin or f"http://{host}").encode()),
                (b"content-type", content_type.encode()),
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
                return {"type": "http.request", "body": raw if raw is not None else urlencode(data or {}).encode()}
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
        location = headers[b"location"].decode()
        self.assertTrue(location.startswith("/requester/case/"))
        return location.replace("/requester", "", 1)

    @staticmethod
    def review_case(path):
        return "/review" + path.removeprefix("/requester") if path.startswith(("/requester/case/", "/case/")) else path

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
        self.assertIn("Eredeti bemenet és kért feladat", html)
        self.assertIn('<mark class="type-PERSON">Teszt Luca</mark>', html)
        self.assertIn("Foglald össze két mondatban", html)
        self.assertEqual((await self.request("POST", doc + "/send", cookie=owner))[0], 403)

        mail = await self.start(owner, "A-MAIL-01")
        status, _, html = await self.request("GET", mail, cookie=reviewer)
        self.assertEqual(status, 200)
        self.assertIn("[E-MAIL]", html)
        self.assertIn('<mark class="type-PERSON">Teszt Mira</mark>', html)
        self.assertIn('<mark class="type-EMAIL">demo42@example.invalid</mark>', html)
        self.assertIn("Pontosan kért feladat", html)
        self.assertIn("Kezelt előnézet", html)
        self.assertNotIn("Helyi futtatás", html)
        decision = self.approval(html)
        for method, route, cookie, fields in (
            ("POST", mail + "/decision", owner, decision),
            ("POST", mail + "/send", reviewer, None),
        ):
            self.assertEqual((await self.request(method, route, cookie=cookie, data=fields))[0], 403)
        self.assertEqual((await self.request(
            "GET", mail, cookie=await self.login("test-reviewer")
        ))[0], 200)
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
        ))[0], 303)
        doc = await self.start(owner, "A-DOC-01")
        self.assertEqual((await self.request("POST", "/logout", cookie=owner))[0], 303)
        self.assertEqual((await self.request("GET", doc, cookie=owner))[0], 401)

    async def test_mail_handoff_waits_in_both_baskets_until_reviewer_logs_in(self):
        owner = await self.login("test-owner")
        mail = await self.start(owner, "A-MAIL-01")
        owner_inbox = (await self.request("GET", "/inbox", cookie=owner))[2]
        self.assertIn(mail, owner_inbox)
        self.assertIn("Következő felelős:</strong> Ellenőr", owner_inbox)
        self.assertEqual((await self.request("POST", mail + "/send", cookie=owner))[0], 403)

        reviewer = await self.login("test-reviewer")
        reviewer_inbox = (await self.request("GET", "/inbox", cookie=reviewer))[2]
        self.assertIn(mail, reviewer_inbox)
        self.assertIn("Megnyitás döntéshez", reviewer_inbox)
        self.assertEqual((await self.request("POST", "/review/logout", cookie=reviewer))[0], 303)
        self.assertIn("Döntésre vár", (await self.request("GET", "/inbox", cookie=owner))[2])

        later_reviewer = await self.login("test-reviewer")
        _, _, preview = await self.request("GET", mail, cookie=later_reviewer)
        self.assertIn("[E-MAIL]", preview)
        self.assertEqual((await self.request(
            "POST", mail + "/decision", cookie=later_reviewer, data=self.approval(preview)
        ))[0], 200)
        self.assertEqual((await self.request("POST", "/review/logout", cookie=later_reviewer))[0], 303)
        self.assertIn("Következő felelős:</strong> Kérő", (await self.request("GET", "/inbox", cookie=owner))[2])
        self.assertEqual((await self.request("POST", mail + "/send", cookie=owner))[0], 200)

    async def test_two_roles_in_one_browser_keep_distinct_case_views(self):
        owner = await self.login("test-owner")
        case = await self.start(owner, "A-MAIL-01")
        status, headers, _ = await self.request(
            "POST", "/login", cookie=owner,
            data={"username": "ellenor", "code": "test-reviewer"},
        )
        self.assertEqual(status, 303)
        reviewer = headers[b"set-cookie"].decode().split(";", 1)[0]
        both = owner + "; " + reviewer
        self.assertNotEqual(owner.split("=", 1)[0], reviewer.split("=", 1)[0])

        owner_inbox = (await self.request("GET", "/requester/inbox", cookie=both))[2]
        review_inbox = (await self.request("GET", "/review/inbox", cookie=both))[2]
        self.assertIn(case, owner_inbox)
        self.assertIn(case, review_inbox)
        self.assertIn(f'action="/review{case}"', review_inbox)
        self.assertIn("Ügy megtekintése", owner_inbox)
        self.assertIn("Megnyitás döntéshez", review_inbox)
        self.assertEqual((await self.request("GET", "/inbox", cookie=both))[0], 409)
        self.assertEqual((await self.request("GET", case, cookie=both))[0], 409)

        _, _, review_page = await self.request("GET", "/review" + case, cookie=both)
        self.assertIn("Szerep: Ellenőr", review_page)
        self.assertIn(f'action="/review{case}/decision"', review_page)
        self.assertIn("Jóváhagyás", review_page)
        _, _, owner_page = await self.request("GET", "/requester" + case, cookie=both)
        self.assertIn("Szerep: Kérő", owner_page)
        self.assertNotIn(f'action="{case}/decision"', owner_page)
        self.assertEqual((await self.request(
            "POST", "/review" + case + "/decision", cookie=both,
            data=self.approval(review_page),
        ))[0], 200)
        self.assertEqual((await self.request("POST", "/logout", cookie=both))[0], 303)
        self.assertIn(case, (await self.request("GET", "/review/inbox", cookie=reviewer))[2])

    async def test_cases_survive_restart_with_actions_and_local_restoration(self):
        import os
        import stat

        owner = await self.login("test-owner")
        mail = await self.start(owner, "A-MAIL-01")
        document = await self.start(owner, "A-DOC-01")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.root / "cases.sqlite3").stat().st_mode), 0o600)

        self.app = create_app(
            self.root / "audit.sqlite3", "test-owner", "test-reviewer", operator_code="test-operator"
        )
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        self.assertIn(mail, (await self.request("GET", "/review/inbox", cookie=reviewer))[2])
        self.assertIn(document, (await self.request("GET", "/inbox", cookie=owner))[2])
        _, _, review_page = await self.request("GET", "/review" + mail, cookie=reviewer)
        self.assertIn("Jóváhagyás", review_page)
        self.assertEqual((await self.request(
            "POST", "/review" + mail + "/decision", cookie=reviewer,
            data=self.approval(review_page),
        ))[0], 200)
        _, _, document_page = await self.request("GET", document, cookie=owner)
        self.assertEqual((await self.request(
            "POST", document + "/decision", cookie=owner,
            data=self.approval(document_page),
        ))[0], 200)

        self.app = create_app(
            self.root / "audit.sqlite3", "test-owner", "test-reviewer", operator_code="test-operator"
        )
        owner = await self.login("test-owner")
        inbox = (await self.request("GET", "/inbox", cookie=owner))[2]
        self.assertIn(mail, inbox)
        self.assertIn(document, inbox)
        self.assertIn("Jóváhagyva", inbox)
        self.assertEqual((await self.request("POST", mail + "/send", cookie=owner))[0], 200)
        status, _, document_result = await self.request("POST", document + "/send", cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn("Teszt Luca átvette", document_result)

        self.app = create_app(
            self.root / "audit.sqlite3", "test-owner", "test-reviewer", operator_code="test-operator"
        )
        owner = await self.login("test-owner")
        self.assertIn("Futtatás befejezve", (await self.request("GET", "/inbox", cookie=owner))[2])
        restored = (await self.request("GET", document, cookie=owner))[2]
        self.assertIn("Teszt Luca átvette", restored)
        self.assertIn('<mark class="type-PERSON">Teszt Luca</mark>', restored)
        self.assertIn("Pontosan kért feladat", restored)
        reviewer = await self.login("test-reviewer")
        self.assertIn(mail, (await self.request("GET", "/review/inbox", cookie=reviewer))[2])
        self.assertIn('<mark class="type-EMAIL">demo42@example.invalid</mark>',
                      (await self.request("GET", "/review" + mail, cookie=reviewer))[2])

    async def test_returned_case_remains_in_both_baskets_after_restart(self):
        owner = await self.login("test-owner")
        case = await self.start(owner, "A-MAIL-01")
        reviewer = await self.login("test-reviewer")
        _, _, page = await self.request("GET", "/review" + case, cookie=reviewer)
        decision = dict(self.approval(page), outcome="returned")
        self.assertEqual((await self.request(
            "POST", "/review" + case + "/decision", cookie=reviewer, data=decision,
        ))[0], 200)
        self.app = create_app(self.root / "audit.sqlite3", "test-owner", "test-reviewer")
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        for path, cookie in (("/inbox", owner), ("/review/inbox", reviewer)):
            inbox = (await self.request("GET", path, cookie=cookie))[2]
            self.assertIn(case, inbox)
            self.assertIn("Visszaadva javításra", inbox)
        self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 403)


    async def test_sample_catalog_and_separate_filtered_draft_inbox(self):
        from gateway.requester_cases import samples
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        status, headers, _ = await self.request("GET", "/", cookie=owner)
        self.assertEqual((status, headers[b"location"]), (303, b"/samples"))
        status, _, page = await self.request("GET", "/samples?sample_id=DEV-DOC-06", cookie=owner)
        self.assertEqual(status, 200)
        self.assertNotIn(" · piros</option>", page)
        self.assertIn("Az ellenőr a kezelt előnézet alapján dönt", page)
        self.assertIn('<mark class="type-', page)
        self.assertIn("Elvárt működés és válasz", page)
        self.assertNotIn("EVAL-", page)
        for sample in samples().values():
            self.assertIn(sample["id"], page)
        self.assertEqual((await self.request("GET", "/samples", cookie=reviewer))[0], 401)
        self.assertEqual((await self.request("GET", "/samples?sample_id=EVAL-DOC-01", cookie=owner))[0], 404)

        status, _, _ = await self.request("POST", "/sample-drafts", cookie=owner, data={"sample_id": "DEV-DOC-06"})
        self.assertEqual(status, 303)
        _, _, page = await self.request("GET", "/inbox", cookie=owner)
        self.assertIn("DEV-DOC-06", page)
        self.assertIn("Piszkozat – feldolgozás előtt", page)
        self.assertNotIn("Elvárt eredmény", page)
        draft_id = re.search(r'name="draft_id" value="([a-f0-9]{32})"', page)[1]
        self.assertNotIn(draft_id, (await self.request("GET", "/review/inbox", cookie=reviewer))[2])
        self.assertEqual((await self.request("GET", f"/own-input?draft_id={draft_id}", cookie=reviewer))[0], 401)
        later_owner = await self.login("test-owner")
        self.assertIn(draft_id, (await self.request("GET", "/inbox", cookie=later_owner))[2])
        self.assertEqual((await self.request("GET", f"/own-input?draft_id={draft_id}", cookie=later_owner))[0], 200)
        self.assertNotIn(draft_id, (await self.request("GET", "/inbox?query=no-such-case", cookie=owner))[2])
        self.assertNotIn(draft_id, (await self.request("GET", "/inbox?status=Jóváhagyva", cookie=owner))[2])
        self.assertIn(draft_id, (await self.request("GET", "/inbox?query=DEV-DOC-06", cookie=owner))[2])
        self.assertEqual((await self.request("POST", "/logout", cookie=owner))[0], 303)
        self.assertEqual((await self.request("GET", f"/own-input?draft_id={draft_id}", cookie=owner))[0], 401)
        self.app = create_app(self.root / "audit.sqlite3", "test-owner", "test-reviewer")
        restarted_owner = await self.login("test-owner")
        self.assertIn(draft_id, (await self.request("GET", "/inbox", cookie=restarted_owner))[2])

    async def test_all_dev_samples_can_enter_the_correct_review_path(self):
        from html import unescape
        from gateway.requester_cases import samples
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        for sample in samples().values():
            if not sample["id"].startswith("DEV-"):
                continue
            with self.subTest(sample=sample["id"]):
                self.assertEqual((await self.request(
                    "POST", "/sample-local", cookie=owner, data={"sample_id": sample["id"]},
                ))[0], 303)
                _, _, inbox = await self.request("GET", "/inbox", cookie=owner)
                draft_id = re.findall(r'name="draft_id" value="([a-f0-9]{32})"', inbox)[0]
                _, _, page = await self.request("GET", f"/own-input?draft_id={draft_id}", cookie=owner)
                fields = {
                    "source": sample["source"], "request": sample["request"], "draft_id": draft_id,
                    "stamp": re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1],
                    "selections": unescape(re.search(r'name="selections" value="([^"]*)"', page)[1]),
                }
                status, headers, page = await self.request(
                    "POST", "/own-input/token-preview?review=1", cookie=owner, data=fields,
                )
                self.assertEqual(status, 303, page[-1200:])
                case = headers[b"location"].decode()
                status, _, page = await self.request("GET", case, cookie=owner)
                self.assertEqual(status, 200)
                requires_reviewer = sample["risk"] in ("narancs", "piros")
                self.assertEqual('name="outcome"' in page, not requires_reviewer)
                status, _, page = await self.request("GET", self.review_case(case), cookie=reviewer)
                self.assertEqual(status, 200 if requires_reviewer else 403)
                self.assertNotIn(draft_id, (await self.request("GET", "/inbox", cookie=owner))[2])

    async def test_dev_model_trial_cannot_fall_back_to_local_receipt(self):
        from html import unescape
        from gateway.requester_cases import samples
        from unittest.mock import patch
        owner = await self.login("test-owner")
        sample = samples()["DEV-DOC-01"]
        status, headers, _ = await self.request(
            "POST", "/sample-start", cookie=owner, data={"sample_id": sample["id"]},
        )
        self.assertEqual(status, 303)
        _, _, page = await self.request("GET", headers[b"location"].decode(), cookie=owner)
        self.assertIn("Ez a helyi demó nem használ modellkapcsolatot", page)
        fields = dict(
            source=sample["source"], request=sample["request"],
            stamp=re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1],
            draft_id=re.search(r'name="draft_id" value="([a-f0-9]{32})"', page)[1],
            selections=unescape(re.search(r'name="selections" value="([^"]*)"', page)[1]),
        )
        with patch("gateway.demo_flow.local_response") as local:
            status, _, _ = await self.request("POST", "/own-input/token-preview?review=1", cookie=owner, data=fields)
            self.assertEqual(status, 409)
            local.assert_not_called()
        self.assertIn(fields["draft_id"], (await self.request("GET", "/inbox", cookie=owner))[2])

    async def test_single_treatment_choice_previews_locally_and_keeps_model_gate(self):
        from html import unescape
        from gateway.requester_cases import samples

        owner = await self.login("test-owner")
        sample = samples()["DEV-DOC-01"]
        status, headers, _ = await self.request(
            "POST", "/sample-start", cookie=owner, data={"sample_id": sample["id"]},
        )
        self.assertEqual(status, 303)
        _, _, page = await self.request("GET", headers[b"location"].decode(), cookie=owner)
        self.assertIn("Minta előkészítése", page)
        self.assertIn('name="treatment"', page)
        self.assertIn('data-help=', page)
        self.assertIn("Ügy előkészítése megerősítésre", page)
        fields = {
            "source": sample["source"], "request": sample["request"],
            "stamp": re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1],
            "draft_id": re.search(r'name="draft_id" value="([a-f0-9]{32})"', page)[1],
            "selections": unescape(re.search(r'name="selections" value="([^"]*)"', page)[1]),
        }
        for treatment, heading in (("token", "Tokenizált előnézet"), ("mask", "Maszkolt előnézet")):
            status, _, preview = await self.request(
                "POST", "/own-input/preview-selected", cookie=owner,
                data=dict(fields, treatment=treatment),
            )
            self.assertEqual(status, 200)
            self.assertIn(heading, preview)
        status, _, invalid = await self.request(
            "POST", "/own-input/preview-selected", cookie=owner,
            data=dict(fields, treatment="unknown"),
        )
        self.assertEqual(status, 400)
        self.assertIn("A művelet nem sikerült", invalid)
        self.assertIn("Válassz érvényes kezelési módot", invalid)
        self.assertEqual((await self.request(
            "POST", "/own-input/start", cookie=owner, data=dict(fields, treatment="token"),
        ))[0], 409)
        self.assertIn(fields["draft_id"], (await self.request("GET", "/inbox", cookie=owner))[2])

    async def test_dev_model_trial_requires_approval_and_excludes_expected_answer(self):
        from html import unescape
        from unittest.mock import patch
        from gateway.operator_config import OperatorConfig
        from gateway.provider_https import ProviderHTTPS
        from gateway.provider_messages import json_bytes
        from gateway.requester_cases import samples

        endpoint = "https://model.example.invalid/v1"
        transport = ProviderHTTPS(frozenset({("openai-responses", endpoint)}))
        catalog = samples()
        for sample in catalog.values():
            sample["expected"] = ["REFERENCE-ONLY-DO-NOT-SEND"]
        with patch("gateway.requester_web.samples", return_value=catalog):
            self.app = create_app(self.root / "audit.sqlite3", "test-owner", "test-reviewer", provider_https=transport)
        config = OperatorConfig(self.root / "operator")
        connection = config.save_connection(dict(
            name="Teszt", protocol="openai-responses", endpoint=endpoint, model="synthetic",
            auth="api-key", api_key="synthetic-key", enabled="yes",
        ))
        profiles = {task: config.save_profile(dict(
            name=task, task=task, connection_id=connection, max_tokens="100", timeout_seconds="30",
        )) for task in ("summary", "mail-draft")}
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        response = json_bytes(dict(status="completed", output=[dict(
            type="message", role="assistant", status="completed",
            content=[dict(type="output_text", text="Szintetikus modellválasz.")],
        )]))
        with patch.object(transport, "send", return_value=response) as remote, patch("gateway.demo_flow.local_response") as local:
            for sample_id in ("DEV-DOC-01", "DEV-MAIL-03"):
                sample = catalog[sample_id]
                _, headers, _ = await self.request("POST", "/sample-start", cookie=owner, data={"sample_id": sample_id})
                _, _, page = await self.request("GET", headers[b"location"].decode(), cookie=owner)
                task = "summary" if "DOC" in sample_id else "mail-draft"
                fields = dict(
                    source=sample["source"], request=sample["request"], profile_id=profiles[task],
                    stamp=re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1],
                    draft_id=re.search(r'name="draft_id" value="([a-f0-9]{32})"', page)[1],
                    selections=unescape(re.search(r'name="selections" value="([^"]*)"', page)[1]),
                    treatment="token",
                )
                self.assertIn("Külső modellválasz", page)
                status, _, local_preview = await self.request(
                    "POST", "/own-input/preview-selected", cookie=owner, data=fields,
                )
                self.assertEqual(status, 200)
                self.assertIn("Tokenizált előnézet", local_preview)
                status, headers, _ = await self.request("POST", "/own-input/start", cookie=owner, data=fields)
                self.assertEqual(status, 303)
                case = headers[b"location"].decode()
                before = remote.call_count
                self.assertEqual((await self.request("POST", case+"/send", cookie=owner))[0], 403)
                self.assertEqual(remote.call_count, before)
                decider = reviewer if sample["risk"] in ("narancs", "piros") else owner
                _, _, page = await self.request("GET", self.review_case(case) if decider == reviewer else case, cookie=decider)
                self.assertNotIn("synthetic-key", page)
                await self.request("POST", (self.review_case(case) if decider == reviewer else case)+"/decision", cookie=decider, data=self.approval(page))
                status, _, page = await self.request("POST", case+"/send", cookie=owner)
                self.assertEqual(status, 200)
                self.assertIn("Szintetikus modellválasz.", page)
                wire = remote.call_args.args[1]
                self.assertNotIn(b"REFERENCE-ONLY-DO-NOT-SEND", wire)
                self.assertNotIn(b"synthetic-key", wire)
                self.assertEqual(remote.call_args.kwargs["api_key"], "synthetic-key")
            self.assertEqual(remote.call_count, 2)
            local.assert_not_called()

    async def test_fixed_demo_draft_retains_expected_response_and_idempotent_start(self):
        owner = await self.login("test-owner")
        await self.request("POST", "/sample-drafts", cookie=owner, data={"sample_id": "A-DOC-01"})
        _, _, page = await self.request("GET", "/inbox", cookie=owner)
        draft_path = re.search(r'action="(/sample-drafts/[a-f0-9]{32})"', page)[1]
        self.assertEqual((await self.request("GET", draft_path, cookie=owner))[0], 200)
        status, headers, _ = await self.request("POST", draft_path + "/start", cookie=owner)
        self.assertEqual(status, 303)
        case = headers[b"location"].decode()
        self.assertEqual((await self.request("POST", draft_path + "/start", cookie=owner))[1][b"location"], headers[b"location"])
        _, _, page = await self.request("GET", case, cookie=owner)
        await self.request("POST", case + "/decision", cookie=owner, data=self.approval(page))
        status, _, page = await self.request("POST", case + "/send", cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn("Teszt Luca átvette", page)

    async def test_own_draft_save_resume_and_process_replaces_inbox_row(self):
        from html import unescape
        from unittest.mock import patch
        owner = await self.login("test-owner")
        reviewer = await self.login("test-reviewer")
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            status, headers, _ = await self.request("POST", "/own-input", cookie=owner, data={
                "source": "Teszt Anna <b>adat</b>", "request": "Írj összefoglalót.", "save_only": "yes",
            })
            self.assertEqual((status, headers[b"location"]), (303, b"/requester/inbox"))
            _, _, page = await self.request("GET", "/inbox", cookie=owner)
            draft_id = re.search(r'name="draft_id" value="([a-f0-9]{32})"', page)[1]
            self.assertNotIn("Teszt Anna", page)
            _, _, page = await self.request("GET", f"/own-input?draft_id={draft_id}", cookie=owner)
        self.assertIn("&lt;b&gt;adat&lt;/b&gt;", page)
        self.assertIn('id="selection-form"', page)
        fields = {
            "source": "Teszt Anna <b>adat</b>", "request": "Írj összefoglalót.", "draft_id": draft_id,
            "stamp": re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1],
            "selections": json.dumps([{"section": "source", "start": 0, "end": 10, "kind": "PERSON"}]),
        }
        self.assertEqual((await self.request("POST", "/own-input/save", cookie=owner, data=fields))[0], 303)
        status, headers, page = await self.request("POST", "/own-input/token-preview?review=1", cookie=owner, data=fields)
        self.assertEqual(status, 303, page[-500:])
        case = headers[b"location"].decode()
        self.assertNotIn(draft_id, (await self.request("GET", "/inbox", cookie=owner))[2])
        self.assertIn(case.rsplit("/", 1)[-1], (await self.request("GET", "/review/inbox", cookie=reviewer))[2])

    async def test_uploaded_text_is_local_escaped_and_resumable(self):
        from unittest.mock import patch

        owner = await self.login("test-owner")
        boundary = "gateway-upload-test"
        raw = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"task\"\r\n\r\nmail-draft\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n\r\nÍrj választ.\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"teszt.txt\"\r\nContent-Type: text/plain\r\n\r\n"
            f"Teszt Anna <script>\r\n--{boundary}--\r\n"
        ).encode()
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            status, _, html = await self.request(
                "POST", "/own-input/upload", cookie=owner, raw=raw,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        self.assertEqual(status, 200)
        self.assertIn("E-mail-választervezet", html)
        self.assertIn("Teszt Anna &lt;script&gt;", html)
        self.assertIn('id="document-preview"', html)
        self.assertIn("Nagy előnézet", html)
        self.assertNotIn("teszt.txt", html)
        from test_document_input import document
        docx = document("<w:p><w:r><w:t>Egyszerű dokumentum</w:t></w:r></w:p>")
        docx_raw = raw.replace(b'filename="teszt.txt"', b'filename="teszt.docx"').replace(
            "Teszt Anna <script>".encode(), docx,
        )
        with patch("gateway.own_input_web.suggest_names", return_value=None):
            docx_status, _, docx_html = await self.request(
                "POST", "/own-input/upload", cookie=owner, raw=docx_raw,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        self.assertEqual(docx_status, 200)
        self.assertIn("Egyszerű dokumentum", docx_html)

        self.assertEqual((await self.request(
            "POST", "/own-input/upload", raw=raw, content_type=f"multipart/form-data; boundary={boundary}",
        ))[0], 401)
        oversized = b"x" * (1024 * 1024 + 16385)
        self.assertEqual((await self.request(
            "POST", "/own-input/upload", cookie=owner, raw=oversized,
            content_type=f"multipart/form-data; boundary={boundary}",
        ))[0], 400)

    async def test_sample_edit_allows_category_selection_and_dictionary_save(self):
        from html import unescape
        owner = await self.login("test-owner")
        status, headers, _ = await self.request(
            "POST", "/sample-edit", cookie=owner, data={"sample_id": "A-DOC-01"},
        )
        self.assertEqual(status, 303)
        _, _, html = await self.request("GET", headers[b"location"].decode(), cookie=owner)
        self.assertIn('id="selection-form"', html)
        self.assertIn('id="kind-source"', html)
        self.assertIn('<option value="PERSON">Személy</option>', html)
        self.assertIn("Megjegyzés későbbre", (await self.request("GET", "/own-input.js"))[2])
        from gateway.requester_cases import samples
        sample = samples()["A-DOC-01"]
        fields = {
            "source": sample["source"], "request": sample["request"],
            "stamp": re.search(r'name="stamp" value="([a-f0-9]{64})"', html)[1],
            "draft_id": re.search(r'name="draft_id" value="([a-f0-9]{32})"', html)[1],
        }
        start = sample["source"].index("Teszt Műhely")
        fields["selections"] = json.dumps([dict(section="source", start=start, end=start+len("Teszt Műhely"), kind="SENSITIVE")])
        self.assertEqual((await self.request("POST", "/own-input/remember", cookie=owner, data=fields))[0], 200)
        _, _, page = await self.request("GET", "/sensitive-values", cookie=owner)
        self.assertIn("Teszt Műhely", page)

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
        self.assertEqual((await self.request("GET", "/own-input", cookie=reviewer))[0], 401)

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
            details = dict(name="hu_core_news_md", model_version="3.8.1",
                           spacy_version="3.8.18", installed_at="2026-11-13T10:00:00+00:00",
                           time_source="Helyben rögzített.", load_method="Kérésenként külön helyi folyamat.")
            with patch("gateway.web_demo.nlp_installed", return_value=ready), patch("gateway.web_demo.nlp_active", return_value=ready), patch("gateway.web_demo.nlp_details", return_value=details if ready else None):
                status, _, html = await self.request("GET", "/recognizer-setup", cookie=owner)
                self.assertEqual(status, 200)
                self.assertIn("Mire szolgál?", html)
                if ready:
                    self.assertIn("hu_core_news_md 3.8.1", html)
                    self.assertIn("2026-11-13T10:00:00+00:00", html)
                    self.assertIn("Kérésenként külön helyi folyamat.", html)
                self.assertNotIn("Telepítés indítása", html)
                self.assertEqual("fordulj az Üzemeltetőhöz" in html, not ready)
                self.assertIn("telepítve van" if ready else "nincs telepítve", html)
        self.assertEqual((await self.request("GET", "/recognizer-setup", cookie=reviewer))[0], 401)

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
            _, _, html = await self.request("GET", "/inbox", cookie=cookie)
            self.assertIn(case, html)
            self.assertIn("Döntésre vár", html)
            self.assertNotIn("Teszt Mira", html)
        other_owner = await self.login("test-owner")
        self.assertIn(case, (await self.request("GET", "/inbox", cookie=other_owner))[2])
        later_reviewer = await self.login("test-reviewer")
        self.assertIn(case, (await self.request("GET", "/inbox", cookie=later_reviewer))[2])
        _, _, preview = await self.request("GET", case, cookie=reviewer)
        self.assertEqual((await self.request(
            "POST", case + "/decision", cookie=reviewer, data=self.approval(preview),
        ))[0], 200)
        self.assertIn("Futtatás megnyitása", (await self.request("GET", "/inbox", cookie=owner))[2])
        self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 200)
        self.assertIn("Futtatás befejezve", (await self.request("GET", "/inbox", cookie=owner))[2])
        self.assertIn("Futtatás befejezve", (await self.request("GET", "/review/inbox", cookie=reviewer))[2])

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
                self.assertIn("Kijelölés hozzáadása", html)
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
        self.assertEqual((await self.request("GET", "/sensitive-values", cookie=reviewer))[0], 401)
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
        status, headers, _ = await self.request(
            "POST", "/own-input/token-preview?review=1", cookie=owner, data=payload
        )
        self.assertEqual(status, 303)
        pending_case = headers[b"location"].decode()
        owner_inbox = (await self.request("GET", "/inbox", cookie=owner))[2]
        self.assertIn(pending_case, owner_inbox)
        self.assertIn("Következő felelős:</strong> Ellenőr", owner_inbox)
        self.assertEqual((await self.request("POST", pending_case + "/send", cookie=owner))[0], 403)
        reviewer = await self.login("test-reviewer")
        reviewer_inbox = (await self.request("GET", "/inbox", cookie=reviewer))[2]
        self.assertIn(pending_case.rsplit("/", 1)[-1], reviewer_inbox)
        self.assertIn("Megnyitás döntéshez", reviewer_inbox)
        for route, marker in (("preview", "[ÉRZÉKENY]"), ("token-preview", "[[A:ERZEKENY:")):
            with self.subTest(route=route):
                status, headers, _ = await self.request(
                    "POST", f"/own-input/{route}?review=1", cookie=owner, data=payload
                )
                self.assertEqual(status, 303)
                case = headers[b"location"].decode()
                status, _, page = await self.request("GET", self.review_case(case), cookie=reviewer)
                self.assertEqual(status, 200)
                self.assertIn(marker, page)
                self.assertIn('<mark class="type-SENSITIVE">BELSŐ-42</mark>', page)
                self.assertNotIn("Helyi kapupróba", page)
                self.assertEqual((await self.request(
                    "POST", case + "/send", cookie=owner
                ))[0], 403)
                decision = self.approval(page)
                self.assertEqual((await self.request(
                    "POST", case + "/decision", cookie=owner, data=decision
                ))[0], 403)
                self.assertEqual((await self.request(
                    "POST", self.review_case(case) + "/decision", cookie=reviewer, data=decision
                ))[0], 200)
                self.assertEqual((await self.request(
                    "POST", self.review_case(case) + "/send", cookie=reviewer
                ))[0], 404)
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
        fields = {"source": "BELSŐ-42 egyeztetés", "request": "Írj rövid választ.", "task": "mail-draft"}
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
            _, _, page = await self.request("GET", self.review_case(case), cookie=reviewer)
            self.assertNotIn(connection_fields["api_key"], page)
            self.assertIn('<mark class="type-SENSITIVE">BELSŐ-42</mark>', page)
            self.assertIn("Írj rövid választ.", page)
            preview = unescape(re.search(r"<pre>(.*?)</pre>", page, re.S)[1]).encode("utf-8")
            status, _, _ = await self.request(
                "POST", self.review_case(case) + "/decision", cookie=reviewer, data=self.approval(page)
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
            self.assertIn('<mark class="type-SENSITIVE">BELSŐ-42</mark>', page)
            self.assertIn("Pontosan kért feladat", page)
            self.assertIn("Írj rövid választ.", page)
            self.assertIn('id="provider-body"', page)
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
                self.assertNotIn(secret.encode(), (self.root / "cases.sqlite3").read_bytes())
                self.app = create_app(audit_path, "test-owner", "test-reviewer", provider_https=transport)
                owner = await self.login("test-owner")
                reviewer = await self.login("test-reviewer")
                _, _, page = await self.request("GET", self.review_case(case), cookie=reviewer)
                self.assertIn("Külső HTTPS-küldés", page)
                self.assertNotIn(secret, page)
                self.assertIn('<mark class="type-SENSITIVE">BELSŐ-42</mark>', page)
                wire = unescape(re.search(r'<pre id="provider-body">(.*?)</pre>', page, re.S)[1]).encode()
                self.assertEqual((await self.request("POST", case + "/send", cookie=owner))[0], 403)
                resolver.assert_not_called()
                self.assertEqual((await self.request(
                    "POST", self.review_case(case) + "/decision", cookie=reviewer, data=self.approval(page)
                ))[0], 200)
                self.assertEqual((await self.request("POST", self.review_case(case) + "/send", cookie=reviewer))[0], 404)
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
            _, _, page = await self.request("GET", "/inbox", cookie=cookie)
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

    async def test_model_capable_local_prefill_covers_three_roles_without_api_key(self):
        from gateway.operator_config import OperatorConfig
        from gateway.provider_https import ProviderHTTPS

        private = OperatorConfig(self.root / "operator")
        private.save_connection({
            "name": "Helyi modellpróba", "protocol": "openai-responses",
            "endpoint": "https://model.example.invalid/v1", "model": "synthetic",
            "auth": "api-key", "api_key": "private-test-key", "enabled": "yes",
        })
        self.app = create_app(
            self.root / "audit.sqlite3", "test-owner", "test-reviewer",
            operator_code="test-operator",
            provider_https=ProviderHTTPS(frozenset({("openai-responses", "https://model.example.invalid/v1")})),
            prefill_role_logins=True,
        )
        _, _, home = await self.request("GET", "/")
        self.assertIn("A beállított modell", home)
        for code in ("test-owner", "test-reviewer", "test-operator", "private-test-key"):
            self.assertNotIn(code, home)
        for path, code in (("/login/kero", "test-owner"), ("/login/ellenor", "test-reviewer")):
            status, _, form = await self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(f'value="{code}"', form)
            self.assertNotIn("private-test-key", form)
        status, _, operator = await self.request("GET", "/operator")
        self.assertEqual(status, 200)
        self.assertIn('value="uzemelteto"', operator)
        self.assertIn('value="test-operator"', operator)
        self.assertNotIn("private-test-key", operator)
