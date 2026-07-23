from __future__ import annotations

from pathlib import Path

from taste.rescore_context import read_record
from taste.rescore_persistence import persist
from taste.verdict_quality import EvidenceVerdict


def maybe_verdict() -> EvidenceVerdict:
    return {
        "weighted_score": 5,
        "confidence": "medium",
        "one_liner": "Nearby-only. Reviews support a cautious stop.",
        "dimensions": [],
    }


def test_persist_updates_frontmatter_record(tmp_path: Path) -> None:
    record = tmp_path / "Test Place.md"
    record.write_text(
        "---\n"
        "taste: 4\n"
        'tasteVerdict: "skip - old verdict"\n'
        "tasteConfidence: low\n"
        "---\n\n"
        "## Notes\n\nVerified review details.\n"
    )
    facts, _ = read_record(record)

    persist(record, facts, maybe_verdict(), "2026-07-22")

    updated = record.read_text()
    assert "taste: 5" in updated
    assert 'tasteVerdict: "maybe - Nearby-only. Reviews support a cautious stop."' in updated
    assert "tasteConfidence: medium" in updated
    assert "### 2026-07-22 · Latest" in updated
    assert "**UPGRADED** · `4 → 5 ↑` · `maybe`" in updated


def test_persist_updates_plain_record(tmp_path: Path) -> None:
    record = tmp_path / "Test Place.md"
    record.write_text(
        "# Test Place\n\n"
        "**Verdict: SKIP — 4/7** (confidence: low)\n\n"
        "Verified review details.\n"
    )
    facts, _ = read_record(record)

    persist(record, facts, maybe_verdict(), "2026-07-22")

    updated = record.read_text()
    assert "**Verdict: MAYBE — 5/7** (confidence: medium)" in updated
    assert "### 2026-07-22 · Latest" in updated
    assert "**UPGRADED** · `4 → 5 ↑` · `maybe`" in updated


def test_persist_migrates_legacy_logs_and_marks_latest(tmp_path: Path) -> None:
    record = tmp_path / "Test Place.md"
    record.write_text(
        "# Test Place\n\n"
        "**Verdict: SKIP — 4/7** (confidence: low)\n\n"
        "## 2026-07-08\n\n"
        "Trip note that must stay.\n\n"
        "## 2026-07-12\n\n"
        "Rescored after research: **6 → 4 ↓** (skip) — Older summary.\n"
    )
    facts, _ = read_record(record)

    persist(record, facts, maybe_verdict(), "2026-07-22")

    updated = record.read_text()
    assert updated.count("## Rescore history") == 1
    assert "### 2026-07-22 · Latest" in updated
    assert "**UPGRADED** · `4 → 5 ↑` · `maybe`" in updated
    assert updated.index("Reviews support a cautious stop.") < updated.index(
        "Older summary."
    )
    assert "Rescored after research:" not in updated
    assert "## 2026-07-08\n\nTrip note that must stay." in updated


def test_persist_demotes_the_previous_latest_entry(tmp_path: Path) -> None:
    record = tmp_path / "Test Place.md"
    record.write_text(
        "# Test Place\n\n"
        "**Verdict: SKIP — 4/7** (confidence: low)\n\n"
        "## Rescore history\n\n"
        "### 2026-07-12 · Latest\n"
        "**DOWNGRADED** · `6 → 4 ↓` · `skip`\n\n"
        "Previous summary.\n"
    )
    facts, _ = read_record(record)

    persist(record, facts, maybe_verdict(), "2026-07-22")

    updated = record.read_text()
    assert updated.count("· Latest") == 1
    assert "### 2026-07-22 · Latest" in updated
    assert "### 2026-07-12\n" in updated
    assert updated.index("Reviews support a cautious stop.") < updated.index(
        "Previous summary."
    )
