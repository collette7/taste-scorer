#!/usr/bin/env python3
"""Score a list of venues extracted from an Obsidian note.

Two ways to use it:

  A) Model-agnostic (Hermes / any bot):
       list_scorer.py "2026-05-07 FOUND..." --city LA --prompt   # dumps batched prompts
       <your LLM runs inference, returns {"verdicts": [...]} per batch>
       list_scorer.py --parse < raw.json                          # validates + prints

  B) Local Anthropic:
       list_scorer.py "2026-05-07 FOUND..." --city LA
       list_scorer.py <note> --limit 20 --write
       list_scorer.py <note> --json

  Extras:
       list_scorer.py <note> --prep     # dump extracted candidates without scoring
"""
from __future__ import annotations

import _env  # noqa: F401 -- loads .env into os.environ before any env reads below

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from rubric import build_batch_prompt, load_profile, parse_batch

HERE = Path(__file__).parent
VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
MODEL = os.environ.get("TASTE_MODEL", "claude-sonnet-4-5")
BATCH_SIZE = int(os.environ.get("TASTE_BATCH_SIZE", "10"))


# ---- Extraction ---------------------------------------------------------------

TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.+)$")
HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def strip_wikilink(s: str) -> str:
    m = WIKILINK_RE.search(s)
    return m.group(1).strip() if m else s.strip()


