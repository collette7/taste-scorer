"""Profile staleness detection + auto-refresh.

The profile is a cached snapshot of the roots. This module answers "is the
snapshot older than the newest rated note?" and refreshes when needed.

Modes:
  ensure_fresh(domain, auto=True)   -> refresh in-process if stale (intake path)
  ensure_fresh(domain, auto=False)  -> print a warning if stale (score path)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
REFS = VAULT / os.environ.get("TASTE_REFS_DIR", "07 References")


def profile_path(domain: str) -> Path:
    return HERE / ("taste_profile.json" if domain == "places" else f"taste_profile.{domain}.json")


def newest_root_mtime() -> float:
    newest = 0.0
    root = REFS if REFS.exists() else VAULT
    if not root.exists():
        return 0.0
    for md in root.rglob("*.md"):
        try:
            mt = md.stat().st_mtime
        except OSError:
            continue
        if mt > newest:
            newest = mt
    return newest


def is_stale(domain: str) -> bool:
    p = profile_path(domain)
    if not p.exists():
        return True
    return newest_root_mtime() > p.stat().st_mtime


def refresh(domain: str) -> bool:
    result = subprocess.run(
        [sys.executable, str(HERE / "build_profile.py"), "--domain", domain],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  (refresh failed: {result.stderr.strip()[:200]})", file=sys.stderr)
        return False
    summary = result.stdout.strip().splitlines()
    if summary:
        print(f"  (profile refreshed: {summary[-2].strip() if len(summary) > 1 else summary[-1].strip()})", file=sys.stderr)
    return True


def ensure_fresh(domain: str = "places", auto: bool = True) -> None:
    if os.environ.get("TASTE_NO_AUTOREFRESH"):
        return
    try:
        if not is_stale(domain):
            return
        if auto:
            refresh(domain)
        else:
            print(
                f"  ⚠ taste profile ({domain}) is stale — newer ratings exist in the vault. "
                f"Run `taste refresh{' --domain ' + domain if domain != 'places' else ''}` "
                f"or score via intake for auto-refresh.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"  (freshness check skipped: {e})", file=sys.stderr)
