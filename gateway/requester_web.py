# Károly Tamás szakdolgozati prototípusa (2026).
"""Requester sample catalog and role-scoped inbox."""

import json
from dataclasses import asdict

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from gateway.demo_response import DemoTask
from gateway.own_input import OwnInput
from gateway.role_access import AccessDenied, Role
from gateway.requester_cases import marked_sections, samples
from gateway.web_layout import TEMPLATES


_SAMPLES = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Szintetikus példák – AI Privacy Gateway</title><style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>Szintetikus példák</h1>{{ toolbar("Kérő", "/samples") }}
<p>Válassz mintát, nézd át a bemenetet és az elvárt választ, majd indítsd el a megfelelő próbát.</p>
<ol class="workflow" aria-label="Feldolgozási lépések"><li aria-current="step">Minta kiválasztása</li><li>Kijelölés és előkészítés</li><li>Jóváhagyás</li><li>Küldés és válasz</li></ol>
<section class="panel" aria-labelledby="choose-title">
<h2 id="choose-title">1. Minta kiválasztása</h2>
<p>A választó és a gomb az alábbi mintaelőnézetet frissíti.</p>
<form method="get" action="/samples" class="fields">
<label>Példa <select name="sample_id">{% for option in samples %}
<option value="{{ option.id }}"{% if option.id == sample.id %} selected{% endif %}>{{ option.id }} · {{ option.title }}</option>
{% endfor %}</select></label><button class="help-button" data-help="Csak a kiválasztott példa szövegét és elvárt eredményét mutatja meg. Feldolgozás nem indul.">Példa megjelenítése</button>
</form>
</section>
<section class="panel" aria-labelledby="input-title">
<h2 id="input-title">2. Bemenet áttekintése</h2>
<p><strong>{{ sample.id }} · {{ sample.title }}</strong></p>
<p>A kiemelések a referencia szerint védendő részeket mutatják.</p>
<p class="actions"><span class="type-PERSON">Személy</span><span class="type-EMAIL">E-mail</span><span class="type-SENSITIVE">Egyéb érzékeny</span></p>
{% for section, title in [("source", "Forrás"), ("request", "Felhasználói kérés")] %}
<h3>{{ title }}</h3><div class="sample-text">{% for span in sections[section] %}{% if span.category %}<mark class="{{ 'type-PERSON' if span.category == 'SZEMELY' else ('type-EMAIL' if span.category == 'EMAIL' else 'type-SENSITIVE') }}" title="{{ span.category }}">{{ span.text }}</mark>{% else %}{{ span.text }}{% endif %}{% endfor %}</div>
{% endfor %}
</section>
<section class="panel" aria-labelledby="expected-title">
<h2 id="expected-title">3. Elvárt működés és válasz</h2>
<div class="info-block">
<h3>Adatkezelés és jóváhagyás</h3>
<p>{{ handling }}</p>
<p>{{ approval_reason }}</p>
<p>{{ "Az ellenőr a kezelt előnézet alapján dönt. Jóváhagyás után a kérő indítja a futtatást." if reviewer else "A kérő a kezelt előnézet ellenőrzése után erősíti meg és indítja a futtatást." }}</p>
</div>
<div class="info-block">
<h3>A válasz tartalmi elvárásai</h3>
<ul>{% for expected in sample.expected + sample.criteria %}<li>{{ expected }}</li>{% endfor %}</ul>
<p class="muted">Ezek az utólagos ellenőrzés szempontjai.</p>
</div>
<div class="info-block">
<h3>A próba eredménye</h3>
{% if fixed %}<p>A rövid demó előre rögzített helyi választ ad. Az adatkezelést és a jóváhagyási lépéseket szemlélteti.</p>
{% else %}<p>A fejlesztési próba a beállított külső modell válaszát adja. Indítás előtt ki kell választani a feladathoz tartozó modellprofilt.</p>{% endif %}
</div>
</section>
<section class="panel" aria-labelledby="actions-title">
<h2 id="actions-title">4. Következő lépés</h2>
{% if draft_id %}
<div class="action-card">
<h3>Mentett demó indítása</h3>
<p>A rögzített helyi bemutató kezelt ügyelőnézetéhez lépsz.</p>
<form method="post" action="/sample-drafts/{{ draft_id }}/start"><button class="help-button" data-help="A mentett rögzített demóból ügyet készít. A válasz helyi bemutatóválasz.">Helyi demó előkészítése</button></form>
</div>
{% else %}
<div class="action-card primary-action">
<h3>{{ "Helyi bemutató" if fixed else "Fejlesztési modellpróba" }}</h3>
<p>{{ "A gomb elkészíti a rögzített demó kezelt ügyelőnézetét. Jóváhagyás után helyi választ kapsz." if fixed else "A szerkesztőben ellenőrizheted a jelöléseket, kiválaszthatod a kezelést és a külső modell profilját." }}</p>
<form method="post" action="/sample-start">
<input type="hidden" name="sample_id" value="{{ sample.id }}"><button class="help-button" data-help="{{ 'A rögzített helyi demóból ügyet készít; külső modell nem indul.' if fixed else 'A minta kijelöléseinek ellenőrzésére és a modellprofil kiválasztására visz. Modellhívás még nem történik.' }}">{{ 'Helyi demó előkészítése' if fixed else 'Tovább a minta előkészítéséhez' }}</button>
</form>
</div>
<div class="action-grid">
<div class="action-card">
<h3>Későbbi folytatás</h3><p>A mintát feldolgozás előtt a munkakosárba menti.</p>
<form method="post" action="/sample-drafts"><input type="hidden" name="sample_id" value="{{ sample.id }}"><button class="secondary help-button" data-help="A mintát piszkozatként a munkakosárba teszi. Feldolgozás nem indul.">Piszkozatként mentés</button></form>
</div>
{% if fixed %}
<div class="action-card">
<h3>Jelölések módosítása</h3><p>A helyi szerkesztőben saját érzékeny részeket adhatsz hozzá. Ezen az ágon saját, szerkeszthető ügy készül.</p>
<form method="post" action="/sample-edit"><input type="hidden" name="sample_id" value="{{ sample.id }}"><button class="secondary help-button" data-help="A minta szövegét a szerkesztőben nyitja meg, ahol külön érzékeny elemeket jelölhetsz és menthetsz.">Jelölések szerkesztése</button></form>
</div>
{% endif %}
</div>
{% if not fixed %}
<details><summary>Helyi folyamatpróba</summary>
<p>A kijelölést és a jóváhagyás menetét gyakorolhatod. A végén helyi visszaigazolás jelenik meg.</p>
<form method="post" action="/sample-local"><input type="hidden" name="sample_id" value="{{ sample.id }}"><button class="secondary help-button" data-help="A minta szerkesztőjét helyi folyamatpróbához nyitja meg. A végén nem küld külső modellnek.">Helyi próba előkészítése</button></form>
</details>
{% endif %}
{% endif %}
</section>
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main></html>
""")

_INBOX = TEMPLATES.from_string("""
<!doctype html><html lang="hu"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Munkakosár – AI Privacy Gateway</title><style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>Munkakosár</h1><p>Szerep: {{ role }}</p>{{ toolbar(role, inbox_path) }}
{% if role == "Ellenőr" %}<p>Nyisd meg a döntésre váró ügyet, ellenőrizd a kezelt előnézetet, majd hagyd jóvá vagy add vissza javításra. A már eldöntött ügyek is itt maradnak.</p>{% else %}<p>Az ügyek állapotát és a következő felelőst itt követheted. A jóváhagyott ügy külön küldési művelettel folytatható.</p>{% endif %}
<form method="get" action="{{ inbox_path }}" class="panel fields">
<label>Keresés azonosító vagy típus szerint <input name="query" value="{{ query }}" maxlength="100"></label>
<label>Állapot <select name="status">
<option value="">Minden állapot</option>
{% for value in statuses %}<option{% if value == status %} selected{% endif %}>{{ value }}</option>{% endfor %}
</select></label>
<div class="actions"><button>Szűrés</button><button class="secondary" name="clear" value="1">Szűrés törlése</button></div>
</form>
<p>Találatok: {{ rows | length }}</p>
{% for row in rows %}
<article class="panel">
<h2>{{ row.kind }}</h2><p><code>{{ row.case_id }}</code></p>
<p><strong>Állapot:</strong> {{ row.status }} · <strong>Következő felelős:</strong> {{ row.next_role }}</p>
<form method="get" action="{{ row.path }}">
{% if row.draft_id %}<input type="hidden" name="draft_id" value="{{ row.draft_id }}">{% endif %}
<button>{{ row.action }}</button>
</form>
</article>
{% else %}<p>A megadott feltételekhez nincs releváns ügy.</p>{% endfor %}
<p>Az ügyek és a piszkozatok a helyi futási tárban megmaradnak kijelentkezés és újraindítás után is.</p>
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main></html>
""")


def mount_requester_pages(app, require_actor, read_fields, flow, drafts, assigned_reviewer):
    catalog = samples()

    def requester(request):
        actor = require_actor(request)
        if actor.role is not Role.REQUESTER:
            raise AccessDenied("Requester required")
        return actor

    def sample_page(sample, draft_id=None):
        handling = (
            "A referenciában nincs kijelölt védendő érték."
            if sample["risk"] == "zöld" else
            "A megjelölt személyes vagy egyéb érzékeny értékeket a kiválasztott kezelés helyettesíti."
        )
        categories = {
            "SZEMELY": "személyes", "EMAIL": "e-mailes", "TELEFON": "telefonos",
            "PENZUGY": "pénzügyi", "SZERVEZET": "szervezeti", "SZERZODES": "szerződéses",
            "HR": "személyzeti", "TITOK": "titok jellegű",
        }
        context = ", ".join(categories.get(value, "egyéb érzékeny") for value in sample.get("categories", []))
        approval_reason = (
            "A nyilvános tartalmú mintához kérői megerősítés tartozik."
            if sample["risk"] == "zöld" else
            f"A minta {context} adatokat tartalmaz. A mintához rögzített próbaút "
            + ("külön ellenőri döntést igényel." if sample["risk"] in ("narancs", "piros") else "kérői megerősítést igényel.")
        )
        return HTMLResponse(_SAMPLES.render(
            sample=sample, samples=catalog.values(), fixed=sample["id"].startswith("A-"),
            sections=marked_sections(sample), reviewer=sample["risk"] in ("narancs", "piros"),
            handling=handling, approval_reason=approval_reason, draft_id=draft_id,
        ))

    @app.get("/samples")
    async def catalog_page(request: Request, sample_id: str = "A-DOC-01"):
        requester(request)
        sample = catalog.get(sample_id)
        if sample is None:
            raise HTTPException(404, "Ismeretlen szintetikus példa.")
        return sample_page(sample)

    @app.get("/sample-drafts/{draft_id}")
    async def fixed_draft_page(request: Request, draft_id: str):
        actor = requester(request)
        item = drafts.get(actor, draft_id)
        if item.case_id:
            return RedirectResponse(f"/requester/case/{item.case_id}", status_code=303)
        sample = catalog.get(item.sample_id)
        if sample is None or not sample["id"].startswith("A-"):
            raise HTTPException(404)
        return sample_page(sample, draft_id)

    @app.post("/sample-drafts/{draft_id}/start")
    async def process_fixed_draft(request: Request, draft_id: str):
        actor = requester(request)
        item = drafts.get(actor, draft_id)
        if item.case_id:
            return RedirectResponse(f"/requester/case/{item.case_id}", status_code=303)
        try:
            task = DemoTask(item.sample_id)
        except ValueError:
            raise HTTPException(400, "Ismeretlen demó.") from None
        reviewer = assigned_reviewer if task is DemoTask.MAIL_DRAFT else None
        case_id = drafts.submit(actor, draft_id, lambda: flow.start(task, actor, reviewer=reviewer))
        return RedirectResponse(f"/requester/case/{case_id}", status_code=303)

    @app.post("/sample-start")
    @app.post("/sample-local")
    @app.post("/sample-edit")
    @app.post("/sample-drafts")
    async def create_sample_draft(request: Request):
        actor = requester(request)
        fields = await read_fields(request)
        sample = catalog.get(fields.get("sample_id"))
        if sample is None:
            raise HTTPException(400, "Ismeretlen szintetikus példa.")
        if request.url.path == "/sample-start" and sample["id"].startswith("A-"):
            task = DemoTask(sample["id"])
            reviewer = assigned_reviewer if task is DemoTask.MAIL_DRAFT else None
            case_id = flow.start(task, actor, reviewer=reviewer)
            return RedirectResponse(f"/requester/case/{case_id}", status_code=303)
        selected = [
            dict(section=o["source_section"], start=o["start"], end=o["end"],
                 kind={"SZEMELY": "PERSON", "EMAIL": "EMAIL"}.get(o["category"], "SENSITIVE"),
                 group=o["protected_value_id"])
            for o in sample["occurrences"]
        ]
        item = drafts.save(
            actor, OwnInput.from_fields(sample["source"], sample["request"]),
            sample_id=sample["id"], selections=json.dumps(selected),
            editable=request.url.path in ("/sample-edit", "/sample-start", "/sample-local"),
            model_required=sample["id"].startswith("DEV-") and request.url.path != "/sample-local",
        )
        if request.url.path in ("/sample-edit", "/sample-start", "/sample-local"):
            return RedirectResponse(f"/own-input?draft_id={item.id}", status_code=303)
        return RedirectResponse("/requester/inbox", status_code=303)

    @app.get("/inbox")
    @app.get("/requester/inbox")
    @app.get("/review/inbox")
    async def inbox_page(request: Request, query: str = "", status: str = "", clear: str = ""):
        actor = require_actor(request)
        query, status = ("", "") if clear else (query[:100], status[:100])
        active = await run_in_threadpool(flow.inbox, actor)
        rows = []
        for row in active:
            values = asdict(row)
            values["path"] = (
                f"/review/case/{row.case_id}" if actor.role is Role.REVIEWER else f"/requester/case/{row.case_id}"
            )
            rows.append(values)
        for item in drafts.pending(actor):
            sample = catalog.get(item.sample_id)
            rows.insert(0, dict(
                case_id=item.id, kind=f'{sample["id"]} · {sample["title"]}' if sample else "Saját szöveg",
                status="Piszkozat – feldolgozás előtt", next_role="Kérő", action="Piszkozat folytatása",
                path=f"/sample-drafts/{item.id}" if item.sample_id.startswith("A-") and not item.editable else "/own-input",
                draft_id="" if item.sample_id.startswith("A-") and not item.editable else item.id,
            ))
        statuses = sorted({row["status"] for row in rows})
        rows = [
            row for row in rows
            if (not status or row["status"] == status)
            and query.casefold() in f'{row["case_id"]} {row["kind"]}'.casefold()
        ]
        return HTMLResponse(_INBOX.render(
            rows=rows, statuses=statuses, query=query, status=status,
            role="Kérő" if actor.role is Role.REQUESTER else "Ellenőr",
            inbox_path="/review/inbox" if actor.role is Role.REVIEWER else "/requester/inbox",
        ))
