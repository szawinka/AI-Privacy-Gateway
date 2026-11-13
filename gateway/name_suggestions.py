# Károly Tamás szakdolgozati prototípusa (2026).
"""Run the optional local name worker in its separate environment."""

import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone
import os

from gateway.install_nlp import NLP_HOME, active, interpreter
from gateway.own_input import OwnInput


def suggest_names(draft: OwnInput) -> list[dict] | None:
    """Return None when uninstalled, or raise if the installed worker fails."""
    if not active():
        return None
    python = interpreter(NLP_HOME / "venv")
    worker = Path(__file__).with_name("name_worker.py").resolve()
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", str(worker)],
            input=json.dumps([draft.source.text, draft.request.text]),
            text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, check=True,
        )
        proposals = json.loads(result.stdout)
        sections = {"source": draft.source.text, "request": draft.request.text}
        if not isinstance(proposals, list) or any(
            entry["kind"] != "PERSON"
            or type(entry["start"]) is not int
            or type(entry["end"]) is not int
            or not 0 <= entry["start"] < entry["end"] <= len(sections[entry["section"]])
            for entry in proposals
        ):
            raise ValueError("Invalid suggestions")
        stamp = NLP_HOME / "last_loaded.json"
        fd = os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"loaded_at": datetime.now(timezone.utc).isoformat(),
                       "method": "Helyi spaCy-folyamat, névjavaslatkéréskor."}, output)
        return proposals
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise RuntimeError("Local name recognition unavailable") from None
