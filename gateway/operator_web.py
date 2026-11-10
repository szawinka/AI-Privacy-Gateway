"""Separate local operator sign-in and configuration pages."""

from hmac import compare_digest
from pathlib import Path
import sqlite3
from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from gateway.install_nlp import MODEL_VERSION, SPACY_VERSION, active, install, installed, remove, set_active
from gateway.operator_config import ConfigError, OperatorConfig, PROTOCOLS
from gateway.session_lifecycle import SessionStore

from gateway.web_layout import TEMPLATES


_COOKIE = "gateway_operator_session"
_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Üzemeltetés – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace"><h1>Üzemeltetés</h1>
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
{{ toolbar("Üzemeltető", "/operator/" ~ area) }}
{% if area == 'connections' %}
<h2>Modellkapcsolatok</h2>
{% for row in [none] + rows %}
<details{% if not row %} open{% endif %}><summary>{{ row.name if row else "Új modellkapcsolat" }}{% if row %} · {{ row.model }}{% endif %}</summary>
<form class="fields" method="post" action="/operator/connections{{ '/' ~ row.id if row else '' }}">
<p><label>Név <input name="name" value="{{ row.name if row else '' }}" required maxlength="128"></label></p>
<p><label>API-protokoll <select name="protocol">
{% for protocol in protocols %}<option value="{{ protocol }}"{% if row and row.protocol == protocol %} selected{% endif %}>{{ protocol }}</option>{% endfor %}
</select></label></p>
<p><label>HTTPS alapcím <input type="url" name="endpoint" value="{{ row.endpoint if row else '' }}" required maxlength="512"></label></p>
<p><label>Modellazonosító <input name="model" value="{{ row.model if row else '' }}" required maxlength="128"></label></p>
<p><label>Projekt- vagy telepítésazonosító <input name="project" value="{{ row.project if row else '' }}" maxlength="128"></label></p>
<p><label>Hitelesítés <select name="auth">
{% if not guided_demo %}<option value="api-key">API-kulcs</option>{% endif %}
<option value="none"{% if row and row.auth == 'none' %} selected{% endif %}>Hitelesítés nélkül</option>
</select></label></p>
{% if not guided_demo %}<p>Mentett API-kulcs: {{ "van" if row and row.has_key else "nincs" }}</p>
<p><label>Új API-kulcs <input type="password" name="api_key" autocomplete="new-password" maxlength="2048"></label></p>
{% if row and row.has_key %}<p><label><input type="checkbox" name="remove_key" value="yes"> Mentett kulcs törlése</label></p>{% endif %}{% else %}<p>Ebben a helyi bemutatóban API-kulcs megadása nem elérhető.</p>{% endif %}
<p><label><input type="checkbox" name="enabled" value="yes"{% if row and row.enabled %} checked{% endif %}> Engedélyezett konfiguráció</label></p>
<button>Mentés</button>
{% if row %}<button class="danger" formaction="/operator/connections/{{ row.id }}/delete" formnovalidate>Törlés</button>{% endif %}
</form>
</details>
{% endfor %}
{% else %}
<h2>Hívási profilok</h2>
{% if not connections %}<p>Hívási profilhoz modellkapcsolat szükséges. <a href="/operator/connections">Modellkapcsolatok megnyitása</a></p>{% else %}
{% for row in [none] + rows %}
<details{% if not row %} open{% endif %}><summary>{{ row.name if row else "Új hívási profil" }}{% if row %} · {{ row.max_tokens }} token · {{ row.timeout_seconds }} mp{% endif %}</summary>
<form class="fields" method="post" action="/operator/profiles{{ '/' ~ row.id if row else '' }}">
<p><label>Név <input name="name" value="{{ row.name if row else '' }}" required maxlength="128"></label></p>
<p><label>Feladat <select name="task">
<option value="summary">Dokumentum-összefoglalás</option>
<option value="mail-draft"{% if row and row.task == 'mail-draft' %} selected{% endif %}>Levéltervezet</option>
</select></label></p>
<p><label>Modellkapcsolat <select name="connection_id">
{% for item in connections %}<option value="{{ item.id }}"{% if row and row.connection_id == item.id %} selected{% endif %}>{{ item.name }}</option>{% endfor %}
</select></label></p>
<p><label>Kimeneti tokenkorlát <input type="number" name="max_tokens" min="1" max="8192" value="{{ row.max_tokens if row else 800 }}" required></label></p>
<p><label>Időkorlát (másodperc) <input type="number" name="timeout_seconds" min="1" max="120" value="{{ row.timeout_seconds if row else 30 }}" required></label></p>
<p><label>Temperature <input type="number" name="temperature" min="0" max="2" step="any" value="{{ row.temperature if row and row.temperature is not none else '' }}"></label></p>
<p><label>Top-p <input type="number" name="top_p" min="0" max="1" step="any" value="{{ row.top_p if row and row.top_p is not none else '' }}"></label></p>
<p><label>Kimeneti forma <select name="output_format"><option value="text">Szöveg</option></select></label></p>
<button>Mentés</button>
{% if row %}<button class="danger" formaction="/operator/profiles/{{ row.id }}/delete" formnovalidate>Törlés</button>{% endif %}
</form>
</details>
{% endfor %}
{% endif %}
{% endif %}

