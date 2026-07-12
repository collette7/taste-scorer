import json

from taste.rubric import build_batch_prompt, build_single_prompt, parse_batch, parse_verdict


def minimal_profile() -> dict[str, object]:
    return {
        "summary": {"total_records": 1, "rated_count": 1},
        "persona": {"scale_max": 7, "tendency": "test"},
        "visited_cities": [],
        "tag_stats": [],
        "loc_stats": [],
        "exemplars": {},
        "top_places": [],
        "low_places": [],
    }


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


def test_build_single_prompt_prefix_splits_cleanly() -> None:
    # Given: a minimal profile and a candidate with extra context.
    profile = minimal_profile()

    # When: the single-venue prompt is built.
    prompt = build_single_prompt(profile, "Test Place", "some context")

    # Then: the prefix+suffix reconstruct the full user string, the prefix
    # ends at the profile JSON fence, and the suffix carries the candidate.
    prefix_len = prompt["user_prefix_len"]
    prefix, suffix = prompt["user"][:prefix_len], prompt["user"][prefix_len:]
    assert prefix + suffix == prompt["user"]
    assert prefix.rstrip().endswith("```")
    assert "CANDIDATE: Test Place" in suffix
    assert "CANDIDATE" not in prefix


def test_build_batch_prompt_prefix_splits_cleanly() -> None:
    # Given: a minimal profile and two candidates.
    profile = minimal_profile()
    candidates = [{"name": "A"}, {"name": "B"}]

    # When: the batch prompt is built.
    prompt = build_batch_prompt(profile, candidates)

    # Then: the prefix+suffix reconstruct the full user string, the prefix
    # ends at the profile JSON fence, and the suffix carries the candidates.
    prefix_len = prompt["user_prefix_len"]
    prefix, suffix = prompt["user"][:prefix_len], prompt["user"][prefix_len:]
    assert prefix + suffix == prompt["user"]
    assert prefix.rstrip().endswith("```")
    assert "CANDIDATES" in suffix
    assert "CANDIDATES" not in prefix
