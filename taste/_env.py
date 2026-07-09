"""Tiny .env loader, zero dependencies. Reads KEY=VALUE lines from a .env
file next to the scripts and sets os.environ (never overriding a variable
that's already set, so real shell exports always win). Imported by root.py
so every script picks it up automatically."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    path = path or (Path(__file__).parent.parent / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()
