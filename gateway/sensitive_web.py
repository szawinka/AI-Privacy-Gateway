"""Requester-only dictionary management. Search values stay out of URLs."""

import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from gateway.role_access import AccessDenied, Role
from gateway.sensitive_store import SensitiveStore
from gateway.text_processing import InvalidSelection
from gateway.token_mapping import InvalidToken

from gateway.web_layout import TEMPLATES


_PAGE = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mentett érzékeny elemek</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace"><h1>Mentett érzékeny elemek</h1>
{{ toolbar("Kérő", "/sensitive-values") }}
{% if message %}<p role="status">{{ message }}</p>{% endif %}
<section class="panel actions">
<form class="actions" method="post" action="/sensitive-values/search">
<label>Keresés <input name="query" value="{{ query }}" maxlength="4000"></label>
<button>Szűrés</button>
</form>
{% if query %}<form method="get" action="/sensitive-values"><button class="secondary">Szűrés törlése</button></form>{% endif %}
</section>
<p>Találatok: {{ rows | length }}</p>
{% for id, value, kind in rows %}
<form class="panel fields" method="post" action="/sensitive-values/{{ id }}">
<label>Érték<br><textarea name="value" maxlength="4000" required>
{{ value }}</textarea></label>
<label>Kategória <select name="kind">
{% for key, title in [('SENSITIVE', 'Érzékeny'), ('PERSON', 'Személy'), ('EMAIL', 'E-mail')] %}
<option value="{{ key }}"{% if key == kind %} selected{% endif %}>{{ title }}</option>
{% endfor %}
</select></label>
<button>Módosítás mentése</button>
<button class="danger" formaction="/sensitive-values/{{ id }}/delete" formnovalidate>Törlés</button>
</form>
{% endfor %}

</main></html>
""")


def mount_sensitive_values(app: FastAPI, require_actor, read_fields, store: SensitiveStore) -> None:
    def requester(request):
        if require_actor(request).role is not Role.REQUESTER:
            raise AccessDenied("Requester access required")

    def render(query="", message=None, status=200):
        try:
            rows = store.entries(query)
        except sqlite3.Error:
            rows, message, status = [], "A helyi szótár nem érhető el.", 503
        return HTMLResponse(_PAGE.render(rows=rows, query=query, message=message), status)

    @app.get("/sensitive-values")
    async def values(request: Request):
        requester(request)
        return render()

    @app.post("/sensitive-values/search")
    async def search(request: Request):
        requester(request)
        fields = await read_fields(request, limit=60_000)
        return render(fields.get("query", ""))

    @app.post("/sensitive-values/{item_id}")
    async def update(request: Request, item_id: int):
        fields = await read_fields(request, limit=60_000)
        requester(request)
        try:
            store.save(fields.get("value", ""), fields.get("kind", ""), item_id)
        except (InvalidSelection, InvalidToken):
            return render(message="A módosítás nem menthető. Ellenőrizd az értéket és a duplikációt.", status=400)
        except sqlite3.Error:
            return render(message="A helyi szótár nem írható.", status=503)
        return render(message="Módosítás mentve.")

    @app.post("/sensitive-values/{item_id}/delete")
    async def delete(request: Request, item_id: int):
        requester(request)
        try:
            store.delete(item_id)
        except sqlite3.Error:
            return render(message="A helyi szótár nem írható.", status=503)
        return render(message="Bejegyzés törölve.")
