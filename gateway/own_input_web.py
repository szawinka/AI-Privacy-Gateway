# Károly Tamás szakdolgozati prototípusa (2026).
"""Local two-step browser UI for manually masking own text."""

from hmac import compare_digest, digest
from secrets import token_bytes
import sqlite3
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from gateway.demo_flow import DemoFlow
from gateway.document_input import DocumentRejected, extract_document, read_upload, validate_text
from gateway.name_suggestions import suggest_names
from gateway.operator_config import ConfigError, OperatorConfig

from gateway.own_input import OwnInput
from gateway.role_access import AccessDenied, Actor, Role, Risk
from gateway.sensitive_store import SensitiveStore
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import InvalidSelection
from gateway.token_mapping import InvalidToken

from gateway.web_layout import TEMPLATES
from gateway.requester_cases import DraftStore, samples, RISK_LABELS


_PAGE = TEMPLATES.from_string("""
<!doctype html>
<html lang="hu">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ 'Minta előkészítése' if sample else 'Saját szöveg' }} – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>{{ 'Minta előkészítése' if sample else 'Saját szöveg' }}</h1>
{{ toolbar("Kérő", "/samples" if sample else "/own-input") }}
{% if sample %}<a class="context-link" href="/samples?sample_id={{ sample.id }}">Vissza a kiválasztott mintához</a>{% endif %}
{% if draft_id %}<p class="muted">Piszkozat azonosítója: <code>{{ draft_id }}</code></p>{% endif %}
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<section class="panel">
{% if preview %}
<ol class="workflow" aria-label="Feldolgozási lépések"><li>Bemenet</li><li>Kijelölés</li><li aria-current="step">Helyi előnézet</li><li>Jóváhagyás és küldés</li></ol>
<h2>{{ "Tokenizált előnézet" if tokenized else "Maszkolt előnézet" }}</h2>
<h3>Forrás</h3><pre>{{ preview[0] }}</pre>
<h3>Kérés</h3><pre>{{ preview[1] }}</pre>
<p>Ellenőrizd mindkét szövegrészt. Ez a lépés helyi előnézetet készít.</p>
{% elif draft %}
<ol class="workflow" aria-label="Feldolgozási lépések"><li>Bemenet</li><li aria-current="step">Kijelölés és előkészítés</li><li>Jóváhagyás</li><li>Küldés</li></ol>
<h2>1. Feladat és bemenet</h2>
<p>{{ "E-mail-választervezet" if task == "mail-draft" else "Dokumentum-összefoglalás" }}</p>
<p>Jelölj ki egy részt a szövegmezőben, válaszd ki a típusát, majd add hozzá. A <strong>Megjegyzés későbbre</strong> külön a helyi érzékenyérték-tárba ment.</p>
<p class="actions"><span class="type-PERSON">Személy</span> <span class="type-EMAIL">E-mail</span> <span class="type-SENSITIVE">Egyéb érzékeny</span></p>
<p role="status">{{ suggestion_note }}</p>
{% if sample %}<p>Szintetikus minta: {{ sample.id }}. {{ "Külön ellenőri döntés szükséges." if needs_reviewer else "Kérői megerősítés szükséges." }}</p>{% endif %}

<form id="selection-form" method="post" action="/own-input/start">
{% for name, title, section in [('source', 'Forrás', draft.source), ('request', 'Kérés', draft.request)] %}
<fieldset class="input-block"><legend>{{ title }}</legend>
<label>{{ title }} szövege<br><textarea name="{{ name }}" readonly rows="6" cols="68">
{{ section.text }}</textarea></label>
<div class="selection-preview sample-text" data-section="{{ name }}" aria-label="{{ title }} színes előnézete"></div>
<div class="selection-tools"><label>{{ title }} kijelölésének típusa <select id="kind-{{ name }}"><option value="PERSON">Személy</option><option value="EMAIL">E-mail</option><option value="SENSITIVE">Egyéb érzékeny</option></select></label>
<button class="secondary help-button" type="button" data-section="{{ name }}" data-kind-select="kind-{{ name }}" data-help="A szövegmezőben kijelölt részt hozzáadja az ellenőrizhető jelölésekhez.">Kijelölés hozzáadása</button>
<button class="secondary help-button" type="button" data-preview="{{ name }}" data-help="A teljes szöveget és a jelöléseket nagy, görgethető ablakban mutatja.">Nagy előnézet</button></div>
</fieldset>
{% endfor %}
<fieldset><legend>2. Kijelölések és mentés</legend>
<p>A listában ellenőrizheted, törölheted és külön a helyi szótárba mentheted az egyes kijelöléseket.</p>
<input type="hidden" name="draft_id" value="{{ draft_id or '' }}">
<input type="hidden" name="stamp" value="{{ stamp }}">
<input type="hidden" name="selections" value="{{ suggestions | tojson | forceescape }}">
<ul id="marked" class="compact-selection"></ul>
<p id="selection-status" role="status"></p>
<button class="secondary help-button" type="button" id="clear" data-help="Csak ezen piszkozat aktuális jelöléseit törli. A korábban külön megjegyzett érzékeny értékeket nem érinti.">Összes jelölés törlése</button>
<p>Az azonos értékhez közös tokent kérhetsz, ha az előfordulásokhoz ugyanazt a csoportkódot adod meg. Üres kóddal külön tokent kapnak.</p>
</fieldset>
<fieldset><legend>3. Kezelés és helyi előnézet</legend>
<p>Válaszd ki, hogyan helyettesítse az alkalmazás a jelölt részeket. Az előnézet helyben készül, külső kérést nem indít.</p>
<label>Kezelési mód <select name="treatment"><option value="token">Tokenizálás – következetes, helyben visszaállítható azonosítók</option><option value="mask">Maszkolás – a jelölt tartalom közös típusjelölőre cserélődik</option></select></label>
<div class="action-row"><button class="secondary help-button" type="submit" formnovalidate formaction="/own-input/preview-selected" data-help="A kiválasztott kezelést a forrásra és a kérésre helyben alkalmazza. A piszkozat szerkeszthető marad.">Kezelt előnézet megtekintése</button><p>Ez még nem hoz létre jóváhagyásra váró ügyet.</p></div>
</fieldset>
<fieldset class="primary-action"><legend>4. Jóváhagyásra előkészítés</legend>
{% if model_required %}
<h3>Külső modellválasz</h3>
<p>Ez a fejlesztési minta a kiválasztott modellhez készül. A kezelt szöveg a jóváhagyási lépések után küldhető.</p>
{% if not external_https %}<p role="status">Ez a helyi demó nem használ modellkapcsolatot. A mentett beállítások a modellképes alkalmazásban érhetők el. A két példány piszkozatai nem közösek.</p>
{% elif not profiles %}<p role="status">Ehhez a feladathoz nincs engedélyezett modellprofil. Az Üzemeltető tudja beállítani.</p>{% endif %}
{% else %}
<h3>Helyi folyamatpróba</h3><p>A profil nélküli próba helyi visszaigazolást ad.</p>
{% endif %}
{% if needs_reviewer %}<p>Az ügy indítás után az Ellenőr munkakosarába kerül. Az Ellenőr később is bejelentkezhet és dönthet.</p>{% else %}<p>A minta kezelt előnézetét a kérő erősíti meg.</p>{% endif %}
{% if profiles %}
<label>{{ "Modellprofil" if model_required else "Hívási profil" }}
<select name="profile_id"{% if model_required %} required{% endif %}>
<option value="">{{ "Válassz modellprofilt" if model_required else "Helyi kapupróba profil nélkül" }}</option>
{% for profile in profiles %}<option value="{{ profile.profile_id }}">{{ profile.name }} · {{ profile.model }}</option>{% endfor %}
</select></label>
{% if not model_required %}<p>{{ "A kiválasztott profillal a jóváhagyott külső modell hívható. Üres választással helyi kapupróba készül." if external_https else "A kiválasztott profil helyi szolgáltató-helyettesítőt használ. Üres választással helyi kapupróba készül." }}</p>{% endif %}
{% endif %}
<div class="action-row"><button class="help-button" type="submit"{% if model_required and (not profiles or not external_https) %} disabled{% endif %} data-help="A kijelölésekből és a választott kezelési módból ügyet készít. A külső modell csak később, jóváhagyás és külön küldés után kap kérést.">{{ "Ügy átadása az ellenőrnek" if needs_reviewer else "Ügy előkészítése megerősítésre" }}</button></div>
<p class="muted">Következő képernyő: kezelt ügyelőnézet. A modellt ezen a gombon még nem hívjuk.</p>
</fieldset>
<fieldset><legend>5. Későbbi folytatás</legend>
<p>A munkakosárból később visszatérhetsz a piszkozathoz.</p>
<button class="secondary help-button" formaction="/own-input/save" formnovalidate data-help="A jelenlegi kijelöléseket menti, majd a munkakosárhoz visz. Külső kérés nem indul.">Piszkozat mentése a munkakosárba</button>
</fieldset>
</form>
<dialog id="document-preview" aria-labelledby="preview-title">
<h2 id="preview-title">Szöveges előnézet</h2><p>A jelölések szerkesztéséhez zárd be az előnézetet.</p>
<div id="preview-content" class="sample-text"></div>
<form method="dialog"><button>Bezárás</button></form>
</dialog>
<script src="/own-input.js" defer></script>
{% else %}
<ol class="workflow" aria-label="Feldolgozási lépések"><li aria-current="step">Bemenet</li><li>Kijelölés és előkészítés</li><li>Jóváhagyás</li><li>Küldés és válasz</li></ol>
<h2>Új szöveg megadása</h2>
<p>Válassz feladatot. Másold be a forrást, vagy tölts fel TXT/DOCX fájlt. A kérésben írd le, milyen választ szeretnél.</p>
<form class="fields" method="post" action="/own-input">
<label>Feladat <select name="task">
<option value="summary">Dokumentum-összefoglalás</option>
<option value="mail-draft">E-mail-választervezet</option>
</select></label>
<label>Forrás <textarea name="source" rows="6" maxlength="8000" required></textarea></label>
<label>Kérés <textarea name="request" rows="4" maxlength="8000" required></textarea></label>
<p>A <strong>Tovább</strong> megnyitja a kijelölésszerkesztőt. A <strong>Piszkozat mentése</strong> a munkakosárba teszi a bevitt szöveget.</p>
<div class="actions"><button class="help-button" data-help="A forrást és a kérést helyben megnyitja a kijelölésszerkesztőben. Külső modellhívás nem indul.">Tovább a kijelöléshez</button>
<button class="secondary help-button" name="save_only" value="yes" data-help="A szöveget piszkozatként a munkakosárba menti. Később folytatható.">Piszkozat mentése</button></div>
</form>
<details><summary>Forrás feltöltése fájlból</summary>
<p>UTF-8 TXT vagy egyszerű DOCX választható, legfeljebb 1 MiB méretben és 8000 kinyert karakterrel. A feltöltés után szöveges előnézet nyílik meg.</p>
<form class="fields" method="post" action="/own-input/upload" enctype="multipart/form-data">
<label>Feladat <select name="task"><option value="summary">Dokumentum-összefoglalás</option><option value="mail-draft">E-mail-választervezet</option></select></label>
<label>Forrásfájl <input type="file" name="document" accept=".txt,.docx" required></label>
<label>Kérés <textarea name="request" rows="3" maxlength="8000" required></textarea></label>
<button class="help-button" data-help="A helyi TXT vagy DOCX szövegét kinyeri és a kijelölésszerkesztőben nyitja meg. A fájlt nem tölti fel külső szolgáltatóhoz.">Fájl megnyitása és előnézet</button>
</form>
</details>
{% endif %}
</section>
{% if resume_id %}<form method="get" action="/own-input">
<input type="hidden" name="draft_id" value="{{ resume_id }}"><button class="secondary">Vissza a piszkozathoz</button></form>{% endif %}
{% if (preview or draft) and not sample %}<form method="get" action="/own-input"><button class="secondary help-button" data-help="Üres saját szöveges űrlapot nyit. A jelenlegi piszkozatot a munkakosárban találod, ha mentetted.">Új saját szöveg megadása</button></form>{% endif %}
<footer class="credit">Károly Tamás szakdolgozati prototípusa · 2026</footer></main>
</html>
""")

