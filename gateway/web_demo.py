# Károly Tamás szakdolgozati prototípusa (2026).
"""Loopback-only web entry point for the fixed synthetic A-stage demo."""

import argparse
from hmac import compare_digest
from markupsafe import Markup, escape
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
from gateway.install_nlp import installation_details as nlp_details
from gateway.operator_config import OperatorConfig, PROTOCOLS
from gateway.operator_web import mount_operator
from gateway.own_input_web import mount_own_input
from gateway.provider_https import ProviderHTTPS
from gateway.role_access import Actor, Role
from gateway.session_lifecycle import SessionStore, SessionUnavailable
from gateway.sensitive_store import SensitiveStore
from gateway.sensitive_web import mount_sensitive_values
from gateway.requester_cases import DraftStore
from gateway.requester_web import mount_requester_pages
from gateway.web_layout import STYLE_HASH, TEMPLATES


_LOOPBACK = "127.0.0.1"
_DEFAULT_PORT = 8765
_COOKIE = "gateway_demo_session"


def _marked_original(origin: dict, section: str) -> Markup:
    value = origin.get(section, "")
    if not isinstance(value, str):
        return escape("")
    spans = sorted(
        (item for item in origin.get("spans", []) if item.get("section") == section),
        key=lambda item: item.get("start", -1),
    )
    parts = []
    cursor = 0
    for item in spans:
        start, end = item.get("start"), item.get("end")
        if type(start) is not int or type(end) is not int or not cursor <= start < end <= len(value):
            return escape(value)
        category = item.get("kind")
        css = "type-PERSON" if category in ("PERSON", "SZEMELY") else (
            "type-EMAIL" if category == "EMAIL" else "type-SENSITIVE"
        )
        parts.extend((escape(value[cursor:start]), Markup(f'<mark class="{css}">'),
                      escape(value[start:end]), Markup("</mark>")))
        cursor = end
    parts.append(escape(value[cursor:]))
    return Markup("").join(parts)
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
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main>
""")

_ERROR_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A művelet nem sikerült – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
<main class="workspace"><h1>A művelet nem sikerült</h1>
<section class="panel"><p role="alert">{{ message }}</p>
<p>A munkakosárból a mentett piszkozat folytatható. Ha a munkamenet lejárt, jelentkezz be újra.</p>
<p><a href="/">Vissza a kezdőlapra</a></p>
</section><footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main></html>
""")

