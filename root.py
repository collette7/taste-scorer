"""Your rating ROOTS — the places you've already been and rated.

These root your taste model: the evidence the judge compares candidates against. NOT the
places being scored (those are "candidates", passed at score time).

Every root backend yields normalized place records:

    {
      "name": str,
      "rating": int | None,     # 1-7
      "visited": bool,
      "type": str,              # "Cities" | "Countries" | "States" | "Venue"
      "tags": list[str],
      "loc": list[str],         # location names, most-specific first
      "context": str,           # free text (address, description)
    }

Built-in root backends:
  - ObsidianRoot: walks a vault, reads YAML frontmatter (category: [[Places]])
  - CSVRoot: any CSV with a name + rating column (auto-detects common headers,
    parses Google-Maps-export style "Tags: .. / Location: .." descriptions)
  - JSONRoot: a JSON array of records (already normalized, or {name, rating})

Configure via taste.config.json next to this file (optional):

    {
      "roots": [
        {"kind": "obsidian", "vault": "~/Documents/Obsidian Vault", "refs": "07 References"},
        {"kind": "csv", "path": "~/places.csv"},
        {"kind": "json", "path": "~/ratings.json"}
      ],
      "scale_max": 7,
      "gate": {"min_score": 6}
    }

("history" and "sources" are accepted as legacy aliases for "roots".)

If no config exists, falls back to a single Obsidian root at
$TASTE_VAULT_PATH (default ~/Documents/Obsidian Vault).
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("TASTE_CONFIG", HERE / "taste.config.json"))

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
WIKILINK_RE = re.compile(r"^\[\[|\]\]$")


def _coerce_rating(raw, scale_max: int = 7) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        v = round(float(str(raw).strip()))
        return v if 1 <= v <= scale_max else None
    except (TypeError, ValueError):
        return None


def _norm_links(raw) -> list[str]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        if not isinstance(item, str):
            continue
        s = item.strip().strip("'\"")
        s = WIKILINK_RE.sub("", s)
        s = s.split("|", 1)[0]
        if s:
            out.append(s)
    return out


# ---- Obsidian ------------------------------------------------------------------


class ObsidianRoot:
    kind = "obsidian"

    def __init__(self, vault: str | None = None, refs: str | None = None,
                 category: str = "Places", type_field_values: list[str] | None = None,
                 signal_fields: list[str] | None = None, **_):
        self.vault = Path(os.path.expanduser(
            vault or os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")
        ))
        self.refs = self.vault / (refs or "07 References")
        self.category = category
        self.type_field_values = type_field_values if type_field_values is not None else ["Cities", "Countries", "States"]
        self.signal_fields = signal_fields or ["tags", "loc"]

    def records(self) -> Iterator[dict]:
        import yaml

        root = self.refs if self.refs.exists() else self.vault
        for md in root.rglob("*.md"):
            if "Template" in md.name:
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = FRONTMATTER_RE.match(text)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            cats = _norm_links(fm.get("category"))
            if self.category not in cats:
                continue

            types = _norm_links(fm.get("type"))
            ntype = next((t for t in self.type_field_values if t in types), "Venue")
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            addr = fm.get("address") or ""
            has_rating = _coerce_rating(fm.get("rating")) is not None
            notes = fm.get("review") or (fm.get("notes") if has_rating else "") or ""
            if not isinstance(addr, str):
                addr = ""
            if not isinstance(notes, str):
                notes = " ".join(str(n) for n in notes) if isinstance(notes, list) else ""
            context = f"REVIEW: {notes} | {addr}" if notes else addr

            extra_signals = {}
            for field in self.signal_fields:
                if field in ("tags",):
                    continue
                extra_signals[field] = _norm_links(fm.get(field))

            rec = {
                "name": md.stem,
                "rating": _coerce_rating(fm.get("rating")),
                "visited": fm.get("visited") is True or str(fm.get("visited")).lower() == "true"
                           or (isinstance(tags, list) and "watched" in tags),
                "type": ntype,
                "tags": [t for t in tags if isinstance(t, str)],
                "loc": extra_signals.get("loc", _norm_links(fm.get("loc"))),
                "context": context,
            }
            for field, values in extra_signals.items():
                if field != "loc":
                    rec[field] = values
            if "year" in fm and fm.get("year"):
                rec["year"] = fm["year"]
            yield rec


# ---- CSV -----------------------------------------------------------------------

# Header aliases, case-insensitive
CSV_ALIASES = {
    "name": {"name", "title", "place", "venue"},
    "rating": {"rating", "score", "stars", "my rating"},
    "type": {"type", "category", "kind"},
    "tags": {"tags", "labels"},
    "loc": {"location", "loc", "city", "area"},
    "context": {"review", "description", "notes", "address", "comment"},
    "visited": {"visited", "been"},
}

DESC_TAGS_RE = re.compile(r"Tags:\s*([^\n]+)", re.IGNORECASE)
DESC_LOC_RE = re.compile(r"Location:\s*([^\n]+)", re.IGNORECASE)
DESC_TYPE_RE = re.compile(r"Type:\s*([^\n]+)", re.IGNORECASE)


class CSVRoot:
    kind = "csv"

    def __init__(self, path: str, mapping: dict | None = None, scale_max: int = 7, **_):
        self.path = Path(os.path.expanduser(path))
        self.mapping = mapping or {}
        self.scale_max = scale_max

    def _resolve_columns(self, headers: list[str]) -> dict:
        resolved = dict(self.mapping)  # explicit mapping wins
        lower = {h.lower().strip(): h for h in headers}
        for field, aliases in CSV_ALIASES.items():
            if field in resolved:
                continue
            for a in aliases:
                if a in lower:
                    resolved[field] = lower[a]
                    break
        return resolved

    def records(self) -> Iterator[dict]:
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = self._resolve_columns(reader.fieldnames or [])
            for row in reader:
                name = (row.get(cols.get("name", "")) or "").strip()
                if not name:
                    continue
                context = (row.get(cols.get("context", "")) or "").strip()

                # Tags/loc/type: dedicated columns first, then parse from description
                tags_raw = (row.get(cols.get("tags", "")) or "").strip()
                loc_raw = (row.get(cols.get("loc", "")) or "").strip()
                type_raw = (row.get(cols.get("type", "")) or "").strip()
                if not tags_raw and context:
                    m = DESC_TAGS_RE.search(context)
                    tags_raw = m.group(1).strip() if m else ""
                if not loc_raw and context:
                    m = DESC_LOC_RE.search(context)
                    loc_raw = m.group(1).strip() if m else ""
                if not type_raw and context:
                    m = DESC_TYPE_RE.search(context)
                    type_raw = m.group(1).strip() if m else ""

                visited_raw = (row.get(cols.get("visited", "")) or "").strip().lower()
                rating = _coerce_rating(row.get(cols.get("rating", "")), self.scale_max)
                yield {
                    "name": name,
                    "rating": rating,
                    # CSV rows with a rating are implicitly visited
                    "visited": visited_raw in {"true", "yes", "1", "x"} or rating is not None,
                    "type": type_raw or "Venue",
                    "tags": [t.strip() for t in tags_raw.split(",") if t.strip()],
                    "loc": [l.strip() for l in loc_raw.split(",") if l.strip()],
                    "context": context[:300],
                }


# ---- JSON ----------------------------------------------------------------------


class JSONRoot:
    kind = "json"

    def __init__(self, path: str, scale_max: int = 7, **_):
        self.path = Path(os.path.expanduser(path))
        self.scale_max = scale_max

    def records(self) -> Iterator[dict]:
        data = json.loads(self.path.read_text())
        if not isinstance(data, list):
            raise ValueError(f"{self.path}: expected a JSON array of records")
        for r in data:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            rating = _coerce_rating(r.get("rating"), self.scale_max)
            notes = str(r.get("review", "") or r.get("notes", "")).strip()
            context = str(r.get("context", "")).strip()
            if notes:
                context = f"REVIEW: {notes}" + (f" | {context}" if context else "")
            yield {
                "name": str(r["name"]),
                "rating": rating,
                "visited": bool(r.get("visited", rating is not None)),
                "type": r.get("type", "Venue"),
                "tags": list(r.get("tags", [])),
                "loc": list(r.get("loc", [])),
                "context": context[:300],
            }


# ---- Registry / config ----------------------------------------------------------

ROOT_KINDS = {"obsidian": ObsidianRoot, "csv": CSVRoot, "json": JSONRoot}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"roots": [{"kind": "obsidian"}], "scale_max": 7, "gate": {"min_score": 6}}


def _root_specs(config: dict) -> list[dict]:
    """Read "roots" from config; accept legacy "history"/"sources" aliases."""
    return config.get("roots") or config.get("history") or config.get("sources") or [{"kind": "obsidian"}]


def build_roots(config: dict | None = None, overrides: list[dict] | None = None) -> list:
    """Instantiate root backends from config, or from explicit override specs."""
    specs = overrides if overrides else _root_specs(config or load_config())
    backends = []
    for spec in specs:
        kind = spec.get("kind")
        cls = ROOT_KINDS.get(kind)
        if cls is None:
            raise ValueError(f"unknown root kind {kind!r} (have: {sorted(ROOT_KINDS)})")
        backends.append(cls(**{k: v for k, v in spec.items() if k != "kind"}))
    return backends


def all_records(backends: list) -> list[dict]:
    """Collect records from all root backends. Later backends win on name collisions."""
    by_name: dict[str, dict] = {}
    for backend in backends:
        for rec in backend.records():
            by_name[rec["name"]] = rec
    return list(by_name.values())
