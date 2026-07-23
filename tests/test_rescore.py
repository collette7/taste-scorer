from __future__ import annotations

import json

import pytest

from taste import rescore


def test_judge_retries_once_after_quality_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "candidate": "Test Place",
                    "weighted_score": 6,
                    "verdict": "go",
                    "dimensions": [],
                    "one_liner": "Destination. Strong neighborhood signal.",
                    "confidence": "medium",
                }
            ),
            json.dumps(
                {
                    "candidate": "Test Place",
                    "weighted_score": 5,
                    "verdict": "maybe",
                    "dimensions": [],
                    "one_liner": "Nearby-only. Menu details remain undocumented.",
                    "confidence": "low",
                }
            ),
        ]
    )
    calls: list[str] = []

    def fake_call_llm(prompt: rescore.Prompt) -> str:
        calls.append(prompt["user"])
        return next(responses)

    monkeypatch.setattr(rescore, "call_llm", fake_call_llm)
    prompt: rescore.Prompt = {
        "system": "system",
        "user": "candidate evidence",
        "user_prefix_len": 0,
    }

    verdict = rescore.judge_with_retry(prompt)

    assert verdict["weighted_score"] == 5
    assert len(calls) == 2
    assert "CORRECTION REQUIRED" in calls[1]