</main></html>
""")


_RECOGNIZER_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Névfelismerő telepítése – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>Magyar névfelismerő</h1>
{{ toolbar("Üzemeltető", "/operator/recognizer") }}
<section class="panel">
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<dl>
<dt>Modell</dt><dd>hu_core_news_md {{ model_version }}</dd>
<dt>Futtatókörnyezet</dt><dd>spaCy {{ spacy_version }} · CPython 3.14</dd>
</dl>
{% if ready %}
<p role="status">Telepítve · {{ "Aktív" if enabled else "Kikapcsolva" }}</p>
<form method="post" action="/operator/recognizer">
<button name="operation" value="{{ 'disable' if enabled else 'enable' }}">{{ "Kikapcsolás" if enabled else "Aktiválás" }}</button>
</form>
<details><summary>Újratelepítés és eltávolítás</summary>
<p>Az újratelepítés lecseréli a helyi példányt az ellenőrzött csomagverzióra.</p>
{% else %}<p role="status">A helyi névfelismerő nincs telepítve.</p>{% endif %}
<p>A telepítés letölti a magyar modellt (kb. 127 MB) és a függőségeit a projekt külön virtuális környezetébe.</p>
<form method="post" action="/operator/recognizer">
<p><label><input type="checkbox" name="consent" value="yes" required> Jóváhagyom a letöltést és telepítést.</label></p>
<button name="operation" value="{{ 'reinstall' if ready else 'install' }}">{{ "Újratelepítés" if ready else "Telepítés indítása" }}</button>
</form>
{% if ready %}
<form method="post" action="/operator/recognizer">
<p><label><input type="checkbox" name="consent" value="yes" required> Jóváhagyom a helyi modell eltávolítását.</label></p>
<button class="danger" name="operation" value="remove">Modell eltávolítása</button>
</form>
</details>
{% endif %}
</section>
</main></html>
""")


