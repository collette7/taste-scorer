from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from taste.rescore_context import FALLBACK_VERDICT_RE
from taste.rubric import VERDICT_FROM_SCORE
from taste.verdict_quality import EvidenceVerdict

HISTORY_RE = re.compile(r"\n## Rescore history\n(?P<body>.*?)(?=\n## |\Z)", re.S)
LEGACY_RESCORE_RE = re.compile(
    r"\n## (?P<date>\d{4}-\d{2}-\d{2})\n\n"
    r"Rescored after research: (?:(?P<status>\w+) )?"
    r"\*\*(?P<delta>[^*]+)\*\* \((?P<verdict>[^)]+)\) — "
    r"(?P<summary>[^\n]*)\n?"
)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    entry_date: str
    status: str
    delta: str
    verdict: str
    summary: str

    def markdown(self, *, latest: bool) -> str:
        recency = " · Latest" if latest else ""
        return (
            f"### {self.entry_date}{recency}\n"
            f"**{self.status}** · `{self.delta}` · `{self.verdict}`\n\n"
            f"{self.summary}"
        )


def _status(previous: int | None, new: int) -> str:
    if previous is None:
        return "SCORED"
    if new > previous:
        return "UPGRADED"
    if new < previous:
        return "DOWNGRADED"
    return "UNCHANGED"


def _legacy_status(status: str | None, delta: str) -> str:
    if status:
        return status.upper()
    if "↑" in delta:
        return "UPGRADED"
    if "↓" in delta:
        return "DOWNGRADED"
    if "=" in delta:
        return "UNCHANGED"
    return "SCORED"


def _write_rescore_history(text: str, latest_entry: HistoryEntry) -> str:
    existing_entries = ""
    history_match = HISTORY_RE.search(text)
    if history_match:
        existing_entries = re.sub(
            r"^### (\d{4}-\d{2}-\d{2}) · Latest$",
            r"### \1",
            history_match.group("body").strip(),
            flags=re.M,
        )
        text = text[:history_match.start()] + text[history_match.end():]

    legacy_entries = [
        HistoryEntry(
            entry_date=match.group("date"),
            status=_legacy_status(match.group("status"), match.group("delta")),
            delta=match.group("delta"),
            verdict=match.group("verdict"),
            summary=match.group("summary"),
        ).markdown(latest=False)
        for match in reversed(list(LEGACY_RESCORE_RE.finditer(text)))
    ]
    text = LEGACY_RESCORE_RE.sub("", text).rstrip()

    entries = [latest_entry.markdown(latest=True)]
    if existing_entries:
        entries.append(existing_entries)
    entries.extend(legacy_entries)
    return text + "\n\n## Rescore history\n\n" + "\n\n".join(entries) + "\n"


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
    latest_entry = HistoryEntry(
        entry_date=today,
        status=_status(previous_score, new_score),
        delta=delta,
        verdict=VERDICT_FROM_SCORE[new_score],
        summary=verdict.get("one_liner", ""),
    )
    path.write_text(_write_rescore_history(text.rstrip(), latest_entry))


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
