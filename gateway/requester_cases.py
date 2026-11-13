# Károly Tamás szakdolgozati prototípusa (2026).
"""Session-bound requester drafts and curated development sample descriptions."""

from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from threading import RLock
import json
import os
import sqlite3
from contextlib import closing

from gateway.demo_input import _DOC_SOURCE, _DOC_REQUEST, _MAIL_SOURCE, _MAIL_REQUEST
from gateway.own_input import OwnInput
from gateway.role_access import AccessDenied, Actor, Role
from gateway.session_lifecycle import SessionStore


RISK_LABELS = {"zöld": "green", "sárga": "yellow", "narancs": "orange", "piros": "red"}


def samples():
    rows = [
        dict(id="A-DOC-01", title="Rövid dokumentumdemó", risk="sárga", categories=["SZEMELY"],
             source=_DOC_SOURCE, request=_DOC_REQUEST, occurrences=[
                 dict(source_section="source", start=start, end=start+len("Teszt Luca"),
                      category="SZEMELY", protected_value_id="R1" if index < 2 else "R2")
                 for index, start in enumerate(
                     [i for i in range(len(_DOC_SOURCE)) if _DOC_SOURCE.startswith("Teszt Luca", i)]
                 )
             ], expected=["Teszt Luca átvette a SYN-17 mintadobozt. Péntekre állapotjelentést ígért."],
             criteria=["A visszaállított helyi válasz tartalmazza a nevet és a következő teendőt."]),
        dict(id="A-MAIL-01", title="Rövid levéldemó", risk="narancs", categories=["SZEMELY", "EMAIL"],
             source=_MAIL_SOURCE, request=_MAIL_REQUEST, occurrences=[
                 dict(source_section=section, start=text.index(value), end=text.index(value)+len(value),
                      category=kind, protected_value_id=f"V{index}")
                 for index, (section,text,value,kind) in enumerate((
                     ("source",_MAIL_SOURCE,"Teszt Mira","SZEMELY"),
                     ("source",_MAIL_SOURCE,"demo42@example.invalid","EMAIL"),
                     ("request",_MAIL_REQUEST,"Teszt Mirának","SZEMELY"),
                 ))
             ], expected=["Csütörtök megfelelő, pontos időpont ígérete nélkül."],
             criteria=["A kezelt előnézet külön ellenőri döntést igényel."]),
    ]
    rows.extend(json.loads(Path(__file__).with_name("demo_samples.json").read_text(encoding="utf-8")))
    return {row["id"]: row for row in rows}


def marked_sections(sample):
    result = {}
    for section in ("source", "request"):
        text = sample[section]
        parts, position = [], 0
        for span in sorted(
            (o for o in sample["occurrences"] if o["source_section"] == section),
            key=lambda o: o["start"],
        ):
            start, end = span["start"], span["end"]
            if start < position or text[start:end] == "":
                raise ValueError("Invalid sample spans")
            parts.append(dict(text=text[position:start], category=""))
            parts.append(dict(text=text[start:end], category=span["category"]))
            position = end
        parts.append(dict(text=text[position:], category=""))
        result[section] = parts
    return result


@dataclass(slots=True, repr=False)
class DraftCase:
    id: str
    owner: Actor
    draft: OwnInput | None
    sample_id: str = ""
    selections: str = "[]"
    case_id: str | None = None
    task: str = "summary"
    editable: bool = False
    model_required: bool = False


class DraftStore:
    def __init__(self, sessions: SessionStore, path: Path):
        self.sessions = sessions
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDONLY, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self._items: dict[str, DraftCase] = {}
        self._lock = RLock()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, owner_identity TEXT NOT NULL,
                    source TEXT, request TEXT, sample_id TEXT NOT NULL,
                    selections TEXT NOT NULL, case_id TEXT, task TEXT NOT NULL,
                    editable INTEGER NOT NULL, model_required INTEGER NOT NULL
                )
            """)
            rows = db.execute("SELECT * FROM drafts ORDER BY rowid").fetchall()
        for (item_id, identity, source, request, sample_id, selections,
             case_id, task, editable, model_required) in rows:
            draft = OwnInput.from_fields(source, request) if source is not None else None
            self._items[item_id] = DraftCase(
                item_id, Actor(identity, "stored-draft", Role.REQUESTER), draft,
                sample_id, selections, case_id, task, bool(editable), bool(model_required),
            )

    def _persist(self, item: DraftCase):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("""
                INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source, request=excluded.request,
                    sample_id=excluded.sample_id, selections=excluded.selections,
                    case_id=excluded.case_id, task=excluded.task,
                    editable=excluded.editable, model_required=excluded.model_required
            """, (
                item.id, item.owner.identity,
                item.draft.source.text if item.draft else None,
                item.draft.request.text if item.draft else None,
                item.sample_id, item.selections, item.case_id, item.task,
                int(item.editable), int(item.model_required),
            ))

    def save(self, actor, draft, *, draft_id="", sample_id="", selections="[]", task=None, editable=False, model_required=False):
        with self._lock:
            if actor.role is not Role.REQUESTER or not self.sessions.is_active(actor.session_id):
                raise AccessDenied("Requester session required")
            if draft_id:
                item = self.get(actor, draft_id)
                if item.case_id:
                    raise AccessDenied("Draft already submitted")
                # Editing a sample makes it own input with its usual review requirement.
                if item.draft != draft:
                    item.sample_id = ""
                item.draft, item.selections = draft, selections
            else:
                if sum(item.owner.identity == actor.identity and not item.case_id for item in self._items.values()) >= 100:
                    raise AccessDenied("Draft limit reached")
                item = DraftCase(
                    token_hex(16), actor, draft, sample_id, selections,
                    task=task or ("mail-draft" if "MAIL" in sample_id else "summary"), editable=editable, model_required=model_required,
                )
                self._items[item.id] = item
            self._persist(item)
            return item

    def get(self, actor, draft_id):
        with self._lock:
            item = self._items.get(draft_id)
            if item is None or actor.role is not Role.REQUESTER or item.owner.identity != actor.identity:
                raise AccessDenied("Draft unavailable")
            return item

    def pending(self, actor):
        with self._lock:
            return [item for item in reversed(list(self._items.values())) if item.owner.identity == actor.identity and not item.case_id]

    def submit(self, actor, draft_id, operation):
        with self._lock:
            item = self.get(actor, draft_id)
            if item.case_id:
                return item.case_id
            item.case_id = operation()
            # Keep only the case link so a retried submit cannot create another case.
            item.draft = None
            item.selections = "[]"
            self._persist(item)
            return item.case_id
