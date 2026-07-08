#!/usr/bin/env python3
"""Single-venue taste scorer.

Two ways to use it:

  A) Model-agnostic (Hermes / any bot):
       score.py "Fuglen Tokyo" --prompt          # dumps {system, user} JSON
       <your LLM produces raw JSON>
       score.py --parse < raw.json               # validates + pretty-prints

  B) Local Anthropic (if ANTHROPIC_API_KEY set):
       score.py "Fuglen Tokyo"
       score.py "Fuglen" "Sushi Saito" --json    # score multiple, sorted best-first
       score.py "Fuglen Tokyo" --write           # also write a note to the vault
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from rubric import build_single_prompt, load_profile, parse_verdict

HERE = Path(__file__).parent
VAULT_REFS = Path(os.path.expanduser(
    os.environ.get("TASTE_REFS_PATH",
                   os.path.join(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault"), "07 References"))
))
MODEL = os.environ.get("TASTE_MODEL", "claude-sonnet-4-5")


def call_anthropic(prompt: dict) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1400,
        system=prompt["system"],
        messages=[{"role": "user", "content": prompt["user"]}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def format_verdict(v: dict) -> str:
    stars = "★" * v["weighted_score"] + "☆" * (7 - v["weighted_score"])
    verdict_map = {"go": "GO", "maybe": "MAYBE", "skip": "SKIP", "actively avoid": "AVOID"}
    tag = verdict_map.get(v.get("verdict", ""), "?")
    lines = [
        f"\n[{tag}] {v['candidate']}  ({v.get('candidate_type', '')})",
        f"  {stars}  {v['weighted_score']}/7  ·  {v.get('confidence', '?')} confidence",
        f"  → {v.get('one_liner', '')}",
    ]
    if v.get("closest_analog"):
        lines.append(f"  ≈ closest analog in your vault: [[{v['closest_analog']}]]")
    lines.append("\n  Dimensions (sorted by weight):")
    for d in sorted(v.get("dimensions", []), key=lambda x: -x.get("weight", 0)):
        w = d.get("weight", 0)
        w_bar = "▓" * int(w * 10) + "░" * (10 - int(w * 10))
        lines.append(f"    {d['name']:22s} {d['score']}/7  w={w:.2f} {w_bar}  {d['reason']}")
    if v.get("exemplars_cited"):
        lines.append(f"\n  Anchored on: {', '.join(v['exemplars_cited'])}")
    if v.get("red_flags"):
        lines.append(f"  Red flags: {'; '.join(v['red_flags'])}")
    return "\n".join(lines)


def write_note(v: dict) -> Path:
    safe = "".join(c for c in v["candidate"] if c.isalnum() or c in " ,-").strip()
    path = VAULT_REFS / f"Taste - {safe}.md"
    today = date.today().isoformat()
    dims_yaml = "\n".join(f"  {d['name']}: {d['score']}" for d in v.get("dimensions", []))
    body = f"""---
category:
  - "[[Places]]"
tags:
  - places
  - taste-scored
type:
  - "[[Taste Scores]]"
rating: {v['weighted_score']}
verdict: {v.get('verdict', '')}
candidate_type: {v.get('candidate_type', '')}
confidence: {v.get('confidence', '')}
closest_analog: "[[{v.get('closest_analog', '')}]]"
created: {today}
one_liner: "{v.get('one_liner', '')}"
dimensions:
{dims_yaml}
---

## Verdict: {v.get('verdict', '?').upper()} — {v['weighted_score']}/7 ({v.get('confidence', '?')} confidence)

**{v.get('one_liner', '')}**

Closest analog in your vault: [[{v.get('closest_analog', '')}]]

## Dimensions

