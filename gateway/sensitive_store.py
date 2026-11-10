"""Local, requester-managed dictionary of explicitly remembered values."""

from contextlib import closing
from pathlib import Path
import sqlite3
from unicodedata import normalize

from gateway.text_processing import InvalidSelection, MaskType
from gateway.token_mapping import reject_reserved_markers


class SensitiveStore:
    def __init__(self, path: Path):
        self._path = path

    def _connect(self):
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return closing(sqlite3.connect(self._path))

    @staticmethod
    def _schema(db):
        db.execute("""
            CREATE TABLE IF NOT EXISTS sensitive_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL
            )
        """)

    def entries(self, query: str = "") -> list[tuple[int, str, str]]:
        if not self._path.exists():
            return []
        query = normalize("NFC", query).casefold()
        with self._connect() as db, db:
            self._schema(db)
            rows = db.execute("SELECT id, value, kind FROM sensitive_values ORDER BY id").fetchall()
        return [row for row in rows if query in row[1].casefold() or query in row[2].casefold()]

    def save(self, value: str, kind: str, item_id: int | None = None) -> None:
        value = normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
        if not value.strip() or len(value) > 4000 or "\x00" in value or kind not in MaskType.__members__:
            raise InvalidSelection("Invalid saved value")
        reject_reserved_markers(value)
        with self._connect() as db, db:
            self._schema(db)
            try:
                if item_id is None:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute("SELECT COUNT(*) FROM sensitive_values").fetchone()[0] >= 500:
                        raise InvalidSelection("Dictionary limit reached")
                    db.execute("INSERT INTO sensitive_values (value, kind) VALUES (?, ?)", (value, kind))
                elif not db.execute(
                    "UPDATE sensitive_values SET value = ?, kind = ? WHERE id = ?", (value, kind, item_id)
                ).rowcount:
                    raise InvalidSelection("Saved value unavailable")
            except sqlite3.IntegrityError:
                raise InvalidSelection("Value already saved") from None

    def delete(self, item_id: int) -> None:
        with self._connect() as db, db:
            self._schema(db)
            db.execute("DELETE FROM sensitive_values WHERE id = ?", (item_id,))

    def suggestions(self, draft, names: list[dict]) -> list[dict]:
        proposals = []
        entries = sorted(self.entries(), key=lambda row: -len(row[1]))
        for text in (draft.source, draft.request):
            section = text.section.value
            candidates = []
            for _, value, kind in entries:
                start = text.text.find(value)
                while start != -1:
                    end = start + len(value)
                    candidates.append({"section": section, "kind": kind, "start": start, "end": end})
                    start = text.text.find(value, end)
            candidates.extend(item for item in names if item["section"] == section)
            occupied = bytearray(len(text.text))
            for item in candidates:
                start, end = item["start"], item["end"]
                if not any(occupied[start:end]):
                    occupied[start:end] = b"\x01" * (end - start)
                    proposals.append(item)
                    if len(proposals) > 40:
                        return proposals
        return proposals
