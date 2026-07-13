#!/usr/bin/env python3
"""Single CLI entry point. Dispatches to the taste/ package modules:

  taste.py setup                      interactive setup wizard (or --sample)
  taste.py refresh [--domain movies]  rebuild profile from your ratings
  taste.py synthesize                 distill reviews into taste principles
  taste.py score "<candidate>" [...]  judge one or more candidates
  taste.py list <file.md> [...]       extract + score candidates from markdown
  taste.py clean <file.csv> [...]     bulk CSV pipeline (filter/dedupe/score)
  taste.py rescore "<candidate>"      re-judge an existing record with its accumulated research
  taste.py research "<cand>" --notes  append research to a record, save social link, re-judge
  taste.py gate "<candidate>" [...]   score-only, exit code = verdict

Every subcommand forwards its remaining args unchanged, so all module flags
(--prompt, --parse, --json, --domain, --city, --intake, ...) work as documented.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

COMMANDS = {
    "setup": None,
    "refresh": "taste.build_profile",
    "synthesize": "taste.synthesize",
    "score": "taste.score",
    "list": "taste.list_scorer",
    "clean": "taste.batch_intake",
    "rescore": "taste.rescore",
    "research": "taste.research",
    "gate": "taste.gate",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r} (have: {', '.join(COMMANDS)})", file=sys.stderr)
        sys.exit(2)

    sys.argv = [f"taste {cmd}"] + sys.argv[2:]
    if cmd == "setup":
        import setup

        setup.main()
    else:
        runpy.run_module(COMMANDS[cmd], run_name="__main__")


if __name__ == "__main__":
    main()
