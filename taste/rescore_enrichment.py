from __future__ import annotations

from collections.abc import Mapping

from taste.enrich import enrich


def fresh_place_context(
    name: str,
    facts: Mapping[str, str | int | None],
    *,
    enabled: bool,
) -> str:
    if not enabled:
        return ""

    url = facts.get("url")
    query = url if isinstance(url, str) and "place_id:" in url else name
    enrichment = enrich(query, use_cache=False)
    if not enrichment.get("resolved"):
        return ""
    context = enrichment.get("context")
    return context if isinstance(context, str) else ""
