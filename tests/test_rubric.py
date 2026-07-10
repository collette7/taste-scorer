import json

from taste.rubric import parse_batch, parse_verdict


def verdict_payload(score: int, verdict: str) -> dict[str, object]:
    return {
        "candidate": "Test Place",
        "candidate_type": "cafe",
        "weighted_score": score,
        "verdict": verdict,
        "dimensions": [],
        "closest_analog": "",
        "exemplars_cited": [],
        "red_flags": [],
        "one_liner": "Test verdict.",
        "confidence": "medium",
    }


def test_parse_verdict_derives_verdict_from_score() -> None:
    # Given: a model response whose verdict contradicts its deterministic score.
    raw = json.dumps(verdict_payload(score=6, verdict="maybe"))

    # When: the response crosses the parser boundary.
    parsed = parse_verdict(raw)

    # Then: the score-derived verdict wins.
    assert parsed["verdict"] == "go"


def test_parse_batch_derives_each_verdict_from_score() -> None:
    # Given: a batch with two contradictory model verdicts.
    raw = json.dumps({
        "verdicts": [
            verdict_payload(score=4, verdict="go"),
            verdict_payload(score=2, verdict="maybe"),
        ]
    })

    # When: the batch crosses the parser boundary.
    parsed = parse_batch(raw)

    # Then: every verdict matches the score rubric.
    assert [item["verdict"] for item in parsed] == ["skip", "actively avoid"]
