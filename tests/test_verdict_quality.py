from __future__ import annotations

import json

import pytest

from taste.rubric import parse_verdict
from taste.verdict_quality import EvidenceVerdict, validate_evidence_consistency


def verdict(*, score: int, confidence: str, one_liner: str) -> EvidenceVerdict:
    return {
        "weighted_score": score,
        "confidence": confidence,
        "one_liner": one_liner,
        "dimensions": [],
    }


def test_rejects_go_when_confidence_is_low() -> None:
    candidate = verdict(
        score=6,
        confidence="low",
        one_liner="Destination. Product details are documented.",
    )

    with pytest.raises(ValueError, match="low-confidence verdict cannot score above 5"):
        validate_evidence_consistency(candidate)


def test_rejects_go_when_one_liner_admits_missing_venue_evidence() -> None:
    candidate = verdict(
        score=6,
        confidence="medium",
        one_liner="Destination. No product or curation detail is available.",
    )

    with pytest.raises(ValueError, match="high score contradicts missing venue evidence"):
        validate_evidence_consistency(candidate)


def test_rejects_go_when_location_is_the_positive_basis() -> None:
    candidate = verdict(
        score=6,
        confidence="medium",
        one_liner="Destination. Beloved Roma Norte location makes this worth the trip.",
    )

    with pytest.raises(ValueError, match="location cannot be the basis for a score above 5"):
        validate_evidence_consistency(candidate)


def test_accepts_nearby_only_maybe_when_evidence_is_thin() -> None:
    candidate = verdict(
        score=5,
        confidence="low",
        one_liner="Nearby-only. Inventory and curation are undocumented.",
    )

    validate_evidence_consistency(candidate)


def test_rejects_unsupported_trait_speculation() -> None:
    candidate = verdict(
        score=5,
        confidence="low",
        one_liner="Nearby-only. The address suggests a likely curated retail aesthetic.",
    )

    with pytest.raises(ValueError, match="unsupported venue-trait speculation"):
        validate_evidence_consistency(candidate)


def test_rejects_overweighted_neighborhood_dimension() -> None:
    candidate = verdict(
        score=5,
        confidence="medium",
        one_liner="Nearby-only. Product details support a cautious stop.",
    )
    candidate["dimensions"] = [
        {
            "name": "neighborhood_context",
            "score": 7,
            "weight": 0.3,
            "reason": "Favorite neighborhood.",
        }
    ]

    with pytest.raises(ValueError, match="neighborhood_context weight cannot exceed 0.15"):
        validate_evidence_consistency(candidate)


def test_rejects_go_when_product_dimension_has_no_evidence() -> None:
    candidate = verdict(
        score=6,
        confidence="medium",
        one_liner="Destination. Make a special trip.",
    )
    candidate["dimensions"] = [
        {
            "name": "product_quality",
            "score": 6,
            "weight": 0.4,
            "reason": "No menu, product, or execution information is available.",
        }
    ]

    with pytest.raises(ValueError, match="high score contradicts missing product evidence"):
        validate_evidence_consistency(candidate)


def test_parse_verdict_enforces_evidence_consistency() -> None:
    raw = json.dumps(
        {
            "candidate": "Cibone Case",
            "weighted_score": 6,
            "verdict": "go",
            "dimensions": [],
            "one_liner": "Destination. Strong Ginza location makes this worth a trip.",
            "confidence": "medium",
        }
    )

    with pytest.raises(ValueError, match="location cannot be the basis for a score above 5"):
        parse_verdict(raw)


def test_rejects_deferred_decision_boilerplate() -> None:
    candidate = verdict(
        score=5,
        confidence="medium",
        one_liner="Nearby-only. Probe deeper before committing.",
    )

    with pytest.raises(ValueError, match="one_liner must make the decision now"):
        validate_evidence_consistency(candidate)


def test_rejects_action_that_disagrees_with_score() -> None:
    candidate = verdict(
        score=6,
        confidence="medium",
        one_liner="Nearby-only. Curated homeware with reasonable pricing.",
    )

    with pytest.raises(ValueError, match="one_liner action does not match score band"):
        validate_evidence_consistency(candidate)
