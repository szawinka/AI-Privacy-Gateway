"""Loopback-only web entry point for the fixed synthetic A-stage demo."""

import argparse
from hmac import compare_digest
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
import uvicorn

from gateway.approval import Outcome
from gateway.demo_flow import DemoFlow
from gateway.demo_response import DemoTask
from gateway.install_nlp import active as nlp_active
from gateway.install_nlp import installed as nlp_installed
from gateway.operator_config import OperatorConfig, PROTOCOLS
from gateway.operator_web import mount_operator
from gateway.own_input_web import mount_own_input
from gateway.provider_https import ProviderHTTPS
from gateway.role_access import Actor, Role
from gateway.session_lifecycle import SessionStore, SessionUnavailable
from gateway.sensitive_store import SensitiveStore
from gateway.sensitive_web import mount_sensitive_values
from gateway.web_layout import STYLE_HASH, TEMPLATES


_LOOPBACK = "127.0.0.1"
_DEFAULT_PORT = 8765
_COOKIE = "gateway_demo_session"
_LOGIN_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
<main class="login">
<p>AI Privacy Gateway</p>
<h1>{{ title }} – bejelentkezés</h1>
<p>Add meg a szerepkörhöz tartozó belépési adatokat.</p>
<form class="tile" method="post" action="{{ action }}">
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<label>Felhasználónév <input name="username" value="{{ username }}" required autocomplete="username"></label>
<label>Jelszó <input name="code" type="password" value="{{ code }}" required autocomplete="off"></label>
<button>Belépés</button>
<a class="back" href="/">Vissza a szerepválasztáshoz</a>
</form>
</main>
""")

_PAGE = TEMPLATES.from_string("""
<!doctype html>
<html lang="hu">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Privacy Gateway – helyi demó</title>
<style>{{ page_style }}</style>
<body>
{% from "workspace" import toolbar %}
<main{% if actor %} class="workspace"{% endif %}>
<h1>AI Privacy Gateway – helyi demó</h1>
{% if not actor %}
<h2>Válassz szerepkört</h2>
<section class="tiles" aria-label="Szerepkörök">
{% for title, description, path in login_tiles %}
<a class="tile" href="{{ path }}">
<h2>{{ title }}</h2>
<p>{{ description }}</p>
</a>
{% endfor %}
</section>
{% else %}
<p>Szerep: {{ "Kérő" if owner else "Ellenőr" }}</p>
{{ toolbar("Kérő" if owner else "Ellenőr", "/" if not case_id else "") }}
{% if not case_id %}
{% if owner %}
<section class="panel"><h2>Szintetikus példák</h2>
<p>A levéldemó indítása előtt jelentkezz be ellenőrként külön böngészőprofilban.</p>
<form method="post" action="/cases" class="actions">
<button name="task" value="A-DOC-01">Dokumentumdemó indítása</button>
<button name="task" value="A-MAIL-01">Levéldemó indítása</button>
</form>
</section>
{% endif %}
<section class="panel">
<h2>Munkakosár</h2>
<p>Az aktuális munkamenethez tartozó ügyek.</p>
{% for row in inbox %}
<article class="case-row">
<h3>{{ row.kind }}</h3>
<p><code>{{ row.case_id }}</code></p>
<p><strong>Állapot:</strong> {{ row.status }} · <strong>Következő felelős:</strong> {{ row.next_role }}</p>
<form method="get" action="{{ '/own-input' if row.action == 'Új ügy indítása' and row.kind == 'Saját szöveg' else ('/' if row.action == 'Új ügy indítása' else '/case/' ~ row.case_id) }}">
<button class="secondary">{{ row.action }}</button>
</form>
</article>
{% else %}<p>A munkakosár üres.</p>{% endfor %}
</section>
<section class="panel">
<h2>Ügy keresése azonosítóval</h2>
<form method="get" action="/case" class="actions">
<label>Ügyazonosító <input name="id" required></label>
<button>Ügy megnyitása</button>
</form>
</section>
{% else %}
<section class="panel">
<p>Ügyazonosító: <code>{{ case_id }}</code></p>
<p><strong>Állapot:</strong> {{ progress.status }} · <strong>Következő felelős:</strong> {{ progress.next_role }}</p>
{% if progress.action == "Új ügy indítása" %}
<form method="get" action="{{ '/own-input' if progress.kind == 'Saját szöveg' else '/' }}"><button class="secondary">Új ügy indítása</button></form>
{% endif %}
<p>Indokkód: {{ preview.reason_code }}</p>
<h2>Kezelt előnézet</h2>
<pre>{{ preview.body.decode("utf-8") }}</pre>
{% if preview.provider_body %}
<h2>Szolgáltatói kérés törzse</h2>
<pre id="provider-body">{{ preview.provider_body.decode("utf-8") }}</pre>
<p>{{ "Külső HTTPS-küldés a megadott szolgáltatóhoz." if preview.external_https else "Helyi próba szolgáltatói válaszformátummal." }}</p>
{% endif %}
{% if message %}<p role="status">{{ message }}</p>{% endif %}
{% if result is not none %}<h2>{{ "Modellválasz" if preview.external_https else "Helyi válasz" }}</h2><pre>{{ result }}</pre>{% endif %}
{% if progress.can_decide %}
<form method="post" action="/case/{{ case_id }}/decision" class="actions">
<input type="hidden" name="version" value="{{ preview.version }}">
<input type="hidden" name="digest" value="{{ preview.digest }}">
<button name="outcome" value="approved">Jóváhagyás</button>
<button class="secondary" name="outcome" value="returned">Visszaadás javításra</button>
</form>
{% endif %}
{% if progress.can_send and result is none %}
<form method="post" action="/case/{{ case_id }}/send"><button>{{ "Küldés a szolgáltatóhoz" if preview.external_https else ("Helyi kapupróba" if preview.reason_code == "OWN_REVIEW_REQUIRED" else "Helyi futtatás") }}</button></form>
{% endif %}
</section>
{% endif %}
{% endif %}
</main>
</body>
</html>
""")


_RECOGNIZER_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Magyar névfelismerő – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>Magyar névfelismerő</h1>
{{ toolbar("Kérő", "/recognizer-setup") }}
<section class="panel">
<p role="status">{{ state }}</p>
<form method="get" action="/recognizer-setup"><button class="secondary">Állapot frissítése</button></form>
</section>
</main></html>
""")