| Dimension | Score | Weight | Reason |
|-----------|-------|--------|--------|
""" + "\n".join(
        f"| {d['name']} | {d['score']}/7 | {d.get('weight', 0):.2f} | {d['reason']} |"
        for d in sorted(v.get("dimensions", []), key=lambda x: -x.get("weight", 0))
    )
    body += "\n\n## Exemplars cited\n\n" + "\n".join(f"- [[{e}]]" for e in v.get("exemplars_cited", []))
    if v.get("red_flags"):
        body += "\n\n## Red flags\n\n" + "\n".join(f"- {r}" for r in v["red_flags"])
    body += f"\n\n---\n*Generated {today} by taste-scorer.*\n"
    path.write_text(body)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM-as-judge taste scorer for individual venues.")
    ap.add_argument("candidates", nargs="*", help='One or more venues, e.g. "Fuglen Tokyo"')
    ap.add_argument("--domain", default="places",
                    help="Domain: places (default), movies, shows — needs taste_profile.<domain>.json")
    ap.add_argument("--context", help="Extra context for the judge (e.g. focus area)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="Skip enrichment lookup (default: enrich into verified facts)")
    ap.add_argument("--prompt", action="store_true",
                    help="Model-agnostic: dump {system, user} JSON instead of calling API")
    ap.add_argument("--parse", action="store_true",
                    help="Read raw model output from stdin, validate + pretty-print")
    ap.add_argument("--json", action="store_true", help="Print raw JSON")
    ap.add_argument("--write", action="store_true", help="Write a note to the vault")
    args = ap.parse_args()

    # Parse mode: read raw model output from stdin
    if args.parse:
        raw = sys.stdin.read()
        v = parse_verdict(raw)
        if args.json:
            print(json.dumps(v, indent=2, ensure_ascii=False))
        else:
            print(format_verdict(v))
        if args.write:
            print(f"\n  wrote: {write_note(v)}", file=sys.stderr)
        return

    if not args.candidates:
        ap.error("provide at least one candidate, or use --parse to consume stdin")

    from freshness import ensure_fresh

    ensure_fresh(args.domain, auto=False)
    profile = load_profile(domain=args.domain)

    enriched: list[tuple[str, str | None]] = []
    for c in args.candidates:
        ctx = args.context
        if not args.no_enrich:
            try:
                if args.domain in ("movies", "shows"):
                    from enrich_tmdb import enrich_tmdb

                    info = enrich_tmdb(c, tv=(args.domain == "shows"))
                    source = "TMDB"
                else:
                    from enrich import enrich as _enrich

                    info = _enrich(c)
                    source = "Google Places"
                if info.get("resolved"):
                    verified = f"VERIFIED ({source}): {info['context']}"
                    ctx = f"{verified}\n{args.context}" if args.context else verified
                    c = info["name"]
                else:
                    unverified = f"UNVERIFIED candidate ({info.get('reason', 'lookup failed')}) — do NOT assume its genre/format; lower confidence accordingly."
                    ctx = f"{unverified}\n{args.context}" if args.context else unverified
            except Exception as e:
                print(f"  (enrich skipped: {e})", file=sys.stderr)
        enriched.append((c, ctx))

    # Prompt mode: emit the prompt pair for an external LLM
    if args.prompt:
        prompts = [build_single_prompt(profile, c, ctx) for c, ctx in enriched]
        payload = prompts[0] if len(prompts) == 1 else prompts
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Direct mode: call Anthropic API
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "\nNo ANTHROPIC_API_KEY set. Options:\n"
            "  1. `taste \"...\" --prompt` to get the prompt pair for your own LLM\n"
            "  2. `<your llm output> | taste \"...\" --parse` to validate the response\n"
            "  3. Or set ANTHROPIC_API_KEY\n",
            file=sys.stderr,
        )
        sys.exit(2)

    results = []
    for c, ctx in enriched:
        prompt = build_single_prompt(profile, c, ctx)
        raw = call_anthropic(prompt)
        try:
            results.append(parse_verdict(raw))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"error: judge returned unparseable output for {c!r}: {e}", file=sys.stderr)
            print(raw, file=sys.stderr)
            sys.exit(3)

    results.sort(key=lambda x: -x["weighted_score"])
    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
    else:
        for v in results:
            print(format_verdict(v))
    if args.write:
        for v in results:
            print(f"\n  wrote: {write_note(v)}", file=sys.stderr)


if __name__ == "__main__":
    main()
