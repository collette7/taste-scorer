from __future__ import annotations

import re
from pathlib import Path

from taste.enrich import grating_bucket

FALLBACK_VERDICT_RE = re.compile(
    r"\*\*Verdict: (\w[\w ]*?) — (\d)/7\*\*(?: \(confidence: \w+\))?"
)
DATE_HEADING_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}\s*$")


def read_record(path: Path) -> tuple[dict[str, str | int | None], str]:
    text = path.read_text()
    facts: dict[str, str | int | None] = {"style": "plain", "prev_score": None}
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        verdict_match = FALLBACK_VERDICT_RE.search(text)
        if verdict_match:
            facts["prev_score"] = int(verdict_match.group(2))
        return facts, text

    facts["style"] = "frontmatter"
    frontmatter = match.group(1)
    score_match = re.search(r"^taste:[ \t]*(\d+)", frontmatter, re.M)
    facts["prev_score"] = int(score_match.group(1)) if score_match else None
    for key in ("address", "gRating", "url", "type"):
        value_match = re.search(rf"^{key}:[ \t]*(.*)$", frontmatter, re.M)
        if value_match and value_match.group(1).strip():
            facts[key] = value_match.group(1).strip()
    return facts, text[match.end():]


def strip_body_noise(body: str) -> str:
    lines = body.split("\n")
    kept: list[str] = []
    skip_block = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("## Taste verdict", "## Dimensions")) or FALLBACK_VERDICT_RE.search(line):
            skip_block = True
            index += 1
            continue
        if DATE_HEADING_RE.match(line):
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and lines[next_index].startswith("Rescored after research:"):
                skip_block = True
                index += 1
                continue
        if skip_block:
            if line.startswith(("## ", "# ")):
                skip_block = False
            else:
                index += 1
                continue
        kept.append(line)
        index += 1
    return "\n".join(kept).strip()


def build_context(
    facts: dict[str, str | int | None],
    body: str,
    extra: str | None,
) -> str:
    sections: list[str] = []
    fact_parts: list[str] = []
    public_rating = facts.get("gRating")
    if public_rating:
        normalized_rating = str(public_rating).strip("'\"")
        if re.fullmatch(r"\d+(?:\.\d+)?", normalized_rating):
            fact_parts.append(grating_bucket(float(normalized_rating), 0))
    for key in ("address", "type"):
        value = facts.get(key)
        if value:
            fact_parts.append(f"{key}: {value}")
    if fact_parts:
        sections.append("KNOWN FACTS (from the record): " + " | ".join(fact_parts))
    research = strip_body_noise(body)
    if research:
        sections.append(
            "RESEARCH / NOTES ACCUMULATED ON THE RECORD "
            "(weigh heavily — this is verified research):\n" + research
        )
    if extra:
        sections.append(extra)
    sections.append(
        "This is a RESCORE: the candidate was scored before; judge fresh from "
        "the evidence above, not from the old score."
    )
    return "\n\n".join(sections)