def mount_operator(
    app: FastAPI, read_fields, root: Path, code: str, render_login, *, guided_demo: bool = False,
    cookie_suffix: str = "",
) -> None:
    session_cookie = _COOKIE + cookie_suffix
    sessions = SessionStore()
    store = OperatorConfig(root)

    def require(request: Request):
        session_id = request.cookies.get(session_cookie, "")
        if not sessions.is_active(session_id):
            raise HTTPException(401, "Jelentkezz be üzemeltetőként.")
        return session_id

    async def page(area=None, error=None, status=200):
        rows, connections = [], []
        try:
            if area == "connections":
                rows = await run_in_threadpool(store.list_connections)
            elif area == "profiles":
                rows = await run_in_threadpool(store.list_profiles)
                connections = await run_in_threadpool(store.list_connections)
        except (OSError, sqlite3.Error):
            error, status = "A helyi konfigurációs tár nem érhető el.", 503
        return HTMLResponse(
            _PAGE.render(
                area=area, rows=rows, connections=connections, protocols=PROTOCOLS, error=error,
                guided_demo=guided_demo,
            ),
            status,
        )

    @app.get("/operator")
    async def login_page(request: Request):
        if sessions.is_active(request.cookies.get(session_cookie, "")):
            return RedirectResponse("/operator/connections", status_code=303)
        return render_login("Üzemeltető", "uzemelteto", code, action="/operator/login")

    @app.post("/operator/login")
    async def login(request: Request):
        fields = await read_fields(request)
        if fields.get("username") != "uzemelteto" or not compare_digest(
            fields.get("code", "").encode("utf-8"), code.encode("utf-8")
        ):
            return render_login(
                "Üzemeltető", "uzemelteto", "", action="/operator/login",
                error="Érvénytelen felhasználónév vagy jelszó.", status_code=403,
            )
        sessions.close_session(request.cookies.get(session_cookie, ""))
        response = RedirectResponse("/operator/connections", status_code=303)
        response.set_cookie(session_cookie, sessions.open_session(), path="/operator", httponly=True, samesite="strict")
        return response

    @app.post("/operator/logout")
    async def logout(request: Request):
        sessions.close_session(require(request))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(session_cookie, path="/operator")
        return response

    def recognizer_page(error=None, status_code=200):
        return HTMLResponse(_RECOGNIZER_PAGE.render(
            ready=installed(), enabled=active(), error=error,
            model_version=MODEL_VERSION, spacy_version=SPACY_VERSION,
        ), status_code=status_code)

    @app.get("/operator/recognizer")
    async def recognizer(request: Request):
        require(request)
        return recognizer_page()

    @app.post("/operator/recognizer")
    async def maintain_recognizer(request: Request):
        require(request)
        fields = await read_fields(request)
        require(request)
        operation = fields.get("operation", "install")
        if operation not in ("enable", "disable", "install", "reinstall", "remove"):
            return recognizer_page("Ismeretlen művelet.", 400)
        if operation in ("install", "reinstall", "remove") and fields.get("consent") != "yes":
            return recognizer_page("A művelethez jóváhagyás szükséges.", 400)
        try:
            if operation in ("enable", "disable"):
                await run_in_threadpool(set_active, operation == "enable")
            elif operation == "remove":
                await run_in_threadpool(remove)
            elif operation == "reinstall":
                await run_in_threadpool(install, replace=True)
            else:
                await run_in_threadpool(install)
        except (OSError, RuntimeError, CalledProcessError):
            return recognizer_page("A művelet sikertelen. Ellenőrizd a kiszolgáló terminálját.", 503)
        return RedirectResponse("/operator/recognizer", status_code=303)

    @app.get("/operator/{area}")
    async def listing(request: Request, area: str):
        require(request)
        if area not in ("connections", "profiles"):
            raise HTTPException(404)
        return await page(area)

    @app.post("/operator/{area}")
    @app.post("/operator/{area}/{item_id}")
    @app.post("/operator/{area}/{item_id}/{action}")
    async def update(request: Request, area: str, item_id: str | None = None, action: str = "save"):
        if area not in ("connections", "profiles") or action not in ("save", "delete"):
            raise HTTPException(404)
        require(request)
        try:
            if action == "delete":
                operation = store.delete_connection if area == "connections" else store.delete_profile
                await run_in_threadpool(operation, item_id)
            else:
                fields = await read_fields(request, limit=16_000, max_fields=12)
                require(request)
                if guided_demo and area == "connections" and (
                    fields.get("api_key") or fields.get("auth") != "none"
                ):
                    raise ConfigError("A helyi bemutatóban API-kulcs nem menthető.")
                operation = store.save_connection if area == "connections" else store.save_profile
                await run_in_threadpool(operation, fields, item_id)
        except ConfigError as error:
            return await page(area, str(error), 400)
        except (OSError, sqlite3.Error):
            return await page(area, "A helyi konfigurációs tár nem írható.", 503)
        return RedirectResponse(f"/operator/{area}", status_code=303)
