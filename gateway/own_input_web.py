"""Local two-step browser UI for manually masking own text."""

from collections.abc import Callable
from hmac import compare_digest, digest
from secrets import token_bytes
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from gateway.demo_flow import DemoFlow
from gateway.name_suggestions import suggest_names
from gateway.operator_config import ConfigError, OperatorConfig

from gateway.own_input import OwnInput
from gateway.role_access import AccessDenied, Actor, Role
from gateway.sensitive_store import SensitiveStore
from gateway.session_lifecycle import SessionStore
from gateway.text_processing import InvalidSelection
from gateway.token_mapping import InvalidToken

from gateway.web_layout import TEMPLATES


_PAGE = TEMPLATES.from_string("""
<!doctype html>
<html lang="hu">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saját szöveg – AI Privacy Gateway</title>
<style>{{ page_style }}</style>
{% from "workspace" import toolbar %}
<main class="workspace">
<h1>Saját szöveg</h1>
{{ toolbar("Kérő", "/own-input") }}
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<section class="panel">
{% if preview %}
<h2>{{ "Tokenizált előnézet" if tokenized else "Maszkolt előnézet" }}</h2>
<h3>Forrás</h3><pre>{{ preview[0] }}</pre>
<h3>Kérés</h3><pre>{{ preview[1] }}</pre>
<p>Ellenőrizd mindkét szövegrészt. Ez a lépés helyi előnézetet készít.</p>
{% elif draft %}
<p role="status">{{ suggestion_note }}</p>
<p>Jelöld ki egérrel vagy billentyűzettel a teljes védendő értéket, majd add hozzá a megfelelő típussal. Jelöld meg az ismétlődő előfordulásokat is.</p>
<form method="post" action="/own-input/preview">
{% for name, title, section in [('source', 'Forrás', draft.source), ('request', 'Kérés', draft.request)] %}
<label>{{ title }}<br><textarea name="{{ name }}" readonly rows="6" cols="68">
{{ section.text }}</textarea></label>
<p class="actions">
<button class="secondary" type="button" data-section="{{ name }}" data-kind="PERSON">Személy megjelölése – {{ title }}</button>
<button class="secondary" type="button" data-section="{{ name }}" data-kind="EMAIL">E-mail megjelölése – {{ title }}</button>
<button class="secondary" type="button" data-section="{{ name }}" data-kind="SENSITIVE">Érzékeny rész megjelölése – {{ title }}</button>
</p>
{% endfor %}
<input type="hidden" name="stamp" value="{{ stamp }}">
<input type="hidden" name="selections" value="{{ suggestions | tojson | forceescape }}">
<ul id="marked"></ul>
<p id="selection-status" role="status"></p>
<button class="secondary" type="button" id="clear">Kijelölések törlése</button>
<p>Az azonos értékhez közös tokent kérhetsz, ha az előfordulásokhoz ugyanazt a csoportkódot adod meg. Üres kóddal külön tokent kapnak.</p>
<fieldset><legend>Helyi előnézet</legend>
<div class="actions"><button>Maszkolt előnézet</button>
<button formaction="/own-input/token-preview">Tokenizált előnézet</button></div>
</fieldset>
<fieldset><legend>Ellenőrzésre előkészítés</legend>
<p>Az ellenőri kapupróbához előbb jelentkezz be ellenőrként külön böngészőprofilban.</p>
{% if profiles %}
<label>Hívási profil
<select name="profile_id">
<option value="">Helyi kapupróba profil nélkül</option>
{% for profile in profiles %}
<option value="{{ profile.profile_id }}">{{ profile.name }} · {{ profile.model }}</option>
{% endfor %}
</select></label>
<p>{{ "A jóváhagyott célra éles HTTPS-küldés engedélyezhető az ellenőri döntés után."
        if external_https else "A hívási profilt a helyi szolgáltató-helyettesítővel lehet kipróbálni." }}</p>
{% endif %}
<div class="actions"><button formaction="/own-input/preview?review=1">Maszkolt előnézet ellenőrzésre</button>
<button formaction="/own-input/token-preview?review=1">Tokenizált előnézet ellenőrzésre</button></div>
</fieldset>
</form>
<script src="/own-input.js" defer></script>
{% else %}
<form method="post" action="/own-input">
<label>Forrás<br><textarea name="source" rows="6" cols="68" maxlength="4000" required></textarea></label><br>
<label>Kérés<br><textarea name="request" rows="4" cols="68" maxlength="4000" required></textarea></label><br>
<button>Tovább a kijelöléshez</button>
</form>
{% endif %}
</section>
{% if preview or draft %}<form method="get" action="/own-input"><button class="secondary">Új bevitel</button></form>{% endif %}
</main>
</html>
""")

