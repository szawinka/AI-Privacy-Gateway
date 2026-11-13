# Károly Tamás szakdolgozati prototípusa (2026).
"""Local connection and generation profiles for the operator workspace."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from ipaddress import ip_address
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4


PROTOCOLS = ("openai-responses", "openai-chat-compatible", "anthropic-messages")
TASKS = ("summary", "mail-draft")


class ConfigError(ValueError):
    """Invalid or unavailable operator configuration."""


def _required(fields, key, limit=128):
    value = fields.get(key, "").strip()
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ConfigError(f"Érvénytelen mező: {key}.")
    return value


def _endpoint(fields):
    value = _required(fields, "endpoint", 512)
    try:
        url = urlsplit(value)
        port = url.port
        host = (url.hostname or "").rstrip(".").encode("idna").decode("ascii")
        if (
            url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment or any(c.isspace() for c in value)
            or "\\" in value or port == 0 or "." not in host
            or host.endswith((".localhost", ".local"))
            or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                   for label in host.split("."))
        ):
            raise ValueError
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
    except ValueError:
        raise ConfigError("Érvényes HTTPS alapcímet adj meg, hitelesítési adatok és IP-cím nélkül.") from None
    return value


def _optional_number(fields, key, ceiling):
    value = fields.get(key, "").strip()
    if not value:
        return None
    number = float(value)
    if not 0 <= number <= ceiling:
        raise ConfigError(f"Érvénytelen mező: {key}.")
    return number


@dataclass(frozen=True, slots=True)
class ModelProfile:
    profile_id: str
    profile_revision: str
    connection_id: str
    connection_revision: str
    name: str
    task: str
    protocol: str
    endpoint: str
    model: str
    auth: str
    project: str
    max_tokens: int
    timeout_seconds: int
    temperature: float | None
    top_p: float | None
    output_format: str


class OperatorConfig:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def _db(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        secrets_dir = self.root / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        secret_path = secrets_dir / "credentials.sqlite3"
        fd = os.open(secret_path, os.O_CREAT | os.O_RDONLY, 0o600)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with closing(sqlite3.connect(self.root / "profiles.sqlite3")) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("ATTACH DATABASE ? AS secret", (str(secret_path),))
            db.execute("PRAGMA secret.secure_delete = ON")
            db.execute("""CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, revision TEXT NOT NULL, name TEXT NOT NULL,
                protocol TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL,
                auth TEXT NOT NULL, project TEXT NOT NULL, enabled INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY, revision TEXT NOT NULL, name TEXT NOT NULL,
                task TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES connections(id),
                max_tokens INTEGER NOT NULL, timeout_seconds INTEGER NOT NULL,
                temperature REAL, top_p REAL, output_format TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS secret.credentials (
                connection_id TEXT PRIMARY KEY, api_key TEXT NOT NULL
            )""")
            yield db

    def list_connections(self):
        with self._db() as db:
            return db.execute("""SELECT c.*, EXISTS (
                SELECT 1 FROM secret.credentials s WHERE s.connection_id = c.id
            ) AS has_key FROM connections c ORDER BY name, id""").fetchall()

    def list_profiles(self):
        with self._db() as db:
            return db.execute("SELECT * FROM profiles ORDER BY name, id").fetchall()

    @staticmethod
    def _enabled_profiles(db, profile_id=None):
        query = """SELECT p.id AS profile_id, p.revision AS profile_revision,
            c.id AS connection_id, c.revision AS connection_revision,
            p.name, p.task, c.protocol, c.endpoint, c.model, c.auth, c.project,
            p.max_tokens, p.timeout_seconds, p.temperature, p.top_p, p.output_format
            FROM profiles p JOIN connections c ON c.id = p.connection_id
            WHERE c.enabled = 1 AND (c.auth = 'none' OR EXISTS (
                SELECT 1 FROM secret.credentials s WHERE s.connection_id = c.id
            ))"""
        if profile_id is not None:
            return db.execute(query + " AND p.id = ?", (profile_id,)).fetchall()
        return db.execute(query + " ORDER BY p.name, p.id").fetchall()

    def enabled_profiles(self):
        if not (self.root / "profiles.sqlite3").is_file():
            return []
        with self._db() as db:
            return [ModelProfile(**row) for row in self._enabled_profiles(db)]

    def profile(self, profile_id: str) -> ModelProfile:
        if not (self.root / "profiles.sqlite3").is_file():
            raise ConfigError("A kiválasztott hívási profil nem használható.")
        with self._db() as db:
            rows = self._enabled_profiles(db, profile_id)
            if not rows:
                raise ConfigError("A kiválasztott hívási profil nem használható.")
            return ModelProfile(**rows[0])

    @contextmanager
    def current_profile(self, expected: ModelProfile):
        """Keep revisions stable and make credentials available only on explicit lookup."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = self._enabled_profiles(db, expected.profile_id)
            if not rows or ModelProfile(**rows[0]) != expected:
                raise ConfigError("A modellbeállítás megváltozott. Új ellenőrzés szükséges.")
            def read_key():
                if expected.auth == "none":
                    return None
                record = db.execute(
                    "SELECT api_key FROM secret.credentials WHERE connection_id=?", (expected.connection_id,)
                ).fetchone()
                if not record or not record["api_key"]:
                    raise ConfigError("A szolgáltatói hitelesítés nem érhető el.")
                return record["api_key"]

            yield read_key

    def save_connection(self, fields, item_id=None):
        name = _required(fields, "name")
        protocol = fields.get("protocol")
        auth = fields.get("auth")
        if protocol not in PROTOCOLS or auth not in ("api-key", "none"):
            raise ConfigError("Ismeretlen protokoll vagy hitelesítési mód.")
        endpoint = _endpoint(fields)
        model = _required(fields, "model")
        project = fields.get("project", "").strip()
        key = fields.get("api_key", "").strip()
        if len(project) > 128 or len(key) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ConfigError("Érvénytelen projektazonosító vagy API-kulcs.")
        remove_key = fields.get("remove_key") == "yes" or auth == "none"
        if key and remove_key:
            raise ConfigError("A kulcs cseréje és törlése külön művelet.")
        enabled = fields.get("enabled") == "yes"
        connection_id = item_id or uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT endpoint FROM connections WHERE id=?", (connection_id,)).fetchone()
            if item_id and previous is None:
                raise ConfigError("A modellkapcsolat már nem érhető el.")
            present = db.execute(
                "SELECT 1 FROM secret.credentials WHERE connection_id=?", (connection_id,)
            ).fetchone()
            if previous and previous["endpoint"] != endpoint and present and not (key or remove_key):
                raise ConfigError("Címváltozásnál adj meg új kulcsot, vagy töröld a régi kulcsot.")
            if enabled and auth == "api-key" and (remove_key or not (present or key)):
                raise ConfigError("Engedélyezett kapcsolathoz API-kulcs szükséges.")
            db.execute("""INSERT INTO connections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, name=excluded.name,
                protocol=excluded.protocol, endpoint=excluded.endpoint, model=excluded.model,
                auth=excluded.auth, project=excluded.project, enabled=excluded.enabled""",
                (connection_id, uuid4().hex, name, protocol, endpoint, model, auth, project, enabled))
            if remove_key:
                db.execute("DELETE FROM secret.credentials WHERE connection_id=?", (connection_id,))
            elif key:
                db.execute("INSERT OR REPLACE INTO secret.credentials VALUES (?, ?)", (connection_id, key))
        return connection_id

    def save_profile(self, fields, item_id=None):
        name = _required(fields, "name")
        task = fields.get("task")
        connection_id = fields.get("connection_id")
        output = fields.get("output_format", "text")
        try:
            tokens = int(fields.get("max_tokens", ""))
            timeout = int(fields.get("timeout_seconds", ""))
            temperature = _optional_number(fields, "temperature", 2)
            top_p = _optional_number(fields, "top_p", 1)
        except ValueError:
            raise ConfigError("Érvénytelen hívási paraméter.") from None
        if (
            task not in TASKS or output != "text" or not 1 <= tokens <= 8192
            or not 1 <= timeout <= 120 or (temperature is not None and top_p is not None)
        ):
            raise ConfigError("A hívási profil értékei kívül esnek az engedélyezett tartományon.")
        profile_id = item_id or uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM connections WHERE id=?", (connection_id,)).fetchone():
                raise ConfigError("Válassz létező modellkapcsolatot.")
            if item_id and not db.execute("SELECT 1 FROM profiles WHERE id=?", (item_id,)).fetchone():
                raise ConfigError("A hívási profil már nem érhető el.")
            db.execute("""INSERT INTO profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, name=excluded.name,
                task=excluded.task, connection_id=excluded.connection_id, max_tokens=excluded.max_tokens,
                timeout_seconds=excluded.timeout_seconds, temperature=excluded.temperature,
                top_p=excluded.top_p, output_format=excluded.output_format""",
                (profile_id, uuid4().hex, name, task, connection_id, tokens, timeout, temperature, top_p, output))
        return profile_id

    def delete_connection(self, item_id):
        with self._db() as db:
            try:
                db.execute("DELETE FROM connections WHERE id=?", (item_id,))
            except sqlite3.IntegrityError:
                raise ConfigError("Előbb töröld vagy rendeld át a kapcsolat hívási profiljait.") from None
            db.execute("DELETE FROM secret.credentials WHERE connection_id=?", (item_id,))

    def delete_profile(self, item_id):
        with self._db() as db:
            db.execute("DELETE FROM profiles WHERE id=?", (item_id,))
