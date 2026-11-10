"""Run the optional local name worker in its separate environment."""

import json
from pathlib import Path
import subprocess

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
        return proposals
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise RuntimeError("Local name recognition unavailable") from None
