#!/usr/bin/env python3
"""Batch pipeline: big CSV of places -> dedupe -> batch-score -> ranked note -> selective intake.

Designed for research dumps (e.g. thousands of Instagram-sourced places), where
creating a vault note per row would bury the vault. Instead:

  1. Load + filter the CSV (city / category / mentions / awarded slices)
  2. Dedupe against existing vault notes (07 References/*.md by name)
  3. Batch-score 10 per LLM call, with CSV context (city, category, notes) as
     judge evidence — no per-row Google Places lookups needed
  4. Write ONE ranked note: "Taste Ranked - <label>.md" (go/maybe/skip table)
  5. Optionally intake only the go's (or score >= N) as real Place notes

Usage:
  batch_intake.py places.csv --city Kyoto                       # score one city
  batch_intake.py places.csv --city Kyoto --min-mentions 2
  batch_intake.py places.csv --category Caffeine --city Seoul
  batch_intake.py places.csv --city Kyoto --limit 30            # cap for a cheap pilot
  batch_intake.py places.csv --city Kyoto --intake go           # also create notes for go's
  batch_intake.py places.csv --city Kyoto --intake 7            # only 7s
  batch_intake.py places.csv --city Kyoto --prep                # dump candidates, no scoring
  batch_intake.py places.csv --city Kyoto --prompt > p.json     # BYO-model batches out
  batch_intake.py places.csv --city Kyoto --parse < raw.json    # BYO-model verdicts in

Column auto-detection handles both intake-style CSVs (Name/Url/Notes) and
research-style CSVs (Place/City/Category/Notes/Google Maps/Mentions/Awarded).
"""
from __future__ import annotations

from taste import _env  # noqa: F401 -- loads .env into os.environ

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from taste.rubric import build_batch_prompt, load_profile, parse_batch

from taste.paths import PROJECT_ROOT as HERE
VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
REFS = VAULT / os.environ.get("TASTE_REFS_DIR", "07 References")
NOTES_DIR = VAULT / os.environ.get("TASTE_NOTES_DIR", "02 Notes")
OUTPUT_DIR = Path(os.path.expanduser(os.environ.get("TASTE_OUTPUT_DIR", str(HERE / "taste_notes"))))
MODEL = os.environ.get("TASTE_MODEL", "claude-sonnet-4-5")
BATCH_SIZE = int(os.environ.get("TASTE_BATCH_SIZE", "10"))

COL_ALIASES = {
    "name": ["place", "name", "title", "venue"],
    "city": ["city"],
    "category": ["category", "type", "kind"],
    "area": ["area / address", "area", "address", "neighborhood"],
    "notes": ["review", "notes", "description", "comment"],
    "mentions": ["mentions"],
    "awarded": ["awarded"],
    "hours": ["hours"],
    "url": ["google maps", "url", "maps", "link"],
    "source": ["instagram post", "source", "post"],
}


def resolve_columns(headers: list[str]) -> dict:
    lower = {h.lower().strip(): h for h in headers}
    resolved = {}
    for field, aliases in COL_ALIASES.items():
        for a in aliases:
            if a in lower:
                resolved[field] = lower[a]
                break
    return resolved


def load_rows(path: Path, args) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = resolve_columns(reader.fieldnames or [])
        if "name" not in cols:
            raise SystemExit(f"no name/place column found in {path.name} (headers: {reader.fieldnames})")
        out = []
        for row in reader:
            get = lambda field: (row.get(cols.get(field, "")) or "").strip()
            name = get("name")
            if not name:
                continue
            if name.startswith("(") or "no single place" in get("notes").lower():
                continue
            rec = {
                "name": name,
                "city": get("city"),
                "category": get("category"),
                "area": get("area"),
                "notes": get("notes"),
                "mentions": int(get("mentions") or 0) if get("mentions").isdigit() else 0,
                "awarded": get("awarded"),
                "hours": get("hours"),
                "url": get("url"),
                "source": get("source"),
            }
            out.append(rec)
    if args.city:
        key = args.city.lower()
        out = [r for r in out if key in r["city"].lower()]
    if args.category:
        key = args.category.lower()
        out = [r for r in out if key in r["category"].lower()]
    if args.min_mentions:
        out = [r for r in out if r["mentions"] >= args.min_mentions]
    if args.awarded_only:
        out = [r for r in out if r["awarded"]]
    return out


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


WORD_RE = re.compile(r"[a-z0-9]+")
# Substring/word-subset matches against these are meaningless on their own
# (geographic entities and generic descriptors legitimately appear inside
# many unrelated venue names — e.g. "Midnight Runners Mexico City" contains
# "Mexico City" without being the same place as the city note).
GEO_TYPES = {"cities", "countries", "states"}
STOPWORDS = {
    "bar", "cafe", "café", "restaurant", "restaurante", "the", "la", "el", "de",
    "and", "y", "house", "room", "club", "coffee", "kitchen", "bistro", "cocina",
}


