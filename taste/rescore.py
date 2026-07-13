#!/usr/bin/env python3
"""Rescore: re-judge an existing scored record using everything on the page.

The loop: score/clean creates a record with a first-pass verdict, you (or
your agent) append research to the file, then rescore feeds the WHOLE record
back to the judge to see whether the deeper context moves the score.

Works on both record styles:
  - Obsidian-style notes with `taste:` frontmatter (TASTE_REFS_PATH)
  - plain markdown records written by `taste clean --intake` (./taste_notes/)

Two ways to use it:

  A) Model-agnostic (any bot):
       rescore.py "Fuglen Tokyo" --prompt        # dumps {system, user} JSON
       <your LLM produces raw JSON>
       rescore.py --verdict-json < raw.json      # persists the verdict

  B) Local API key (.env):
       rescore.py "Fuglen Tokyo"
       rescore.py "Fuglen Tokyo" --dry-run       # show delta, write nothing
"""
from __future__ import annotations

from taste import _env  # noqa: F401 -- loads .env into os.environ

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from taste.paths import PROJECT_ROOT
from taste.rubric import build_single_prompt, load_profile, parse_verdict

REFS = Path(os.path.expanduser(
    os.environ.get("TASTE_REFS_PATH",
                   os.path.join(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault"), "07 References"))
))
OUTPUT_DIR = Path(os.path.expanduser(os.environ.get("TASTE_OUTPUT_DIR", str(PROJECT_ROOT / "taste_notes"))))

FALLBACK_VERDICT_RE = re.compile(r"\*\*Verdict: (\w[\w ]*?) — (\d)/7\*\*(?: \(confidence: \w+\))?")


def call_llm(prompt: dict) -> str:
    from taste import llm

    return llm.complete(prompt["system"], prompt["user"], max_tokens=1400,
                        user_prefix_len=prompt.get("user_prefix_len"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_record(name: str) -> Path | None:
    target = _norm(name)
    if not target:
        return None
    candidates = []
    for root in (REFS, OUTPUT_DIR):
        if not root.exists():
            continue
        for f in root.glob("*.md"):
            stem = _norm(f.stem)
            if stem == target:
                return f
            if target in stem or stem in target:
                candidates.append(f)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        print("ambiguous match — pick one:", file=sys.stderr)
        for c in candidates:
            print(f"  {c.stem}", file=sys.stderr)
    return None


def read_record(path: Path) -> tuple[dict, str]:
    """Return (facts, body). Handles frontmatter notes and plain records."""
    text = path.read_text()
    facts: dict = {"style": "plain", "prev_score": None}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        facts["style"] = "frontmatter"
        fm = m.group(1)
        tm = re.search(r"^taste:[ \t]*(\d+)", fm, re.M)
        facts["prev_score"] = int(tm.group(1)) if tm else None
        for key in ("address", "gRating", "type"):
            km = re.search(rf"^{key}:[ \t]*(.*)$", fm, re.M)
            if km and km.group(1).strip():
                facts[key] = km.group(1).strip()
        return facts, text[m.end():]
    vm = FALLBACK_VERDICT_RE.search(text)
    if vm:
        facts["prev_score"] = int(vm.group(2))
    return facts, text


def strip_body_noise(body: str) -> str:
    """Drop the judge's own previous output from the context: verdict blocks,
    dimension breakdowns, and past rescore-log entries. Left in, they anchor
    every subsequent rescore on its prior verdict regardless of new evidence."""
    lines = body.split("\n")
    out = []
    skip_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("## Taste verdict", "## Dimensions")) or FALLBACK_VERDICT_RE.search(line):
            skip_block = True
            i += 1
            continue
        if re.match(r"^## \d{4}-\d{2}-\d{2}\s*$", line):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].startswith("Rescored after research:"):
                skip_block = True
                i += 1
                continue
        if skip_block:
            if line.startswith("## ") or line.startswith("# "):
                skip_block = False
            else:
                i += 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out).strip()


def build_context(facts: dict, body: str, extra: str | None) -> str:
    bits = []
    fact_parts = []
    if facts.get("gRating"):
        from taste.enrich import grating_bucket
        try:
            fact_parts.append(grating_bucket(float(str(facts["gRating"]).strip("'\"")), 0))
        except ValueError:
            pass
    for k in ("address", "type"):
        if facts.get(k):
            fact_parts.append(f"{k}: {facts[k]}")
    if fact_parts:
        bits.append("KNOWN FACTS (from the record): " + " | ".join(fact_parts))
    research = strip_body_noise(body)
    if research:
        bits.append(f"RESEARCH / NOTES ACCUMULATED ON THE RECORD (weigh heavily — this is verified research):\n{research}")
    if extra:
        bits.append(extra)
    bits.append("This is a RESCORE: the candidate was scored before; judge fresh from the evidence above, not from the old score.")
    return "\n\n".join(bits)


def persist(path: Path, facts: dict, v: dict, today: str) -> None:
    text = path.read_text()
    new = v["weighted_score"]
    if facts["style"] == "frontmatter":
        text = re.sub(r"^taste:[ \t]*\d+", f"taste: {new}", text, count=1, flags=re.M)
        text = re.sub(r"^tasteVerdict:.*$",
                      f"tasteVerdict: \"{v.get('verdict', '?')} - {v.get('one_liner', '').replace(chr(34), chr(39))}\"",
                      text, count=1, flags=re.M)
        text = re.sub(r"^tasteConfidence:.*$", f"tasteConfidence: {v.get('confidence', '')}", text, count=1, flags=re.M)
    else:
        text = FALLBACK_VERDICT_RE.sub(
            f"**Verdict: {v.get('verdict', '?').upper()} — {new}/7** (confidence: {v.get('confidence', '?')})",
            text, count=1)
    prev = facts.get("prev_score")
    arrow = "=" if prev == new else ("↑" if prev is None or new > prev else "↓")
    delta = f"{prev} → {new} {arrow}" if prev is not None else f"→ {new}"
    log = f"\n## {today}\n\nRescored after research: **{delta}** ({v.get('verdict', '?')}) — {v.get('one_liner', '')}\n"
    path.write_text(text.rstrip() + "\n" + log)


def report(facts: dict, v: dict) -> None:
    prev, new = facts.get("prev_score"), v["weighted_score"]
    stars = "★" * new + "☆" * (7 - new)
    arrow = "=" if prev == new else ("↑" if prev is None or new > prev else "↓")
    print(f"\n{v['candidate']}  {stars}  {prev} → {new}/7 {arrow}  [{v.get('verdict', '?')}]")
    print(f"  → {v.get('one_liner', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-judge an existing scored record with its accumulated research as context.")
    ap.add_argument("candidate", nargs="?", help="Existing record name")
    ap.add_argument("--context", help="Extra context for the judge")
    ap.add_argument("--dry-run", action="store_true", help="Score + show delta only, write nothing")
    ap.add_argument("--prompt", action="store_true", help="BYO-model: dump prompt and exit")
    ap.add_argument("--verdict-json", action="store_true", help="BYO-model: read verdict from stdin, persist it")
    args = ap.parse_args()

    today = date.today().isoformat()

    if args.verdict_json:
        v = parse_verdict(sys.stdin.read())
        path = find_record(v["candidate"])
        if not path:
            print(f"no existing record found for: {v['candidate']}", file=sys.stderr)
            sys.exit(2)
        facts, _ = read_record(path)
        report(facts, v)
        if not args.dry_run:
            persist(path, facts, v, today)
            print(f"  updated: {path.name}")
        return

    if not args.candidate:
        ap.error("provide an existing record name, or --verdict-json")

    path = find_record(args.candidate)
    if not path:
        print(f"no existing record found for: {args.candidate}\n(rescore only re-judges existing records — run `taste score`/`taste clean --intake` first)", file=sys.stderr)
        sys.exit(2)

    facts, body = read_record(path)
    ctx = build_context(facts, body, args.context)

    from taste.freshness import ensure_fresh
    ensure_fresh("places", auto=True)
    profile = load_profile()
    prompt = build_single_prompt(profile, path.stem, ctx)

    if args.prompt:
        print(json.dumps(prompt, indent=2, ensure_ascii=False))
        return

    v = parse_verdict(call_llm(prompt))
    report(facts, v)
    if args.dry_run:
        print("  (dry run — nothing written)")
        return
    persist(path, facts, v, today)
    print(f"  updated: {path.name}")


if __name__ == "__main__":
    main()