_SCRIPT = """
const form = document.querySelector("#selection-form");
const marked = document.querySelector("#marked");
const feedback = document.querySelector("#selection-status");
const selections = JSON.parse(form.elements.selections.value);
function renderHighlights(target, section) {
    const points = Array.from(form.elements.namedItem(section).value);
    const selected = selections.filter(item => item.section === section).sort((a, b) => a.start - b.start);
    const nodes = [];
    let cursor = 0;
    for (const item of selected) {
        nodes.push(document.createTextNode(points.slice(cursor, item.start).join("")));
        const mark = document.createElement("mark");
        mark.className = "type-" + item.kind;
        mark.textContent = points.slice(item.start, item.end).join("");
        mark.title = {PERSON: "Személy", EMAIL: "E-mail", SENSITIVE: "Egyéb érzékeny"}[item.kind];
        nodes.push(mark);
        cursor = item.end;
    }
    nodes.push(document.createTextNode(points.slice(cursor).join("")));
    target.replaceChildren(...nodes);
}
function update() {
    for (const target of document.querySelectorAll(".selection-preview")) {
        renderHighlights(target, target.dataset.section);
    }
    form.elements.selections.value = JSON.stringify(selections);
    marked.replaceChildren(...selections.map((item, index) => {
        const row = document.createElement("li");
        const section = item.section === "source" ? "Forrás" : "Kérés";
        const kind = {PERSON: "személy", EMAIL: "e-mail", SENSITIVE: "érzékeny"}[item.kind];
        row.textContent = `${section}: ${kind}, ${item.start + 1}–${item.end}. karakter`;
        const text = form.elements.namedItem(item.section);
        const points = Array.from(text.value);
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.className = "secondary help-button";
        inspect.textContent = "Kijelölés megmutatása";
        inspect.dataset.help = "A kijelölt értéket megmutatja a szövegmezőben.";
        inspect.addEventListener("click", () => {
            text.focus();
            text.setSelectionRange(
                points.slice(0, item.start).join("").length,
                points.slice(0, item.end).join("").length
            );
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary help-button";
        remove.textContent = "Törlés";
        remove.dataset.help = "Ezt az egy jelölést törli a piszkozatból.";
        remove.addEventListener("click", () => {
            selections.splice(index, 1);
            feedback.textContent = "Kijelölés törölve. Hozzáadhatsz helyette új kijelölést.";
            update();
        });
        const remember = document.createElement("button");
        remember.type = "button";
        remember.className = "secondary help-button";
        remember.textContent = "Megjegyzés későbbre";
        remember.dataset.help = "Ezt az értéket külön elmenti a helyi érzékenyérték-tárba, hogy később felismerhető legyen.";
        remember.addEventListener("click", async () => {
            const fields = new URLSearchParams(new FormData(form));
            fields.set("selections", JSON.stringify([item]));
            remember.disabled = true;
            try {
                const response = await fetch("/own-input/remember", {method: "POST", body: fields});
                feedback.textContent = response.ok ? "Érték megjegyezve." : "Az érték nem menthető.";
            } catch {
                feedback.textContent = "A mentés nem érhető el.";
            } finally {
                remember.disabled = false;
            }
        });
        const groupLabel = document.createElement("label");
        groupLabel.textContent = " Csoportkód (opcionális) ";
        const group = document.createElement("input");
        group.maxLength = 32;
        group.pattern = "[A-Za-z0-9_\\-]{0,32}";
        group.value = item.group || "";
        group.addEventListener("input", () => {
            item.group = group.value;
            form.elements.selections.value = JSON.stringify(selections);
        });
        groupLabel.append(group);
        row.append(" ", inspect, " ", remove, " ", remember, groupLabel);
        return row;
    }));
}
for (const button of form.querySelectorAll("button[data-section][data-kind-select]")) {
    button.addEventListener("click", () => {
        const {section} = button.dataset;
        const kind = document.getElementById(button.dataset.kindSelect).value;
        const text = form.elements.namedItem(section);
        const start = Array.from(text.value.slice(0, text.selectionStart)).length;
        const end = Array.from(text.value.slice(0, text.selectionEnd)).length;
        if (start === end || selections.length >= 40) {
            feedback.textContent = "Jelölj ki szöveget. Legfeljebb 40 kijelölés adható meg.";
            return;
        }
        if (selections.some(item => item.section === section && item.start < end && start < item.end)) {
            feedback.textContent = "Ez a kijelölés átfed egy korábbi kijelöléssel.";
            return;
        }
        selections.push({section, kind, start, end});
        feedback.textContent = "Kijelölés hozzáadva.";
        update();
    });
}
const dialog = document.querySelector("#document-preview");
for (const button of document.querySelectorAll("[data-preview]")) {
    button.addEventListener("click", () => {
        renderHighlights(document.querySelector("#preview-content"), button.dataset.preview);
        dialog.showModal();
    });
}
document.querySelector("#clear").addEventListener("click", () => {
    selections.length = 0;
    feedback.textContent = "Kijelölések törölve.";
    update();
});
update();
"""