def word_set(name: str) -> set[str]:
    return {w for w in WORD_RE.findall(name.lower()) if len(w) >= 3}


def existing_known_names() -> tuple[dict[str, str], dict[str, set[str]]]:
    """Returns (exact-name lookup, word-set index for fuzzy matching).

    Geographic notes (Cities/Countries/States) are excluded from the fuzzy
    pool — only exact matches count for them.
    """
    import yaml

    names: dict[str, str] = {}
    fuzzy: dict[str, set[str]] = {}
    try:
        from taste.root import all_records, build_roots

        for rec in all_records(build_roots()):
            names[norm_name(rec["name"])] = rec["name"]
            if rec.get("type") not in ("Cities", "Countries", "States"):
                ws = word_set(rec["name"]) - STOPWORDS
                if ws:
                    fuzzy[rec["name"]] = ws
    except Exception as e:
        print(f"  (roots dedupe unavailable: {e})", file=sys.stderr)

    if REFS.exists():
        for md in REFS.rglob("*.md"):
            names[norm_name(md.stem)] = md.stem
            is_geo = False
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
                m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
                if m:
                    fm = yaml.safe_load(m.group(1)) or {}
                    types = fm.get("type") or []
                    types = [types] if isinstance(types, str) else types
                    is_geo = any(
                        g in re.sub(r"[\[\]\"']", "", str(t)).strip().lower()
                        for t in types for g in GEO_TYPES
                    )
            except Exception:
                pass
            if not is_geo:
                ws = word_set(md.stem) - STOPWORDS
                if ws:
                    fuzzy[md.stem] = ws
    return names, fuzzy


def fuzzy_match(candidate_name: str, fuzzy_index: dict[str, set[str]]) -> str | None:
    """Word-subset match: if every (non-stopword) word of the shorter name
    appears in the longer name, treat as the same venue. Requires at least
    one shared word of real length to avoid coincidental collisions."""
    cand_ws = word_set(candidate_name) - STOPWORDS
    if not cand_ws:
        return None
    for known_name, known_ws in fuzzy_index.items():
        if not known_ws:
            continue
        smaller, larger = (cand_ws, known_ws) if len(cand_ws) <= len(known_ws) else (known_ws, cand_ws)
        if smaller and smaller.issubset(larger):
            return known_name
    return None


