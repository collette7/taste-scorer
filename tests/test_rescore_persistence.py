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
    assert "Rescored after research: **4 → 5 ↑** (maybe)" in updated


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
    assert "Rescored after research: **4 → 5 ↑** (maybe)" in updated