def mount_own_input(
    app: FastAPI, require_actor, read_fields, store: SensitiveStore, sessions: SessionStore,
    flow: DemoFlow, assigned_reviewer: Actor, config: OperatorConfig, drafts: DraftStore,
    *, external_https: bool = False,
) -> None:
    key = token_bytes(32)

    def requester(request: Request):
        actor = require_actor(request)
        if actor.role is not Role.REQUESTER:
            raise AccessDenied("Requester access required")
        return actor

    def stamp(actor, draft):
        message = f"{actor.session_id}:{draft.source.digest}:{draft.request.digest}"
        return digest(key, message.encode("ascii"), "sha256").hex()

    async def draft_page(actor, item):
        if item.case_id:
            return RedirectResponse(f"/requester/case/{item.case_id}", status_code=303)
        if item.sample_id.startswith("A-") and not item.editable:
            return RedirectResponse(f"/sample-drafts/{item.id}", status_code=303)
        return await show_draft(actor, item)

    @app.get("/own-input")
    async def own_input(request: Request, draft_id: str = ""):
        actor = requester(request)
        if draft_id:
            return await draft_page(actor, drafts.get(actor, draft_id))
        return HTMLResponse(_PAGE.render())

    @app.get("/own-input.js")
    async def script():
        return Response(_SCRIPT, media_type="text/javascript")

    @app.post("/own-input")
    async def canonicalize(request: Request):
        fields = await read_fields(request, limit=100_000, max_fields=7)
        actor = requester(request)
        try:
            draft = OwnInput.from_fields(validate_text(fields.get("source", "")), validate_text(fields.get("request", "")))
        except (InvalidSelection, InvalidToken):
            return HTMLResponse(_PAGE.render(error="Érvénytelen szöveg. Ellenőrizd a hosszát és a jelöléseket."), 400)
        task = fields.get("task", "summary")
        if task not in ("summary", "mail-draft"):
            raise HTTPException(400, "Ismeretlen feladat.")
        item = drafts.save(actor, draft, task=task)
        if fields.get("save_only") == "yes":
            return RedirectResponse("/requester/inbox", status_code=303)
        return await show_draft(actor, item)

    @app.post("/own-input/upload")
    async def upload(request: Request):
        actor = requester(request)
        try:
            fields, filename, data = await read_upload(request)
            task = fields.get("task", "summary")
            if task not in ("summary", "mail-draft"):
                raise DocumentRejected("Ismeretlen feladat.")
            source = await run_in_threadpool(extract_document, filename, data)
            draft = OwnInput.from_fields(source, validate_text(fields.get("request", "")))
            item = drafts.save(actor, draft, task=task)
        except (InvalidSelection, InvalidToken, UnicodeError, ValueError):
            return HTMLResponse(_PAGE.render(
                error="A fájl nem nyitható meg. Legfeljebb 1 MiB-os UTF-8 TXT vagy egyszerű DOCX és szekciónként 8000 karakter használható. Összetett DOCX esetén adj meg egyszerű szöveges másolatot.",
            ), 400)
        requester(request)
        return await show_draft(actor, item)

    async def show_draft(actor, item, *, error=None, status_code=200):
        draft = item.draft
        sample = samples().get(item.sample_id)
        if sample is not None:
            suggestions = json.loads(item.selections)
            note = "A szintetikus minta rögzített jelölései. Ellenőrizd az előfordulásokat."
        elif item.selections != "[]":
            suggestions = json.loads(item.selections)
            note = "A piszkozat mentett jelölései."
        else:
            try:
                suggestions = await run_in_threadpool(suggest_names, draft)
            except RuntimeError:
                suggestions = []
                note = "A névfelismerő futása sikertelen. Ellenőrizd és jelöld kézzel a védendő részeket."
            else:
                if suggestions is None:
                    note = "A magyar névfelismerő nincs telepítve. A telepítéshez fordulj az Üzemeltetőhöz. A kijelölés kézzel végezhető."
                else:
                    note = f"Automatikus névjavaslatok: {len(suggestions)}. Ellenőrizd és javítsd a kijelöléseket."
            try:
                suggestions = await run_in_threadpool(store.suggestions, draft, suggestions or [])
            except sqlite3.Error:
                return HTMLResponse(_PAGE.render(error="A helyi szótár nem érhető el."), 503)
            if len(suggestions) > 40:
                return HTMLResponse(_PAGE.render(
                    error="Több mint 40 javaslat érkezett. Oszd rövidebb részekre a szöveget."
                ), 400)
        try:
            profiles = [profile for profile in await run_in_threadpool(config.enabled_profiles) if profile.task == item.task]
        except (OSError, sqlite3.Error):
            return HTMLResponse(_PAGE.render(error="A helyi modellkonfiguráció nem érhető el."), 503)
        return HTMLResponse(_PAGE.render(
            draft_id=item.id, sample=sample, task=item.task, model_required=item.model_required, error=error,
            needs_reviewer=sample is None or sample["risk"] in ("narancs", "piros"),
            draft=draft, stamp=stamp(actor, draft), suggestions=suggestions, profiles=profiles,
            suggestion_note=note, external_https=external_https,
        ), status_code=status_code)

    def checked_draft(actor, fields):
        draft = OwnInput.from_fields(validate_text(fields.get("source", "")), validate_text(fields.get("request", "")))
        if not compare_digest(fields.get("stamp", "").encode("utf-8"), stamp(actor, draft).encode("ascii")):
            raise InvalidSelection("Text revision changed")
        return draft

    @app.post("/own-input/remember")
    async def remember(request: Request):
        fields = await read_fields(request, limit=100_000, max_fields=7)
        actor = requester(request)
        try:
            value, kind = checked_draft(actor, fields).selected_value(fields.get("selections", ""))
            store.save(value, kind)
        except (InvalidSelection, InvalidToken):
            return PlainTextResponse("Érvénytelen vagy már mentett érték.", 400)
        except sqlite3.Error:
            return PlainTextResponse("A helyi szótár nem írható.", 503)
        return PlainTextResponse("Érték megjegyezve.")

    @app.post("/own-input/save")
    async def save_draft(request: Request):
        fields = await read_fields(request, limit=100_000, max_fields=7)
        actor = requester(request)
        try:
            draft = checked_draft(actor, fields)
            item = drafts.get(actor, fields.get("draft_id", ""))
            if draft != item.draft:
                raise InvalidSelection("Draft changed")
            draft.token_selections(fields.get("selections", "[]"), allow_empty=True)
            drafts.save(actor, draft, draft_id=item.id, selections=fields.get("selections", "[]"))
        except (InvalidSelection, InvalidToken):
            return HTMLResponse(_PAGE.render(error="A piszkozat nem menthető. Ellenőrizd a jelöléseket."), 400)
        return RedirectResponse("/requester/inbox", status_code=303)

    async def show_preview(request: Request, *, tokenized: bool | None = None, review: bool | None = None):
        fields = await read_fields(request, limit=100_000, max_fields=7)
        actor = requester(request)
        if tokenized is None:
            treatment = fields.get("treatment")
            if treatment not in ("token", "mask"):
                raise HTTPException(400, "Válassz érvényes kezelési módot.")
            tokenized = treatment == "token"
        if review is None:
            review = request.query_params.get("review") == "1"
        item = drafts.get(actor, fields["draft_id"]) if fields.get("draft_id") else None
        sample = samples().get(item.sample_id) if item else None
        risk = Risk[RISK_LABELS[sample["risk"]].upper()] if sample else Risk.ORANGE
        needs_reviewer = risk in (Risk.ORANGE, Risk.RED)
        reviewer = assigned_reviewer if review and needs_reviewer else None
        profile = None
        if review and fields.get("profile_id"):
            try:
                profile = await run_in_threadpool(config.profile, fields["profile_id"])
                if item and profile.task != item.task:
                    raise ConfigError("A profil más feladathoz tartozik.")
            except (ConfigError, OSError, sqlite3.Error):
                raise HTTPException(409, "A kiválasztott hívási profil nem használható.") from None
            requester(request)
        if review and item and item.model_required and (not external_https or profile is None):
            return await show_draft(
                actor, item, error="A fejlesztési modellpróbához engedélyezett külső kapcsolat és modellprofil szükséges.",
                status_code=409,
            )
        selections = fields.get("selections", "")
        try:
            draft = checked_draft(actor, fields)
            if item and draft != item.draft:
                raise InvalidSelection("Draft content changed")
            allow_empty = bool(sample and sample["risk"] == "zöld")
            if tokenized:
                source, requested = draft.token_selections(selections, allow_empty=allow_empty)
                prepared = sessions.prepare(
                    actor.session_id, draft.source, draft.request,
                    source_selections=source, request_selections=requested, check_residue=True,
                )
                preview = prepared.source, prepared.request
            else:
                preview = draft.masked(selections, allow_empty=allow_empty)
                if review:
                    source, requested = draft.selections(selections, allow_empty=allow_empty)
                    prepared = sessions.prepare_masked(
                        actor.session_id, draft.source, draft.request,
                        source_selections=source, request_selections=requested,
                    )
        except (InvalidSelection, InvalidToken):
            return HTMLResponse(_PAGE.render(
                error="Az előnézet blokkolva. Ellenőrizd a szövegváltozatot és jelölj meg minden védendő előfordulást."
            ), 400)
        if item:
            drafts.save(actor, draft, draft_id=item.id, selections=selections)
        if review:
            origin = {
                "source": draft.source.text, "request": draft.request.text,
                "spans": json.loads(selections),
            }
            operation = lambda: flow.start_own(actor, reviewer, prepared, profile=profile, risk=risk,
                                                   label=f'{sample["id"]} · {sample["title"]}' if sample else "",
                                                   origin=origin)
            case_id = drafts.submit(actor, item.id, operation) if item else operation()
            return RedirectResponse(f"/requester/case/{case_id}", status_code=303)
        return HTMLResponse(_PAGE.render(preview=preview, tokenized=tokenized, sample=sample,
                                         draft_id=item.id if item else "", resume_id=item.id if item else ""))

    @app.post("/own-input/preview")
    async def own_preview(request: Request):
        return await show_preview(request, tokenized=False)

    @app.post("/own-input/token-preview")
    async def own_token_preview(request: Request):
        return await show_preview(request, tokenized=True)

    @app.post("/own-input/preview-selected")
    async def own_selected_preview(request: Request):
        return await show_preview(request, review=False)

    @app.post("/own-input/start")
    async def own_start(request: Request):
        return await show_preview(request, review=True)
