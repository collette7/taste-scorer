from __future__ import annotations

import re
from pathlib import Path

from taste.rescore_context import FALLBACK_VERDICT_RE
from taste.rubric import VERDICT_FROM_SCORE
from taste.verdict_quality import EvidenceVerdict


def persist(
    path: Path,
    facts: dict[str, str | int | None],
    verdict: EvidenceVerdict,
    today: str,
) -> None:
    text = path.read_text()
    new_score = verdict["weighted_score"]
    if facts["style"] == "frontmatter":
        text = re.sub(
            r"^taste:[ \t]*\d+",
            f"taste: {new_score}",
            text,
            count=1,
            flags=re.M,
        )
        summary = verdict.get("one_liner", "").replace('"', "'")
        verdict_label = VERDICT_FROM_SCORE[new_score]
        text = re.sub(
            r"^tasteVerdict:.*$",
            f'tasteVerdict: "{verdict_label} - {summary}"',
            text,
            count=1,
            flags=re.M,
        )
        text = re.sub(
            r"^tasteConfidence:.*$",
            f"tasteConfidence: {verdict.get('confidence', '')}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        text = FALLBACK_VERDICT_RE.sub(
            f"**Verdict: {VERDICT_FROM_SCORE[new_score].upper()} — {new_score}/7** "
            f"(confidence: {verdict.get('confidence', '?')})",
            text,
            count=1,
        )
    previous = facts.get("prev_score")
    previous_score = previous if isinstance(previous, int) else None
    arrow = "=" if previous_score == new_score else (
        "↑" if previous_score is None or new_score > previous_score else "↓"
    )
    delta = (
        f"{previous_score} → {new_score} {arrow}"
        if previous_score is not None
        else f"→ {new_score}"
    )
    log = (
        f"\n## {today}\n\nRescored after research: **{delta}** "
        f"({VERDICT_FROM_SCORE[new_score]}) — {verdict.get('one_liner', '')}\n"
    )
    path.write_text(text.rstrip() + "\n" + log)


def report(
    candidate: str,
    facts: dict[str, str | int | None],
    verdict: EvidenceVerdict,
) -> None:
    previous = facts.get("prev_score")
    previous_score = previous if isinstance(previous, int) else None
    new_score = verdict["weighted_score"]
    stars = "★" * new_score + "☆" * (7 - new_score)
    arrow = "=" if previous_score == new_score else (
        "↑" if previous_score is None or new_score > previous_score else "↓"
    )
    print(
        f"\n{candidate}  {stars}  {previous_score} → {new_score}/7 {arrow}  "
        f"[{VERDICT_FROM_SCORE[new_score]}]"
    )
    print(f"  → {verdict.get('one_liner', '')}")