def resolve_note(note_arg: str) -> Path:
    p = Path(note_arg)
    if p.is_absolute() and p.exists():
        return p
    for candidate in (VAULT / note_arg, VAULT / (note_arg + ".md")):
        if candidate.exists():
            return candidate
    stem = note_arg.replace(".md", "").strip()
    matches = list(VAULT.rglob(f"{stem}.md"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        for m in matches:
            if "07 References" in str(m):
                return m
        return matches[0]
    raise FileNotFoundError(f"Note not found: {note_arg}")


def parse_candidates(text: str) -> list[dict]:
    """Extract candidate venues from a markdown blob (tables, lists, wikilinks, headers)."""
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(name: str, context: str = "", source: str = ""):
        name = name.strip().strip("`*_[]")
        name = strip_wikilink(name)
        name = re.sub(r"[\s,;:]+$", "", name)
        if not name or len(name) < 2 or len(name) > 80:
            return
        lower = name.lower()
        if lower in {"place", "name", "notes", "address", "map", "time", "type", "venue"}:
            return
        if name in seen:
            return
        seen.add(name)
        candidates.append({"name": name, "context": context.strip(), "source": source})

    in_table = False
    for line in text.splitlines():
        m = TABLE_ROW_RE.match(line)
        if m:
            cells = [c.strip() for c in m.group(1).split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                in_table = True
                continue
            if len(cells) >= 1 and cells[0]:
                if not in_table and cells[0].lower() in {"place", "name", "venue"}:
                    continue
                context = " | ".join(c for c in cells[1:] if c)[:200]
                add(cells[0], context=context, source="table")
            continue
        in_table = False

        m = BULLET_RE.match(line) or NUMBERED_RE.match(line)
        if m:
            content = m.group(1)
            wl = WIKILINK_RE.search(content)
            if wl:
                name = wl.group(1)
                context = content.replace(wl.group(0), "").strip(" —-–:")
            else:
                parts = re.split(r"\s+[—–\-:]\s+", content, maxsplit=1)
                name = parts[0]
                context = parts[1] if len(parts) > 1 else ""
            add(name, context=context[:200], source="list")
            continue

        m = HEADER_RE.match(line)
        if m:
            content = m.group(1)
            if len(content) < 60 and not re.search(r"\b(week|guide|list|the|and|or)\b", content.lower()):
                add(content, source="header")
            continue

        for match in WIKILINK_RE.finditer(line):
            add(match.group(1), source="wikilink")

    return candidates


def filter_by_city(candidates: list[dict], city: str) -> list[dict]:
    key = city.lower()
    return [c for c in candidates if key in c["name"].lower() or key in c["context"].lower()]


# ---- Model call (Anthropic path) ---------------------------------------------


def call_llm(prompt: dict, max_tokens: int = 8000) -> str:
    import llm

    return llm.complete(prompt["system"], prompt["user"], max_tokens=max_tokens)


# ---- Output -------------------------------------------------------------------

VERDICT_STYLE = {
    "go": ("GO   ", "\033[32m"),
    "maybe": ("MAYBE", "\033[33m"),
    "skip": ("SKIP ", "\033[90m"),
    "actively avoid": ("AVOID", "\033[31m"),
}
RESET = "\033[0m"


def print_ranked(verdicts: list[dict], color: bool = True) -> None:
    verdicts = sorted(verdicts, key=lambda v: (-v.get("weighted_score", 0), v["candidate"]))
    for v in verdicts:
        badge, ansi = VERDICT_STYLE.get(v.get("verdict", ""), (v.get("verdict", "?")[:5].upper(), ""))
        stars = "★" * v["weighted_score"] + "☆" * (7 - v["weighted_score"])
        c1 = ansi if color else ""
        c0 = RESET if color else ""
        analog = f"  ≈ [[{v.get('closest_analog', '')}]]" if v.get("closest_analog") else ""
        print(f"{c1}[{badge}]{c0} {stars}  {v['candidate']:38s} — {v.get('one_liner', '')}{analog}")


def write_ranked_note(source_stem: str, verdicts: list[dict], city: str | None) -> Path:
    verdicts = sorted(verdicts, key=lambda v: -v.get("weighted_score", 0))
    today = date.today().isoformat()
    city_suffix = f" [{city}]" if city else ""
    out = VAULT / "07 References" / f"Taste Ranked - {source_stem}{city_suffix}.md"

    lines = [
        "---",
        "category:",
        '  - "[[Places]]"',
        "tags:",
        "  - places",
        "  - taste-scored",
        "  - taste-ranked-list",
        f'source: "[[{source_stem}]]"',
        f"created: {today}",
        "cssclasses:",
        "  - wide",
        "  - table-max",
        "---",
        "",
        f"# Ranked verdicts for [[{source_stem}]]",
        "",
        f"Source: `{source_stem}`" + (f" · Filter: `{city}`" if city else ""),
        f"Scored: {today} · {len(verdicts)} venues",
        "",
        "| Verdict | Score | Venue | Closest analog | One-liner |",
        "|---------|-------|-------|----------------|-----------|",
    ]
    for v in verdicts:
        analog = v.get("closest_analog", "")
        analog_link = f"[[{analog}]]" if analog else ""
        one_liner = v.get("one_liner", "").replace("|", "\\|")
        lines.append(
            f"| {v.get('verdict', '?')} | {v['weighted_score']}/7 | {v['candidate']} | {analog_link} | {one_liner} |"
        )

    lines.append("\n## Full dimensions\n")
    for v in verdicts:
        lines.append(f"\n### {v['candidate']} — {v['weighted_score']}/7 ({v.get('verdict', '?')})")
        for d in sorted(v.get("dimensions", []), key=lambda x: -x.get("weight", 0)):
            lines.append(f"- **{d['name']}** {d['score']}/7 (w={d.get('weight', 0):.2f}) — {d['reason']}")
        if v.get("red_flags"):
            lines.append(f"- red flags: {'; '.join(v['red_flags'])}")

    out.write_text("\n".join(lines))
    return out


# ---- CLI ----------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a list of venues from an Obsidian note.")
    ap.add_argument("note", nargs="?", help="Note name (wikilink-style) or absolute path")
    ap.add_argument("--city", help="Only score candidates matching this city keyword")
    ap.add_argument("--limit", type=int, help="Max candidates to score")
    ap.add_argument("--json", action="store_true", help="Print raw JSON")
    ap.add_argument("--write", action="store_true", help="Write a ranked-list note back to the vault")
    ap.add_argument("--prep", action="store_true", help="Just print extracted candidates (no LLM call)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="Skip Google Places lookups (default: enrich each candidate, cached)")
    ap.add_argument("--prompt", action="store_true",
                    help="Model-agnostic: dump batched prompts as JSON array")
    ap.add_argument("--parse", action="store_true",
                    help="Read raw model output(s) from stdin, validate + rank")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    # Parse mode: consume raw LLM output(s) from stdin
    if args.parse:
        raw = sys.stdin.read()
        text = raw.strip()
        verdicts: list[dict] = []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "verdicts" in parsed:
                verdicts = parse_batch(text)
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "verdicts" in parsed[0]:
                for chunk in parsed:
                    verdicts.extend(parse_batch(json.dumps(chunk)))
            else:
                verdicts = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            fences = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            for f in fences:
                try:
                    verdicts.extend(parse_batch(f))
                except (json.JSONDecodeError, ValueError):
                    continue
            if not verdicts:
                print("could not parse stdin as verdicts JSON", file=sys.stderr)
                sys.exit(3)

        if args.json:
            print(json.dumps(verdicts, indent=2, ensure_ascii=False))
        else:
            print_ranked(verdicts, color=not args.no_color)
        if args.write and args.note:
            path = resolve_note(args.note)
            print(f"\nwrote: {write_ranked_note(path.stem, verdicts, args.city)}", file=sys.stderr)
        return

    if not args.note:
        ap.error("provide a note, or use --parse to consume stdin")

    path = resolve_note(args.note)
    text = path.read_text(encoding="utf-8", errors="ignore")
    candidates = parse_candidates(text)
    print(f"Extracted {len(candidates)} candidates from {path.name}", file=sys.stderr)

    if args.city:
        candidates = filter_by_city(candidates, args.city)
        print(f"After city={args.city!r} filter: {len(candidates)}", file=sys.stderr)

    if args.limit:
        candidates = candidates[: args.limit]

    if not candidates:
        print("No candidates found.", file=sys.stderr)
        sys.exit(1)

    if not args.no_enrich and not args.prep:
        try:
            from enrich import enrich as _enrich

            place_id_re = re.compile(r"place_id[:=][A-Za-z0-9_-]+")
            verified = 0
            for c in candidates:
                m = place_id_re.search(c.get("context", ""))
                query = m.group(0) if m else (f"{c['name']} {args.city}" if args.city else c["name"])
                info = _enrich(query)
                if info.get("resolved"):
                    c["context"] = f"VERIFIED (Google Places): {info['context']}" + (
                        f" | note: {c['context']}" if c["context"] else ""
                    )
                    verified += 1
                else:
                    c["context"] = "UNVERIFIED — do NOT assume genre/format; lower confidence." + (
                        f" | note: {c['context']}" if c["context"] else ""
                    )
            print(f"Enriched {verified}/{len(candidates)} via Google Places (cached)", file=sys.stderr)
        except Exception as e:
            print(f"  (enrich skipped: {e})", file=sys.stderr)

    if args.prep:
        print(json.dumps(candidates, indent=2, ensure_ascii=False))
        return

    profile = load_profile()
    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]

    if args.prompt:
        prompts = [build_batch_prompt(profile, b) for b in batches]
        payload = {"batches": prompts, "candidates": candidates, "batch_size": BATCH_SIZE}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    import llm

    if llm.detect_provider() is None:
        print(f"\n{llm.NO_PROVIDER_HELP}", file=sys.stderr)
        sys.exit(2)

    all_verdicts: list[dict] = []
    for i, batch in enumerate(batches):
        print(f"Scoring batch {i + 1}/{len(batches)} ({len(batch)} candidates)...", file=sys.stderr)
        try:
            raw = call_llm(build_batch_prompt(profile, batch))
            all_verdicts.extend(parse_batch(raw))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"batch {i + 1} failed to parse: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(all_verdicts, indent=2, ensure_ascii=False))
    else:
        print_ranked(all_verdicts, color=not args.no_color)

    if args.write:
        print(f"\nwrote: {write_ranked_note(path.stem, all_verdicts, args.city)}", file=sys.stderr)


if __name__ == "__main__":
    main()