_PAGE = TEMPLATES.from_string("""
<!doctype html>
<html lang="hu">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Privacy Gateway{{ ' – helyi demó' if not model_enabled else '' }}</title>
<style>{{ page_style }}</style>
<body>
{% from "workspace" import toolbar %}
<main{% if actor %} class="workspace"{% endif %}>
<h1>{{ 'Ellenőri ügy' if actor and not owner and case_id else ('Kérői ügy' if actor and owner and case_id else 'AI Privacy Gateway' ~ (' – helyi demó' if not model_enabled else '')) }}</h1>
{% if not actor %}
<p>{{ "A beállított modell a jóváhagyott ügy külön küldési lépésében hívható." if model_enabled else "Ez a példány helyi bemutatóválaszokat ad." }}</p>
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
{% if case_id %}
<ol class="workflow" aria-label="Feldolgozási lépések"><li>Bemenet és kijelölés</li><li aria-current="{{ 'step' if progress.can_decide else 'false' }}">Kezelt előnézet és jóváhagyás</li><li aria-current="{{ 'step' if progress.can_send and result is none else 'false' }}">Küldés</li><li aria-current="{{ 'step' if result is not none else 'false' }}">Válasz</li></ol>
<section class="panel" aria-labelledby="case-state-title">
<h2 id="case-state-title">Ügy állapota</h2>
<p>Ügyazonosító: <code>{{ case_id }}</code></p>
<p><strong>Állapot:</strong> {{ progress.status }} · <strong>Következő felelős:</strong> {{ progress.next_role }}</p>
{% if progress.can_restart %}
<form method="get" action="{{ '/own-input' if progress.kind == 'Saját szöveg' else '/samples' }}"><button class="secondary help-button" data-help="Új bemenet vagy minta kiválasztására visz. Ez a korábbi ügyet nem módosítja.">Új ügy előkészítése</button></form>
{% endif %}
<p class="muted">Döntési indokkód: {{ preview.reason_code }}</p>
</section>
{% if not owner or result is not none %}
<section class="panel" aria-labelledby="original-title"><h2 id="original-title">Eredeti bemenet és kért feladat</h2>
{% if original %}
<p>Az érzékenynek kijelölt részek színe a kategóriát jelzi. Ez a helyi nézet; a szolgáltató csak a lent látható kezelt kérést kaphatja meg.</p>
<p class="actions"><span class="type-PERSON">Személy</span><span class="type-EMAIL">E-mail</span><span class="type-SENSITIVE">Egyéb érzékeny</span></p>
<h3>Eredeti szöveg</h3><div class="sample-text">{{ original.source_html }}</div>
<h3>Pontosan kért feladat</h3><div class="sample-text">{{ original.request_html }}</div>
{% else %}<p>A korábban indított ügy eredeti szövege és kijelölései nem állíthatók vissza. A kezelt előnézet továbbra is megtekinthető.</p>{% endif %}
</section>
{% endif %}
<section class="panel" aria-labelledby="case-preview-title">
<h2 id="case-preview-title">Kezelt előnézet</h2>
<p>A továbbítandó tartalmat itt ellenőrizheted. A külső modell csak jóváhagyás és külön küldés után kapja meg.</p>
<pre>{{ preview.body.decode("utf-8") }}</pre>
{% if preview.provider_body %}
<details{% if result is not none or not owner %} open{% endif %}><summary>{{ "A szolgáltatónak elküldött kérés pontos törzse" if result is not none and preview.external_https else "A szolgáltatónak szánt kérés pontos törzse" }}</summary>
<pre id="provider-body">{{ preview.provider_body.decode("utf-8") }}</pre>
<p>{{ "Külső HTTPS-küldés a megadott szolgáltatóhoz." if preview.external_https else "Helyi próba szolgáltatói válaszformátummal." }}</p></details>
{% endif %}
</section>
<section class="panel" aria-labelledby="case-action-title">
<h2 id="case-action-title">Következő művelet</h2>
{% if message %}<p role="status">{{ message }}</p>{% endif %}
{% if result is not none %}<h3>{{ "Modellválasz" if preview.external_https else "Helyi válasz" }}</h3><pre>{{ result }}</pre>{% endif %}
{% if progress.can_decide %}
<p>{{ "Ellenőrizd a kezelt tartalmat, majd döntsd el, továbbengedhető-e." if not owner else "Erősítsd meg, hogy a kezelt tartalom továbbengedhető." }}</p>
<form method="post" action="{{ case_path }}/decision" class="actions">
<input type="hidden" name="version" value="{{ preview.version }}">
<input type="hidden" name="digest" value="{{ preview.digest }}">
<button class="help-button" name="outcome" value="approved" data-help="Rögzíti a jóváhagyást. A modell hívása ettől még nem indul el.">{{ "Megerősítés" if owner else "Jóváhagyás" }}</button>
<button class="secondary help-button" name="outcome" value="returned" data-help="Az ügyet javításra visszaadja. Külső küldés nem történik.">Visszaadás javításra</button>
</form>
{% endif %}
{% if progress.can_send and result is none %}
<p>{{ "A jóváhagyott, kezelt kérést a kiválasztott külső modellhez küldi, majd a választ helyben visszaállítja." if preview.external_https else "A jóváhagyott kérést helyi próbaként futtatja." }}</p>
<form method="post" action="{{ case_path }}/send"><button class="help-button" data-help="{{ 'Ez indítja az egyetlen külső modellhívást a jóváhagyott tartalommal.' if preview.external_https else 'Helyi választ készít; külső modellhez nem küld kérést.' }}">{{ "Küldés a szolgáltatóhoz" if preview.external_https else ("Helyi kapupróba" if preview.reason_code == "OWN_REVIEW_REQUIRED" else "Helyi futtatás") }}</button></form>
{% endif %}
</section>
{% endif %}
{% endif %}
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main>
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
<h2>Mire szolgál?</h2>
<p>A helyi magyar névfelismerő személyneveket javasol kijelölésre a saját szövegben. A kijelölést a Kérő ellenőrzi és módosíthatja; bármit kézzel is érzékenynek jelölhet. Ez az oldal állapotot mutat, telepíteni és beállítani az Üzemeltető tudja.</p>
<p role="status">{{ state }}</p>
{% if details %}
<dl>
<dt>Névfelismerő adatállomány</dt><dd>{{ details.name }} {{ details.model_version }}</dd>
<dt>Futtatókönyvtár</dt><dd>spaCy {{ details.spacy_version }}</dd>
<dt>Telepítés ideje</dt><dd>{{ details.installed_at }} · {{ details.time_source }}</dd>
<dt>Betöltés módja</dt><dd>{{ details.load_method }}</dd>
<dt>Utolsó sikeres betöltést követő kérés</dt><dd>{{ details.last_loaded if details.last_loaded else "Még nincs rögzített sikeres névjavaslatkérés." }}</dd>
</dl>
<p>Az időpontot a sikeresen befejezett helyi felismerés után rögzítjük.</p>
{% endif %}
<form method="get" action="/recognizer-setup"><button class="secondary">Állapot frissítése</button></form>
</section>
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main></html>
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
    provider_https: ProviderHTTPS | None = None, guided_demo: bool = False,
    prefill_role_logins: bool = False, port: int = _DEFAULT_PORT,
) -> FastAPI:
    host = f"{_LOOPBACK}:{port}"
    origin = f"http://{host}"
    cookie_suffix = "" if port == _DEFAULT_PORT else f"_{port}"
    session_cookies = {
        Role.REQUESTER: _COOKIE + "_requester" + cookie_suffix,
        Role.REVIEWER: _COOKIE + "_reviewer" + cookie_suffix,
    }
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
        "ellenor": ("Ellenőr", "A hozzárendelt ügy eredeti és kezelt kérésének összevetése, majd döntés.", reviewer_code),
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
    drafts = DraftStore(sessions, audit_path.parent / "drafts.sqlite3")
    operator_config = OperatorConfig(audit_path.parent / "operator")
    flow = DemoFlow(sessions, audit_path, operator_config=operator_config, provider_https=provider_https)
    sensitive = SensitiveStore(audit_path.parent / "sensitive" / "values.sqlite3")
    actors: dict[str, Actor] = {}
    assigned_reviewer = Actor(Role.REVIEWER.value, "assigned-reviewer", Role.REVIEWER)
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

    def signed_in(request: Request, role: Role | None = None) -> Actor | None:
        for selected in (role,) if role is not None else (Role.REQUESTER, Role.REVIEWER):
            session_id = request.cookies.get(session_cookies[selected], "")
            actor = actors.get(session_id)
            if actor is not None and not sessions.is_active(session_id):
                actors.pop(session_id, None)
                continue
            if actor is not None and actor.role is selected:
                return actor
        return None

    def require_actor(request: Request) -> Actor:
        path = request.url.path
        if path.startswith("/review/"):
            actor = signed_in(request, Role.REVIEWER)
        elif path.startswith("/requester/"):
            actor = signed_in(request, Role.REQUESTER)
        elif path in ("/inbox", "/case") or path.startswith("/case/"):
            requester = signed_in(request, Role.REQUESTER)
            reviewer = signed_in(request, Role.REVIEWER)
            if requester is not None and reviewer is not None:
                raise HTTPException(409, "Ez a régi ügycím több szerepnél nem egyértelmű. Nyisd meg az ügyet a szereped munkakosarából.")
            actor = requester or reviewer
        else:
            actor = signed_in(request, Role.REQUESTER)
        if actor is None:
            raise HTTPException(401, "Jelentkezz be a demóhoz.")
        return actor

    def render(actor=None, case_id=None, preview=None, message=None, result=None, *, inbox=None):
        resolved_result = flow.result(actor, case_id) if result is None and case_id else result
        original = None
        if actor is not None and case_id is not None and (
            actor.role is Role.REVIEWER or resolved_result is not None
        ):
            saved = flow.original(actor, case_id)
            if saved is not None:
                original = {
                    "source_html": _marked_original(saved, "source"),
                    "request_html": _marked_original(saved, "request"),
                }
        return HTMLResponse(_PAGE.render(
            actor=actor, owner=actor is not None and actor.role is Role.REQUESTER,
            case_id=case_id, preview=preview, message=message,
            result=resolved_result, original=original,
            case_path=(f"/review/case/{case_id}" if actor and actor.role is Role.REVIEWER else f"/requester/case/{case_id}") if case_id else "",
            login_tiles=login_tiles, inbox=inbox, model_enabled=provider_https is not None,
            progress=flow.progress(actor, case_id) if case_id else None,
        ))

    @app.exception_handler(PermissionError)
    @app.exception_handler(SessionUnavailable)
    async def blocked(_request, _error):
        return HTMLResponse(
            'A művelet blokkolva. <a href="/">Vissza a kezdőlapra</a>', status_code=403
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request, error: HTTPException):
        message = error.detail if isinstance(error.detail, str) and error.status_code < 500 else "A művelet jelenleg nem érhető el."
        return HTMLResponse(_ERROR_PAGE.render(message=message), status_code=error.status_code)

    @app.get("/")
    async def home(request: Request):
        actor = signed_in(request)
        if actor is not None:
            return RedirectResponse("/samples" if actor.role is Role.REQUESTER else "/review/inbox", status_code=303)
        return render()

    def render_login(title, username, code, *, action="/login", error=None, status_code=200):
        prefill = guided_demo or (prefill_role_logins and username in (*role_logins, "uzemelteto"))
        return HTMLResponse(_LOGIN_PAGE.render(
            title=title, username=username, code=code if prefill and not error else "",
            action=action, error=error,
        ), status_code=status_code)

    @app.get("/login/{username}")
    async def login_page(request: Request, username: str):
        if username not in role_logins:
            raise HTTPException(404)
        role = Role.REQUESTER if username == "kero" else Role.REVIEWER
        if signed_in(request, role) is not None:
            return RedirectResponse("/samples" if role is Role.REQUESTER else "/review/inbox", status_code=303)
        title, _, code = role_logins[username]
        return render_login(title, username, code)

    @app.post("/login")
    async def login(request: Request):
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
        if signed_in(request, role) is not None:
            raise HTTPException(409, "Szerepváltáshoz előbb jelentkezz ki.")
        for session_id in tuple(actors):
            if not sessions.is_active(session_id):
                del actors[session_id]
        session_id = sessions.open_session()
        actors[session_id] = Actor(role.value, session_id, role)
        response = RedirectResponse("/samples" if role is Role.REQUESTER else "/review/inbox", status_code=303)
        response.set_cookie(session_cookies[role], session_id, httponly=True, samesite="strict")
        return response

    @app.post("/logout")
    @app.post("/review/logout")
    async def logout(request: Request):
        actor = require_actor(request)
        sessions.close_session(actor.session_id)
        actors.pop(actor.session_id, None)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(session_cookies[actor.role])
        return response

    @app.post("/cases")
    async def start(request: Request):
        fields = await _fields(request)
        actor = require_actor(request)
        try:
            task = DemoTask(fields.get("task"))
        except ValueError:
            raise HTTPException(400, "Ismeretlen demó.") from None
        reviewer = assigned_reviewer if task is DemoTask.MAIL_DRAFT else None
        case_id = flow.start(task, actor, reviewer=reviewer)
        return RedirectResponse(f"/requester/case/{case_id}", status_code=303)

    @app.get("/case")
    @app.get("/requester/case")
    async def lookup(request: Request, id: str):
        actor = require_actor(request)
        return render(actor, id, flow.preview(actor, id))

    @app.get("/case/{case_id}")
    @app.get("/requester/case/{case_id}")
    @app.get("/review/case/{case_id}")
    async def preview(request: Request, case_id: str):
        actor = require_actor(request)
        return render(actor, case_id, flow.preview(actor, case_id))

    @app.post("/case/{case_id}/decision")
    @app.post("/requester/case/{case_id}/decision")
    @app.post("/review/case/{case_id}/decision")
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
    @app.post("/requester/case/{case_id}/send")
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
        return HTMLResponse(_RECOGNIZER_PAGE.render(state=state, details=nlp_details()))

    mount_requester_pages(app, require_actor, _fields, flow, drafts, assigned_reviewer)
    mount_sensitive_values(app, require_actor, _fields, sensitive)
    mount_own_input(
        app, require_actor, _fields, sensitive, sessions, flow, assigned_reviewer, operator_config, drafts,
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
    parser.add_argument("--prefill-role-logins", action="store_true",
                        help="Mindhárom szerep demóbelépése előre kitöltve a helyi példányban")
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
            provider_https=provider_https, guided_demo=args.guided_demo,
            prefill_role_logins=args.prefill_role_logins, port=args.port,
        ),
        host=_LOOPBACK, port=args.port, proxy_headers=False, access_log=False,
    )
