#!/usr/bin/env python3
"""Gate: score a place and branch on the verdict. Score-only — writes nothing.

For capture (note creation + daily log) use intake.py, which always creates
the note and stores the prediction in `taste` props, never `rating`.

Gate is for bot branching: "only notify me / trigger X if it clears the bar".

Usage:
  gate.py "Fuglen Tokyo"                        # score + threshold from config
  gate.py "Fuglen Tokyo" --min-score 6          # override threshold
  gate.py "Fuglen Tokyo" --context "Tomigaya, Norwegian coffee"
  gate.py --verdict-json < verdict.json         # BYO-model: gate a pre-computed verdict

Exit codes (for bot scripting):
  0 = passed gate
  1 = scored below threshold
  2 = config/profile error
  3 = model output unparseable
"""
from __future__ import annotations

import _env  # noqa: F401 -- loads .env into os.environ before any env reads below

import argparse
import json
import os
import sys
from pathlib import Path

from rubric import build_single_prompt, load_profile, parse_verdict
from root import load_config

HERE = Path(__file__).parent
MODEL = os.environ.get("TASTE_MODEL", "claude-sonnet-4-5")


# ---- Gate logic ------------------------------------------------------------------


def gate(v: dict, min_score: int) -> int:
    passed = v["weighted_score"] >= min_score
    stars = "★" * v["weighted_score"] + "☆" * (7 - v["weighted_score"])
    print(f"\n{v['candidate']}  {stars}  {v['weighted_score']}/7  [{v.get('verdict', '?')}]")
    print(f"  → {v.get('one_liner', '')}")
    if v.get("closest_analog"):
        print(f"  ≈ {v['closest_analog']}")
    if v.get("red_flags"):
        print(f"  ⚑ {'; '.join(v['red_flags'])}")

    if not passed:
        print(f"\n  GATE: REJECTED — {v['weighted_score']} < min_score {min_score}.")
        return 1
    print(f"\n  GATE: PASSED ({v['weighted_score']} >= {min_score}). Use `taste intake` to capture it.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a place and branch on the verdict (exit 0 = pass, 1 = below bar). Writes nothing — use intake to capture.")
    ap.add_argument("candidate", nargs="?", help="Place name (optionally with city)")
    ap.add_argument("--context", help="Extra context (neighborhood, what it is)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="Skip Google Places lookup (default: enrich bare names/links)")
    ap.add_argument("--min-score", type=int, help="Threshold (default from config, else 6)")
    ap.add_argument("--prompt", action="store_true", help="BYO-model: dump {system,user} prompt and exit")
    ap.add_argument("--verdict-json", action="store_true", help="BYO-model: read a verdict JSON from stdin and gate it")
    args = ap.parse_args()

    config = load_config()
    gate_cfg = config.get("gate", {})
    min_score = args.min_score or int(gate_cfg.get("min_score", 6))

    if args.verdict_json:
        try:
            v = parse_verdict(sys.stdin.read())
        except (json.JSONDecodeError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(3)
        sys.exit(gate(v, min_score))

    if not args.candidate:
        ap.error("provide a candidate, or --verdict-json to gate a pre-computed verdict")

    try:
        profile = load_profile()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    candidate, ctx = args.candidate, args.context
    if not args.no_enrich:
        try:
            from enrich import enrich as _enrich

            info = _enrich(candidate)
            if info.get("resolved"):
                verified = f"VERIFIED (Google Places): {info['context']}"
                ctx = f"{verified}\n{args.context}" if args.context else verified
                candidate = info["name"]
            else:
                unverified = f"UNVERIFIED candidate ({info.get('reason', 'lookup failed')}) — do NOT assume its genre/format; lower confidence accordingly."
                ctx = f"{unverified}\n{args.context}" if args.context else unverified
        except Exception as e:
            print(f"  (enrich skipped: {e})", file=sys.stderr)

    prompt = build_single_prompt(profile, candidate, ctx)

    # BYO-model path A: emit prompt for Hermes to run
    if args.prompt:
        print(json.dumps(prompt, indent=2, ensure_ascii=False))
        return

    import llm

    if llm.detect_provider() is None:
        print(f"\n{llm.NO_PROVIDER_HELP}\n\nOr for gate specifically:\n"
              f"  gate.py \"{args.candidate}\" --prompt > p.json\n"
              "  <run your LLM on p.json>\n"
              "  cat raw.json | gate.py --verdict-json", file=sys.stderr)
        sys.exit(2)

    raw = llm.complete(prompt["system"], prompt["user"], max_tokens=1400)
    try:
        v = parse_verdict(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: unparseable model output: {e}", file=sys.stderr)
        sys.exit(3)

    sys.exit(gate(v, min_score))


if __name__ == "__main__":
    main()
