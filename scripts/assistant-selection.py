#!/usr/bin/env python3
"""CLI for assistant selection: catalog, active, resolve, selected."""

import json
import sys
from pathlib import Path
from uuid import UUID

from aegisops.assistant.models import CATALOG, LEGACY, Selection, selected
from aegisops.assistant.repository import AssistantRepository
from aegisops.projects.models import ProjectError

ROOT = Path(__file__).absolute().parents[1]


def _repository() -> AssistantRepository:
    config = ROOT.parent.parent / "work" / "aegisops-runtime-credentials" / "assistant.json"
    attempts = ROOT / ".local" / "assistant-attempts"
    return AssistantRepository(config, attempts)


def _pair(selection: Selection) -> dict:
    return {"provider": selection.provider, "model": selection.model}


def _catalog() -> dict:
    return {
        "providers": [
            {"provider": provider, "label": entry.label, "model": entry.model}
            for provider, entry in CATALOG.items()
        ]
    }


def _active() -> dict:
    selection = _repository().active()
    if selection is None:
        return _pair(LEGACY)
    return _pair(selection)


def _resolve(mode: str, identifier: str) -> dict:
    selection = _repository().resolve(mode, UUID(identifier))
    return _pair(selection)


def _selected(provider: str) -> dict:
    return _pair(selected(provider))


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[1] == "catalog":
            payload = _catalog()
        elif len(argv) == 2 and argv[1] == "active":
            payload = _active()
        elif len(argv) == 4 and argv[1] == "resolve":
            payload = _resolve(argv[2], argv[3])
        elif len(argv) == 3 and argv[1] == "selected":
            payload = _selected(argv[2])
        else:
            raise ProjectError()
    except Exception:
        sys.stdout.write(
            json.dumps({"error": "Assistant settings unavailable"}, ensure_ascii=False)
        )
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
