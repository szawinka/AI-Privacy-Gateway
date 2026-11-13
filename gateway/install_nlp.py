# Károly Tamás szakdolgozati prototípusa (2026).
"""Optional, consent-based local installation of the Hungarian recognizer."""

import argparse
from contextlib import contextmanager
import json
import os
from shutil import rmtree
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile, mkdtemp
import venv
from datetime import datetime, timezone


SPACY_VERSION = "3.8.18"
MODEL_VERSION = "3.8.1"
MODEL_WHEEL = (
    "https://huggingface.co/huspacy/hu_core_news_md/resolve/"
    "f6cfd29ca9db9d809d095954ff209b0099343a7f/hu_core_news_md-3.8.1-py3-none-any.whl"
    "#sha256=66dbc77d94c2a5ea348ab459a258ffdf94be7f47223882e6b83e35dd563371a6"
)
PROJECT = Path(__file__).resolve().parents[1]
NLP_HOME = PROJECT / ".local" / "nlp"
CONFIG = NLP_HOME / "installed.json"
VERSIONS = {"spacy": SPACY_VERSION, "hu_core_news_md": MODEL_VERSION}


def interpreter(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    try:
        data = json.loads(CONFIG.read_text())
        return interpreter(NLP_HOME / "venv").is_file() and all(data.get(key) == value for key, value in VERSIONS.items())
    except (OSError, ValueError):
        return False


def installation_details() -> dict | None:
    if not installed():
        return None
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    recorded = data.get("installed_at")
    if not recorded:
        recorded = datetime.fromtimestamp(CONFIG.stat().st_mtime, timezone.utc).isoformat()
        source = "A korábbi telepítés ideje a konfigurációs fájl időbélyegéből becsült."
    else:
        source = "A telepítés ideje helyben rögzített."
    try:
        last_load = json.loads((NLP_HOME / "last_loaded.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last_load = None
    return {
        "name": "hu_core_news_md", "model_version": data["hu_core_news_md"],
        "spacy_version": data["spacy"], "installed_at": recorded, "time_source": source,
        "load_method": "A modell a projekt külön helyi Python-környezetében, névjavaslatkérésenként külön folyamatban töltődik be.",
        "last_loaded": last_load.get("loaded_at") if isinstance(last_load, dict) else None,
    }


def active() -> bool:
    return installed() and not (NLP_HOME / "disabled").exists()


@contextmanager
def _maintenance():
    NLP_HOME.mkdir(parents=True, exist_ok=True)
    lock = NLP_HOME / "install.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError("Az NLP telepítése már folyamatban van, vagy korábban megszakadt.") from None
    try:
        yield
    finally:
        lock.rmdir()


def set_active(enabled: bool) -> None:
    with _maintenance():
        if not installed():
            raise RuntimeError("A modell nincs telepítve.")
        marker = NLP_HOME / "disabled"
        if enabled:
            marker.unlink(missing_ok=True)
        else:
            marker.touch()


def _remove_files() -> None:
    CONFIG.unlink(missing_ok=True)
    (NLP_HOME / "last_loaded.json").unlink(missing_ok=True)
    environment = NLP_HOME / "venv"
    if environment.exists():
        rmtree(environment)
    (NLP_HOME / "disabled").unlink(missing_ok=True)


def remove() -> None:
    with _maintenance():
        _remove_files()


@contextmanager
def _preserve_installation():
    backup = Path(mkdtemp(prefix="reinstall-", dir=NLP_HOME))
    paths = (CONFIG, NLP_HOME / "venv", NLP_HOME / "last_loaded.json")
    moved = []
    try:
        for path in paths:
            if path.exists():
                path.rename(backup / path.name)
                moved.append(path)
    except OSError:
        for path in reversed(moved):
            (backup / path.name).rename(path)
        backup.rmdir()
        raise
    try:
        yield
    except BaseException:
        CONFIG.unlink(missing_ok=True)
        environment = NLP_HOME / "venv"
        if environment.exists():
            rmtree(environment)
        for path in reversed(moved):
            (backup / path.name).rename(path)
        backup.rmdir()
        raise
    else:
        rmtree(backup)


def install(*, replace: bool = False) -> None:
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError("A telepítőhöz CPython 3.14 szükséges.")
    with _maintenance():
        if not replace and installed():
            print("A rögzített helyi modell már telepítve van.")
            return
        if replace:
            with _preserve_installation():
                _install_files()
        else:
            _install_files()


def _install_files() -> None:
    (NLP_HOME / "last_loaded.json").unlink(missing_ok=True)
    environment = NLP_HOME / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = interpreter(environment)
    subprocess.run([
        str(python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
        "--disable-pip-version-check", "--only-binary=:all:", "--index-url",
        "https://pypi.org/simple", f"spacy=={SPACY_VERSION}", MODEL_WHEEL,
    ], check=True)
    subprocess.run([str(python), "-I", "-m", "pip", "--disable-pip-version-check", "check"], check=True)
    subprocess.run([str(python), "-I", "-c", """
from importlib.metadata import version
import spacy
assert version("spacy") == "3.8.18"
assert version("hu-core-news-md") == "3.8.1"
nlp = spacy.load("hu_core_news_md")
assert "ner" in nlp.pipe_names and "PER" in nlp.get_pipe("ner").labels
print("A modell betöltődött, a PER kategória elérhető.")
"""], check=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=NLP_HOME, delete=False) as output:
        json.dump({**VERSIONS, "installed_at": datetime.now(timezone.utc).isoformat(),
                   "installation_method": "Jóváhagyott helyi telepítés rögzített csomagverziókkal."}, output)
    Path(output.name).replace(CONFIG)
    print("A helyi magyar felismerő telepítése és konfigurálása kész.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    consent = parser.add_mutually_exclusive_group()
    consent.add_argument("--yes", action="store_true", help="A letöltés és telepítés jóváhagyása")
    consent.add_argument("--skip", action="store_true", help="Az opcionális telepítés kihagyása")
    args = parser.parse_args()
    if args.skip:
        print("Figyelem: a magyar névfelismerő telepítését kihagytad. A kézi kijelölés elérhető.")
        return 0
    if installed():
        print("A rögzített helyi modell már telepítve van.")
        return 0
    if not args.yes:
        try:
            answer = input(
                "Letöltöd a magyar névfelismerőt (kb. 127 MB és függőségek) a projektbe? [i/N] "
            )
        except EOFError:
            answer = ""
        if answer.strip().casefold() not in {"i", "igen"}:
            print("Figyelem: az automatikus névfelismerő nincs telepítve. A kézi kijelölés elérhető.")
            return 0
    try:
        install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"A modell telepítése sikertelen: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