def dedupe(rows: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    known, fuzzy_index = existing_known_names()
    fresh, dupes = [], []
    seen: set[str] = set()
    for r in rows:
        key = norm_name(r["name"])
        if not key or key in seen:
            continue
        seen.add(key)
        if key in known:
            dupes.append((r["name"], known[key]))
            continue
        match = fuzzy_match(r["name"], fuzzy_index)
        if match:
            dupes.append((r["name"], match))
        else:
            fresh.append(r)
    return fresh, dupes


def to_candidate(r: dict) -> dict:
    bits = [
        f"category: {r['category']}" if r["category"] else "",
        f"city: {r['city']}" if r["city"] else "",
        f"area: {r['area']}" if r["area"] else "",
        f"AWARDED: {r['awarded']}" if r["awarded"] else "",
        f"mentioned {r['mentions']}x by creators" if r["mentions"] >= 2 else "",
        r["notes"][:280],
    ]
    return {"name": r["name"], "context": " | ".join(b for b in bits if b)}


def call_llm(prompt: dict, max_tokens: int = 16000) -> str:
    from taste import llm

    return llm.complete(prompt["system"], prompt["user"], max_tokens=max_tokens)


VERDICT_ORDER = {"go": 0, "maybe": 1, "skip": 2, "actively avoid": 3}


def write_ranked_note(label: str, verdicts: list[dict], rows_by_name: dict, dupes: list) -> Path:
    today = date.today().isoformat()
    try:
        import intake  # noqa: F401 -- Obsidian setup: ranked notes belong in the vault

        target_dir = NOTES_DIR
    except ImportError:
        target_dir = OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"Taste Ranked - {label}.md"
    verdicts = sorted(verdicts, key=lambda v: (VERDICT_ORDER.get(v.get("verdict"), 9), -v.get("weighted_score", 0)))

    lines = [
        "---",
        "tags:",
        "- taste-ranked-list",
        f"created: {today}",
        "cssclasses:",
        "- wide",
        "- table-max",
        "---",
        "",
        f"# Taste Ranked — {label}",
        f"Scored {today} · {len(verdicts)} places · verdicts: "
        + " / ".join(f"{sum(1 for v in verdicts if v.get('verdict') == k)} {k}" for k in ("go", "maybe", "skip")),
        "",
        "| Verdict | Score | Place | Category | Analog | One-liner | Maps |",
        "|---------|-------|-------|----------|--------|-----------|------|",
    ]
    for v in verdicts:
        row = rows_by_name.get(norm_name(v["candidate"]), {})
        raw_analog = v.get("closest_analog", "")
        analog = raw_analog if "[[" in raw_analog else (f"[[{raw_analog}]]" if raw_analog else "")
        one = v.get("one_liner", "").replace("|", "\\|")
        maps = f"[map]({row['url']})" if row.get("url") else ""
        lines.append(
            f"| {v.get('verdict', '?')} | {v['weighted_score']}/7 | {v['candidate']} "
            f"| {row.get('category', '')} | {analog} | {one} | {maps} |"
        )
    if dupes:
        lines += ["", f"## Already in vault ({len(dupes)})", ""]
        lines += [f"- [[{vault_name}]] (csv: {csv_name})" for csv_name, vault_name in dupes[:50]]
    out.write_text("\n".join(lines))
    return out


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def _fallback_note_body(info: dict, v: dict, today: str) -> str:
    """Generic markdown record, used when intake.py (Obsidian-specific note
    creation) isn't available -- i.e. every non-Obsidian setup. No frontmatter
    assumptions, no wikilinks, just a readable standalone file."""
    lines = [
        f"# {v['candidate']}",
        "",
        f"**Verdict: {v.get('verdict', '?').upper()} — {v['weighted_score']}/7** (confidence: {v.get('confidence', '?')})",
        "",
        v.get("one_liner", ""),
        "",
    ]
    if info.get("resolved"):
        lines += [
            f"- Address: {info.get('formatted_address', '')}",
            f"- Google rating: {info.get('google_rating', '')} ({info.get('user_ratings_total', '')} reviews)",
            f"- Type: {'/'.join(info.get('types', []))}",
            f"- Maps: {info.get('url', '')}",
            "",
        ]
    if v.get("closest_analog"):
        lines.append(f"Closest analog: {v['closest_analog']}")
    if v.get("red_flags"):
        lines.append(f"Red flags: {'; '.join(v['red_flags'])}")
    lines += ["", "## Dimensions", ""]
    for d in v.get("dimensions", []):
        lines.append(f"- **{d['name']}** ({d['score']}/7, weight {d['weight']:.2f}): {d['reason']}")
    lines += ["", f"_Scored {today}_"]
    return "\n".join(lines) + "\n"


def intake_verdicts(verdicts: list[dict], rows_by_name: dict, threshold) -> int:
    """Create a full record for every verdict that clears `threshold`.

    Every record — go, maybe, skip, or avoid — carries the full dimension
    breakdown and the one_liner explaining WHY it scored where it did.
    threshold="all" intakes every verdict regardless of score.

    Uses intake.py's Obsidian-native Place note creation when available
    (the taste-scorer skill); otherwise falls back to writing plain
    markdown files into TASTE_OUTPUT_DIR (default: ./taste_notes/) so this
    works standalone with no vault at all.
    """
    from taste.enrich import enrich

    try:
        import intake as intake_mod
        use_obsidian = True
    except ImportError:
        intake_mod = None
        use_obsidian = False
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    created = 0
    for v in verdicts:
        score = v.get("weighted_score", 0)
        if threshold == "all":
            passes = True
        elif threshold == "go":
            passes = v.get("verdict") == "go"
        else:
            passes = score >= threshold
        if not passes:
            continue

        row = rows_by_name.get(norm_name(v["candidate"]), {})
        query = row["url"] if row.get("url") and "place_id" in row.get("url", "") else f"{v['candidate']} {row.get('city', '')}".strip()
        info = enrich(query)
        if not info.get("resolved"):
            info = {"name": v["candidate"], "types": [], "localities": [c for c in [row.get("city")] if c]}

        name = safe_name(v["candidate"])
        if use_obsidian:
            path = REFS / f"{name}.md"
            if path.exists():
                intake_mod.merge_into_existing(path, v, date.today().isoformat())
            else:
                path.write_text(intake_mod.note_body(info, v, date.today().isoformat()))
            intake_mod.append_daily(name, v, info, date.today().isoformat())
        else:
            path = OUTPUT_DIR / f"{name}.md"
            path.write_text(_fallback_note_body(info, v, date.today().isoformat()))
        created += 1
    return created


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch-score a CSV of places, write a ranked note, selectively intake.")
    ap.add_argument("csv_path", help="CSV file")
    ap.add_argument("--city", help="Filter: city substring")
    ap.add_argument("--category", help="Filter: category substring")
    ap.add_argument("--min-mentions", type=int,
                    help="Filter: minimum creator mentions. WARNING: mentions is a popularity/"
                    "reach signal, not a taste signal — using this excludes candidates from "
                    "scoring entirely, including undiscovered gems with few mentions. Do not use "
                    "as a default quality gate; prefer scoring everything and letting the judge's "
                    "taste dimensions (not creator buzz) decide the verdict. Reserve for genuinely "
                    "narrowing scope (e.g. cost/time limits on a huge one-off dump).")
    ap.add_argument("--awarded-only", action="store_true", help="Filter: only awarded places")
    ap.add_argument("--limit", type=int, help="Cap candidates (cheap pilots)")
    ap.add_argument("--label", help="Ranked-note label (default: auto from filters)")
    ap.add_argument("--prep", action="store_true", help="Dump filtered candidates, no scoring")
    ap.add_argument("--prompt", action="store_true", help="BYO-model: dump batched prompts")
    ap.add_argument("--parse", action="store_true", help="BYO-model: read verdicts from stdin")
    ap.add_argument("--json", action="store_true", help="Raw JSON verdicts to stdout")
    ap.add_argument("--no-write", action="store_true", help="Skip the ranked note")
    ap.add_argument("--intake", help='Also create full Place notes: "all", "go", or a min score like "7". '
                    "Every note (go/maybe/skip/avoid) is tagged taste/<verdict> and carries the reasoning in the body.")
    args = ap.parse_args()

    rows = load_rows(Path(os.path.expanduser(args.csv_path)), args)
    print(f"Filtered: {len(rows)} rows", file=sys.stderr)

    fresh, dupes = dedupe(rows)
    print(f"After vault dedupe: {len(fresh)} new, {len(dupes)} already in vault", file=sys.stderr)

    if args.limit:
        fresh = fresh[: args.limit]

    if not fresh:
        print("Nothing to score.", file=sys.stderr)
        sys.exit(1)

    rows_by_name = {norm_name(r["name"]): r for r in rows}
    candidates = [to_candidate(r) for r in fresh]
    label = args.label or " ".join(x for x in [args.city, args.category, "awarded" if args.awarded_only else ""] if x) or Path(args.csv_path).stem

    if args.prep:
        print(json.dumps(candidates, indent=2, ensure_ascii=False))
        return

    from taste.freshness import ensure_fresh

    ensure_fresh("places", auto=True)
    profile = load_profile()
    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]

    if args.prompt:
        prompts = [build_batch_prompt(profile, b) for b in batches]
        print(json.dumps({"batches": prompts, "candidates": candidates, "label": label}, indent=2, ensure_ascii=False))
        return

    if args.parse:
        raw = sys.stdin.read()
        verdicts = parse_batch(raw)
    else:
        from taste import llm

        if llm.detect_provider() is None:
            print(f"\n{llm.NO_PROVIDER_HELP}", file=sys.stderr)
            sys.exit(2)
        verdicts = []
        est = len(batches)
        print(f"Scoring {len(candidates)} candidates in {est} batches...", file=sys.stderr)
        def score_chunk(chunk: list[dict], label: str) -> bool:
            for attempt in (1, 2):
                try:
                    raw = call_llm(build_batch_prompt(profile, chunk))
                    verdicts.extend(parse_batch(raw))
                    return True
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  {label} attempt {attempt} failed ({e})" + (", retrying..." if attempt == 1 else ""), file=sys.stderr)
            return False

        for i, batch in enumerate(batches):
            print(f"  batch {i + 1}/{est} ({len(batch)})...", file=sys.stderr)
            if score_chunk(batch, f"batch {i + 1}"):
                continue
            print(f"  batch {i + 1}: splitting in half (halves output size — truncation-proof)...", file=sys.stderr)
            mid = len(batch) // 2 or 1
            for half_label, half in ((f"batch {i + 1}a", batch[:mid]), (f"batch {i + 1}b", batch[mid:])):
                if half and not score_chunk(half, half_label):
                    print(f"  {half_label} FAILED — unscored: {[c['name'] for c in half]}", file=sys.stderr)

    if args.json:
        print(json.dumps(verdicts, indent=2, ensure_ascii=False))

    counts = {}
    for v in verdicts:
        counts[v.get("verdict", "?")] = counts.get(v.get("verdict", "?"), 0) + 1
    print(f"\nVerdicts: {counts}", file=sys.stderr)

    if not args.no_write:
        path = write_ranked_note(label, verdicts, rows_by_name, dupes)
        print(f"wrote: {path}", file=sys.stderr)

    if args.intake:
        threshold = args.intake if args.intake in ("all", "go") else int(args.intake)
        created = intake_verdicts(verdicts, rows_by_name, threshold)
        print(f"intook {created} places as notes (tagged by verdict)", file=sys.stderr)


if __name__ == "__main__":
    main()
