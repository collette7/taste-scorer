#!/usr/bin/env python3
"""Research: persist research findings onto an existing scored record, then re-judge.

The research itself (web, social, editorial) is done by you or your agent;
this command persists it and feeds it back to the judge:

  research.py "Fuglen Tokyo" --notes "text of findings"
  research.py "Fuglen Tokyo" --file findings.md
  cat findings.md | research.py "Fuglen Tokyo"
  research.py "Fuglen Tokyo" --notes "..." --no-rescore

Findings land under a dated `## YYYY-MM-DD` heading on the record, and the
first instagram.com link found (or --social) is recorded as the venue's
social link. Then the normal rescore flow runs (score delta logged on the
record) unless --no-rescore.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from taste.rescore import find_record

IG_LINK_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[^\s)\"'\]>]+")


def extract_social(explicit: str | None, research_text: str) -> str:
    if explicit:
        return explicit.strip()
    m = IG_LINK_RE.search(research_text)
    return m.group(0) if m else ""


def set_social(path: Path, social: str) -> bool:
    """Record the social link on the record. Frontmatter notes get a
    `social:` prop; plain records get a `Social:` line after the verdict.
    Never overwrites an existing non-empty value."""
    if not social:
        return False
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        fm = m.group(1)
        existing = re.search(r"^social:[ \t]*(\S.*)?$", fm, re.M)
        if existing and (existing.group(1) or "").strip().strip("\"'"):
            return False
        if existing:
            fm = re.sub(r"^social:.*$", f"social: {social}", fm, count=1, flags=re.M)
        else:
            fm += f"\nsocial: {social}"
        path.write_text(f"---\n{fm}\n---\n" + text[m.end():])
        return True
    if re.search(r"^Social: \S", text, re.M):
        return False
    vm = re.search(r"^\*\*Verdict:.*$", text, re.M)
    insert_at = vm.end() if vm else 0
    path.write_text(text[:insert_at] + f"\nSocial: {social}" + text[insert_at:])
    return True


def append_research(path: Path, research: str, today: str) -> None:
    text = path.read_text().rstrip()
    path.write_text(text + f"\n\n## {today}\n\n### Research\n\n{research.strip()}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Append research to an existing scored record, record its social link, and re-judge.")
    ap.add_argument("candidate", help="Existing record name")
    ap.add_argument("--notes", help="Research text (or use --file / stdin)")
    ap.add_argument("--file", help="Read research text from a file")
    ap.add_argument("--social", help="Social/IG link (else auto-extracted from research text)")
    ap.add_argument("--no-rescore", action="store_true", help="Persist research + social only, skip re-judging")
    args = ap.parse_args()

    path = find_record(args.candidate)
    if not path:
        print(f"no existing record found for: {args.candidate}\n(research persists onto existing records — run `taste score`/`taste clean --intake` first)", file=sys.stderr)
        sys.exit(2)

    if args.notes:
        research = args.notes
    elif args.file:
        research = Path(args.file).expanduser().read_text()
    elif not sys.stdin.isatty():
        research = sys.stdin.read()
    else:
        ap.error("provide research via --notes, --file, or stdin")
    if not research.strip() and not args.social:
        ap.error("research text is empty and no --social given — nothing to persist")

    today = date.today().isoformat()
    if research.strip():
        append_research(path, research, today)
        print(f"  research appended: {path.name} › ## {today}")

    social = extract_social(args.social, research)
    if social:
        if set_social(path, social):
            print(f"  social: {social}")
        else:
            print("  social: already set, left untouched")

    if args.no_rescore:
        return

    sys.stdout.flush()
    import runpy
    sys.argv = ["taste rescore", path.stem]
    runpy.run_module("taste.rescore", run_name="__main__")


if __name__ == "__main__":
    main()
