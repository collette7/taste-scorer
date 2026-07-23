#!/usr/bin/env python3
"""Re-judge an existing scored record using all current evidence."""
from __future__ import annotations

from taste import _env  # noqa: F401 -- loads .env into os.environ

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import TypedDict

from taste.paths import PROJECT_ROOT
from taste.rescore_context import build_context, read_record
from taste.rescore_enrichment import fresh_place_context
from taste.rescore_persistence import persist, report
from taste.rubric import build_single_prompt, load_profile, parse_verdict
from taste.verdict_quality import EvidenceVerdict

REFS = Path(
    os.path.expanduser(
        os.environ.get(
            "TASTE_REFS_PATH",
            os.path.join(
                os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault"),
                "07 References",
            ),
        )
    )
)
OUTPUT_DIR = Path(
    os.path.expanduser(
        os.environ.get("TASTE_OUTPUT_DIR", str(PROJECT_ROOT / "taste_notes"))
    )
)


class Prompt(TypedDict):
    system: str
    user: str
    user_prefix_len: int


def call_llm(prompt: Prompt) -> str:
    from taste import llm

    return llm.complete(
        prompt["system"],
        prompt["user"],
        max_tokens=1400,
        user_prefix_len=prompt.get("user_prefix_len"),
    )


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def find_record(name: str) -> Path | None:
    target = _norm(name)
    if not target:
        return None
    candidates: list[Path] = []
    for root in (REFS, OUTPUT_DIR):
        if not root.exists():
            continue
        for record in root.glob("*.md"):
            stem = _norm(record.stem)
            if stem == target:
                return record
            if target in stem or stem in target:
                candidates.append(record)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        print("ambiguous match — pick one:", file=sys.stderr)
        for candidate in candidates:
            print(f"  {candidate.stem}", file=sys.stderr)
    return None


def judge_with_retry(prompt: Prompt) -> EvidenceVerdict:
    current_prompt = prompt
    for attempt in (1, 2):
        try:
            return parse_verdict(call_llm(current_prompt))
        except ValueError as error:
            if attempt == 2:
                raise
            print(f"  judge output rejected ({error}); retrying once...", file=sys.stderr)
            current_prompt = {
                **current_prompt,
                "user": current_prompt["user"]
                + f"\n\nCORRECTION REQUIRED: your previous JSON was rejected because: {error}. "
                "Re-score from the supplied venue evidence. Geography is only a "
                "tie-breaker; make the decision now and return corrected JSON only.",
            }
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-judge an existing scored record with current evidence."
    )
    parser.add_argument("candidate", nargs="?", help="Existing record name")
    parser.add_argument("--context", help="Extra context for the judge")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the score delta without writing"
    )
    parser.add_argument(
        "--prompt", action="store_true", help="BYO-model: dump prompt and exit"
    )
    parser.add_argument(
        "--verdict-json",
        action="store_true",
        help="BYO-model: read verdict from stdin and persist it",
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use record contents only; do not fetch current provider details",
    )
    args = parser.parse_args()
    today = date.today().isoformat()

    if args.verdict_json:
        verdict = parse_verdict(sys.stdin.read())
        path = find_record(verdict["candidate"])
        if not path:
            print(
                f"no existing record found for: {verdict['candidate']}",
                file=sys.stderr,
            )
            sys.exit(2)
        facts, _ = read_record(path)
        report(path.stem, facts, verdict)
        if not args.dry_run:
            persist(path, facts, verdict, today)
            print(f"  updated: {path.name}")
        return

    if not args.candidate:
        parser.error("provide an existing record name, or --verdict-json")

    path = find_record(args.candidate)
    if not path:
        print(
            f"no existing record found for: {args.candidate}\n"
            "(rescore only re-judges existing records — run `taste score`/"
            "`taste clean --intake` first)",
            file=sys.stderr,
        )
        sys.exit(2)

    facts, body = read_record(path)
    context_parts: list[str] = []
    fresh = fresh_place_context(path.stem, facts, enabled=not args.no_refresh)
    if fresh:
        context_parts.append(f"FRESH VERIFIED DETAILS (current provider lookup): {fresh}")
    if args.context:
        context_parts.append(args.context)
    context = build_context(facts, body, "\n".join(context_parts) or None)

    from taste.freshness import ensure_fresh

    ensure_fresh("places", auto=True)
    prompt: Prompt = build_single_prompt(load_profile(), path.stem, context)
    if args.prompt:
        print(json.dumps(prompt, indent=2, ensure_ascii=False))
        return

    verdict = judge_with_retry(prompt)
    report(path.stem, facts, verdict)
    if args.dry_run:
        print("  (dry run — nothing written)")
        return
    persist(path, facts, verdict, today)
    print(f"  updated: {path.name}")


if __name__ == "__main__":
    main()