_SCRIPT = """
const form = document.querySelector("form");
const marked = document.querySelector("#marked");
const feedback = document.querySelector("#selection-status");
const selections = JSON.parse(form.elements.selections.value);
function update() {
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
        inspect.textContent = "Kijelölés megmutatása";
        inspect.addEventListener("click", () => {
            text.focus();
            text.setSelectionRange(
                points.slice(0, item.start).join("").length,
                points.slice(0, item.end).join("").length
            );
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Törlés";
        remove.addEventListener("click", () => {
            selections.splice(index, 1);
            feedback.textContent = "Kijelölés törölve. Hozzáadhatsz helyette új kijelölést.";
            update();
        });
        const remember = document.createElement("button");
        remember.type = "button";
        remember.textContent = "Megjegyzés későbbre";
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
for (const button of form.querySelectorAll("[data-section]")) {
    button.addEventListener("click", () => {
        const {section, kind} = button.dataset;
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
document.querySelector("#clear").addEventListener("click", () => {
    selections.length = 0;
    feedback.textContent = "Kijelölések törölve.";
    update();
});
update();
"""


def mount_own_input(
    app: FastAPI, require_actor, read_fields, store: SensitiveStore, sessions: SessionStore,
    flow: DemoFlow, current_reviewer: Callable[[], Actor | None], config: OperatorConfig,
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

    @app.get("/own-input")
    async def own_input(request: Request):
        requester(request)
        return HTMLResponse(_PAGE.render())

    @app.get("/own-input.js")
    async def script():
        return Response(_SCRIPT, media_type="text/javascript")

    @app.post("/own-input")
    async def canonicalize(request: Request):
        fields = await read_fields(request, limit=100_000, max_fields=5)
        actor = requester(request)
        try:
            draft = OwnInput.from_fields(fields.get("source", ""), fields.get("request", ""))
        except (InvalidSelection, InvalidToken):
            return HTMLResponse(_PAGE.render(error="Érvénytelen szöveg. Ellenőrizd a hosszát és a jelöléseket."), 400)
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
            profiles = await run_in_threadpool(config.enabled_profiles)
        except (OSError, sqlite3.Error):
            return HTMLResponse(_PAGE.render(error="A helyi modellkonfiguráció nem érhető el."), 503)
        requester(request)
        return HTMLResponse(_PAGE.render(
            draft=draft, stamp=stamp(actor, draft), suggestions=suggestions, profiles=profiles,
            suggestion_note=note, external_https=external_https,
        ))

    def checked_draft(actor, fields):
        draft = OwnInput.from_fields(fields.get("source", ""), fields.get("request", ""))
        if not compare_digest(fields.get("stamp", "").encode("utf-8"), stamp(actor, draft).encode("ascii")):
            raise InvalidSelection("Text revision changed")
        return draft

    @app.post("/own-input/remember")
    async def remember(request: Request):
        fields = await read_fields(request, limit=100_000, max_fields=5)
        actor = requester(request)
        try:
            value, kind = checked_draft(actor, fields).selected_value(fields.get("selections", ""))
            store.save(value, kind)
        except (InvalidSelection, InvalidToken):
            return PlainTextResponse("Érvénytelen vagy már mentett érték.", 400)
        except sqlite3.Error:
            return PlainTextResponse("A helyi szótár nem írható.", 503)
        return PlainTextResponse("Érték megjegyezve.")

    async def show_preview(request: Request, *, tokenized: bool):
        fields = await read_fields(request, limit=100_000, max_fields=5)
        actor = requester(request)
        review = request.query_params.get("review") == "1"
        reviewer = current_reviewer() if review else None
        if review and reviewer is None:
            raise HTTPException(409, "A kapupróbához előbb lépj be ellenőrként.")
        profile = None
        if review and fields.get("profile_id"):
            try:
                profile = await run_in_threadpool(config.profile, fields["profile_id"])
            except (ConfigError, OSError, sqlite3.Error):
                raise HTTPException(409, "A kiválasztott hívási profil nem használható.") from None
            requester(request)
        selections = fields.get("selections", "")
        try:
            draft = checked_draft(actor, fields)
            if tokenized:
                source, requested = draft.token_selections(selections)
                prepared = sessions.prepare(
                    actor.session_id, draft.source, draft.request,
                    source_selections=source, request_selections=requested, check_residue=True,
                )
                preview = prepared.source, prepared.request
            else:
                preview = draft.masked(selections)
                if reviewer is not None:
                    source, requested = draft.selections(selections)
                    prepared = sessions.prepare_masked(
                        actor.session_id, draft.source, draft.request,
                        source_selections=source, request_selections=requested,
                    )
        except (InvalidSelection, InvalidToken):
            return HTMLResponse(_PAGE.render(
                error="Az előnézet blokkolva. Ellenőrizd a szövegváltozatot és jelölj meg minden védendő előfordulást."
            ), 400)
        if reviewer is not None:
            case_id = flow.start_own(actor, reviewer, prepared, profile=profile)
            return RedirectResponse(f"/case/{case_id}", status_code=303)
        return HTMLResponse(_PAGE.render(preview=preview, tokenized=tokenized))

    @app.post("/own-input/preview")
    async def own_preview(request: Request):
        return await show_preview(request, tokenized=False)

    @app.post("/own-input/token-preview")
    async def own_token_preview(request: Request):
        return await show_preview(request, tokenized=True)
