#!/usr/bin/env python3
"""Distill the user's own rating notes into per-category taste summaries.

Rationale: feeding raw per-place notes to the judge over-indexes on specific
past places ("she loved X, so anything X-like wins"). Synthesizing the WHYs
into short category-level preference statements generalizes the signal:
"in coffee, craft + warmth earns a 7; scene-y polish loses points" — without
anchoring on any one venue.

Output: taste_synthesis.json next to this script:
  {"generated_from": 49, "categories": {"cocktails": "...", "coffee": "..."},
   "general": "..."}

Run after adding notes: `taste synthesize` (or automatically via refresh).
Requires ANTHROPIC_API_KEY (one call, cached until notes change).
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "taste_synthesis.json"
VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
REFS = VAULT / os.environ.get("TASTE_REFS_DIR", "07 References")
MODEL = os.environ.get("TASTE_MODEL", "claude-haiku-4-5")

GENERIC_TAGS = {"places", "watched", "taste/go", "taste/maybe", "taste/skip", "taste/avoid"}
MIN_FOR_CATEGORY = 4

SYNTH_PROMPT = """You are distilling a person's own notes about places they rated (1-7 scale, 7 best) into general taste principles.

CRITICAL: Do NOT mention any specific place names in your summaries. The goal is
to capture WHY things earn high or low ratings as transferable principles, so a
judge can apply them to brand-new places without anchoring on past favorites.

For each category below, write 2-4 sentences capturing:
- what earns top ratings (the qualities, not the venues)
- what costs points or disappoints
- any nuances (e.g. tolerance for waits, crowds, price)

Also write a "general" summary (3-5 sentences) of cross-category patterns.

Return STRICT JSON only:
{"categories": {"<category>": "...", ...}, "general": "..."}

THE NOTES (rating, then their words):
"""


def collect_notes() -> dict[str, list[tuple[int, str]]]:
    by_cat: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for md in REFS.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="ignore")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            continue
        fm = m.group(1)
        r_m = re.search(r"^rating: *['\"]?([1-7])", fm, re.M)
        n_m = re.search(r"^notes: *(.+)$", fm, re.M)
        if not (r_m and n_m and n_m.group(1).strip()):
            continue
        tags_section = re.search(r"^tags:\n((?:\s*-\s*.+\n?)+)", fm, re.M)
        tags = re.findall(r"-\s*([^\s\"']+)", tags_section.group(1)) if tags_section else []
        tags = [t for t in tags if t not in GENERIC_TAGS]
        cat = tags[0] if tags else "other"
        by_cat[cat].append((int(r_m.group(1)), n_m.group(1).strip()))
    return by_cat


def build_prompt(by_cat: dict) -> tuple[str, int]:
    big = {c: e for c, e in by_cat.items() if len(e) >= MIN_FOR_CATEGORY}
    small = [(c, e) for c, e in by_cat.items() if len(e) < MIN_FOR_CATEGORY]

    lines = []
    total = 0
    for cat, entries in sorted(big.items(), key=lambda x: -len(x[1])):
        lines.append(f"\n## {cat}")
        for rating, note in sorted(entries, key=lambda x: -x[0]):
            lines.append(f"[{rating}] {note}")
            total += 1
    if small:
        lines.append("\n## misc (various categories — fold into 'general')")
        for cat, entries in small:
            for rating, note in entries:
                lines.append(f"[{rating}] ({cat}) {note}")
                total += 1
    return SYNTH_PROMPT + "\n".join(lines), total


def synthesize() -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY required for synthesis", file=sys.stderr)
        sys.exit(2)
    import anthropic

    by_cat = collect_notes()
    prompt, total = build_prompt(by_cat)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json\n")
    data = json.loads(raw)

    result = {"generated_from": total, "categories": data.get("categories", {}), "general": data.get("general", "")}
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> None:
    result = synthesize()
    print(f"Synthesized from {result['generated_from']} noted ratings -> {OUT.name}")
    print(f"\ngeneral: {result['general']}")
    for cat, summary in result["categories"].items():
        print(f"\n{cat}: {summary}")


if __name__ == "__main__":
    main()