async def _fields(request: Request, *, limit: int = 4096, max_fields: int = 4) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0] != (
        "application/x-www-form-urlencoded"
    ):
        raise HTTPException(415, "Nem támogatott űrlapformátum.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Túl nagy űrlap.")
        body.extend(chunk)
    try:
        parsed = parse_qs(body.decode("ascii"), max_num_fields=max_fields)
    except ValueError:
        raise HTTPException(400, "Érvénytelen űrlap.") from None
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(400, "Ismétlődő űrlapmező.")
    return {key: values[0] for key, values in parsed.items()}


def create_app(
    audit_path: Path, requester_code: str, reviewer_code: str, *, operator_code: str | None = None,
    provider_https: ProviderHTTPS | None = None, guided_demo: bool = False, port: int = _DEFAULT_PORT,
) -> FastAPI:
    host = f"{_LOOPBACK}:{port}"
    origin = f"http://{host}"
    cookie_suffix = "" if port == _DEFAULT_PORT else f"_{port}"
    session_cookie = _COOKIE + cookie_suffix
    if not requester_code or not reviewer_code or requester_code == reviewer_code:
        raise ValueError("Distinct demo access codes are required")
    if operator_code is not None and (not operator_code or operator_code in (requester_code, reviewer_code)):
        raise ValueError("Distinct operator access code is required")
    if guided_demo:
        if provider_https is not None:
            raise ValueError("Guided demo cannot use external HTTPS")
        audit_path = audit_path.parent / "guided-demo" / audit_path.name
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    role_logins = {
        "kero": ("Kérő", "Saját szöveg előkészítése és a jóváhagyott feladat futtatása.", requester_code),
        "ellenor": ("Ellenőr", "A hozzárendelt kezelt előnézet ellenőrzése és jóváhagyása.", reviewer_code),
    }
    login_tiles = [
        (title, description, f"/login/{username}")
        for username, (title, description, _) in role_logins.items()
    ]
    if operator_code is not None:
        login_tiles.append((
            "Üzemeltető", "Modellkapcsolatok és hívási profilok karbantartása.",
            "/operator",
        ))
    sessions = SessionStore()
    operator_config = OperatorConfig(audit_path.parent / "operator")
    flow = DemoFlow(sessions, audit_path, operator_config=operator_config, provider_https=provider_https)
    sensitive = SensitiveStore(audit_path.parent / "sensitive" / "values.sqlite3")
    actors: dict[str, Actor] = {}
    reviewer_id: str | None = None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.headers.get("host") != host:
            return HTMLResponse("Érvénytelen helyi cím.", status_code=400)
        if request.method == "POST" and request.headers.get("origin") != origin:
            return HTMLResponse("A műveletet a helyi felületről indítsd.", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        script_policy = "script-src 'self'; connect-src 'self'; " if request.url.path.startswith("/own-input") else ""
        response.headers["Content-Security-Policy"] = (
            script_policy + f"style-src 'sha256-{STYLE_HASH}'; " +
            "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def signed_in(request: Request) -> Actor | None:
        session_id = request.cookies.get(session_cookie, "")
        actor = actors.get(session_id)
        if actor is not None and not sessions.is_active(session_id):
            actors.pop(session_id, None)
            return None
        return actor

    def require_actor(request: Request) -> Actor:
        actor = signed_in(request)
        if actor is None:
            raise HTTPException(401, "Jelentkezz be a demóhoz.")
        return actor

    def render(actor=None, case_id=None, preview=None, message=None, result=None, *, inbox=None):
        return HTMLResponse(_PAGE.render(
            actor=actor, owner=actor is not None and actor.role is Role.REQUESTER,
            case_id=case_id, preview=preview, message=message, result=result,
            login_tiles=login_tiles, inbox=inbox,
            progress=flow.progress(actor, case_id) if case_id else None,
        ))

    @app.exception_handler(PermissionError)
    @app.exception_handler(SessionUnavailable)
    async def blocked(_request, _error):
        return HTMLResponse(
            'A művelet blokkolva. <a href="/">Vissza a kezdőlapra</a>', status_code=403
        )

    @app.get("/")
    async def home(request: Request):
        actor = signed_in(request)
        inbox = await run_in_threadpool(flow.inbox, actor) if actor is not None else None
        return render(actor, inbox=inbox)

    def render_login(title, username, code, *, action="/login", error=None, status_code=200):
        return HTMLResponse(_LOGIN_PAGE.render(
            title=title, username=username, code=code if guided_demo and not error else "",
            action=action, error=error,
        ), status_code=status_code)

    @app.get("/login/{username}")
    async def login_page(request: Request, username: str):
        if username not in role_logins:
            raise HTTPException(404)
        if signed_in(request) is not None:
            return RedirectResponse("/", status_code=303)
        title, _, code = role_logins[username]
        return render_login(title, username, code)

    @app.post("/login")
    async def login(request: Request):
        nonlocal reviewer_id
        fields = await _fields(request)
        code = fields.get("code", "").encode("utf-8")
        username = fields.get("username")
        if username == "kero" and compare_digest(code, requester_code.encode("utf-8")):
            role = Role.REQUESTER
        elif username == "ellenor" and compare_digest(code, reviewer_code.encode("utf-8")):
            role = Role.REVIEWER
        else:
            title = role_logins[username][0] if username in role_logins else "Demó"
            return render_login(
                title, username or "", "", error="Érvénytelen felhasználónév vagy jelszó.", status_code=403,
            )
        if signed_in(request) is not None:
            raise HTTPException(409, "Szerepváltáshoz előbb jelentkezz ki.")
        for session_id in tuple(actors):
            if not sessions.is_active(session_id):
                del actors[session_id]
        session_id = sessions.open_session()
        actors[session_id] = Actor(role.value, session_id, role)
        if role is Role.REVIEWER:
            reviewer_id = session_id
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(session_cookie, session_id, httponly=True, samesite="strict")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        actor = require_actor(request)
        sessions.close_session(actor.session_id)
        actors.pop(actor.session_id, None)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(session_cookie)
        return response

    def current_reviewer() -> Actor | None:
        reviewer = actors.get(reviewer_id)
        if reviewer is not None and sessions.is_active(reviewer.session_id):
            return reviewer
        return None

    @app.post("/cases")
    async def start(request: Request):
        fields = await _fields(request)
        actor = require_actor(request)
        try:
            task = DemoTask(fields.get("task"))
        except ValueError:
            raise HTTPException(400, "Ismeretlen demó.") from None
        reviewer = current_reviewer() if task is DemoTask.MAIL_DRAFT else None
        if task is DemoTask.MAIL_DRAFT and reviewer is None:
            raise HTTPException(409, "A levéldemóhoz előbb lépj be ellenőrként.")
        case_id = flow.start(task, actor, reviewer=reviewer)
        return RedirectResponse(f"/case/{case_id}", status_code=303)

    @app.get("/case")
    async def lookup(request: Request, id: str):
        actor = require_actor(request)
        return render(actor, id, flow.preview(actor, id))

    @app.get("/case/{case_id}")
    async def preview(request: Request, case_id: str):
        actor = require_actor(request)
        return render(actor, case_id, flow.preview(actor, case_id))

    @app.post("/case/{case_id}/decision")
    async def decide(request: Request, case_id: str):
        fields = await _fields(request)
        actor = require_actor(request)
        try:
            version = int(fields["version"])
            outcome = Outcome(fields["outcome"])
            digest = fields["digest"]
        except (KeyError, ValueError):
            raise HTTPException(400, "Érvénytelen döntés.") from None
        flow.decide(actor, case_id, version=version, digest=digest, outcome=outcome)
        return render(actor, case_id, flow.preview(actor, case_id), message="Döntés rögzítve.")

    @app.post("/case/{case_id}/send")
    async def send(request: Request, case_id: str):
        actor = require_actor(request)
        preview = flow.preview(actor, case_id)
        result = await run_in_threadpool(flow.send, actor, case_id)
        return render(actor, case_id, preview, result=result)

    @app.get("/recognizer-setup")
    async def recognizer_setup(request: Request):
        actor = require_actor(request)
        if actor.role is not Role.REQUESTER:
            raise HTTPException(403, "Ez a menüpont a kérő számára érhető el.")
        state = (
            "A modell telepítve van. Saját szövegnél automatikus névjavaslatokat ad."
            if nlp_active()
            else "A magyar névfelismerő telepítve van, de ki van kapcsolva. Az aktiváláshoz fordulj az Üzemeltetőhöz."
            if nlp_installed()
            else "A magyar névfelismerő nincs telepítve. A telepítéshez fordulj az Üzemeltetőhöz. A kézi kijelölés elérhető."
        )
        return HTMLResponse(_RECOGNIZER_PAGE.render(state=state))

    mount_sensitive_values(app, require_actor, _fields, sensitive)
    mount_own_input(
        app, require_actor, _fields, sensitive, sessions, flow, current_reviewer, operator_config,
        external_https=provider_https is not None,
    )
    if operator_code is not None:
        mount_operator(
            app, _fields, audit_path.parent / "operator", operator_code, render_login,
            guided_demo=guided_demo, cookie_suffix=cookie_suffix,
        )
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-https-target", action="append", nargs=2, metavar=("PROTOKOLL", "HTTPS_ALAPCÍM"))
    parser.add_argument("--guided-demo", action="store_true", help="Helyi bemutató előre kitöltött belépéssel")
    parser.add_argument("--runtime-dir", type=Path, default=Path(".local/futtatas/A"),
                        help="A helyi futási adatok könyvtára")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, choices=range(1024, 65536), metavar="PORT")
    args = parser.parse_args()
    targets = frozenset(tuple(target) for target in args.allow_https_target or ())
    if any(protocol not in PROTOCOLS or not endpoint.startswith("https://") for protocol, endpoint in targets):
        parser.error("Érvénytelen protokoll vagy HTTPS alapcím.")
    if args.guided_demo and targets:
        parser.error("Az előre kitöltött demóbelépés külső küldéssel nem használható.")
    provider_https = ProviderHTTPS(targets) if targets else None
    audit_dir = args.runtime_dir
    audit_dir.mkdir(parents=True, exist_ok=True)
    requester_code, reviewer_code, operator_code = (token_urlsafe(18) for _ in range(3))
    print(f"Helyi demó: http://{_LOOPBACK}:{args.port}/", flush=True)
    print(f"Kérői demókód: {requester_code}\nEllenőri demókód: {reviewer_code}", flush=True)
    print(f"Üzemeltetői demókód: {operator_code}", flush=True)
    uvicorn.run(
        create_app(
            audit_dir / "audit.sqlite3", requester_code, reviewer_code, operator_code=operator_code,
            provider_https=provider_https, guided_demo=args.guided_demo, port=args.port,
        ),
        host=_LOOPBACK, port=args.port, proxy_headers=False, access_log=False,
    )
