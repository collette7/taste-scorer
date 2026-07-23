from __future__ import annotations

import pytest

from taste import rescore_enrichment


def test_refresh_uses_place_id_url_and_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_enrich(query: str, *, use_cache: bool) -> dict[str, bool | str]:
        calls.append((query, use_cache))
        return {"resolved": True, "context": "fresh review evidence"}

    monkeypatch.setattr(rescore_enrichment, "enrich", fake_enrich)

    result = rescore_enrichment.fresh_place_context(
        "Candidate Name",
        {"url": "https://www.google.com/maps/place/?q=place_id:abc123"},
        enabled=True,
    )

    assert calls == [
        ("https://www.google.com/maps/place/?q=place_id:abc123", False)
    ]
    assert result == "fresh review evidence"


def test_refresh_falls_back_to_name_for_non_place_id_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_enrich(query: str, *, use_cache: bool) -> dict[str, bool | str]:
        calls.append((query, use_cache))
        return {"resolved": True, "context": "current details"}

    monkeypatch.setattr(rescore_enrichment, "enrich", fake_enrich)

    result = rescore_enrichment.fresh_place_context(
        "Exact Local Name",
        {"url": "https://map.naver.com/p/search/example"},
        enabled=True,
    )

    assert calls == [("Exact Local Name", False)]
    assert result == "current details"


def test_refresh_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(query: str, *, use_cache: bool) -> dict[str, bool | str]:
        raise AssertionError((query, use_cache))

    monkeypatch.setattr(rescore_enrichment, "enrich", fail_if_called)

    result = rescore_enrichment.fresh_place_context("Candidate", {}, enabled=False)

    assert result == ""
