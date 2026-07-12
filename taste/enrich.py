#!/usr/bin/env python3
"""Enrich a bare place name or Google Maps link into verified facts for the judge.

Fixes the "Zest Seoul is a wine bar" hallucination problem: instead of the LLM
guessing what a candidate is, we look it up first.

Usage:
  enrich.py "Zest Seoul"
  enrich.py "https://maps.google.com/?q=place_id:ChIJ..."
  enrich.py "https://www.google.com/maps/place/Zest/@37.52..."

Returns JSON:
  {
    "name": "Zest",
    "resolved": true,
    "types": ["bar", "point_of_interest"],
    "formatted_address": "...Seoul, South Korea",
    "price_level": 3,
    "google_rating": 4.5,
    "user_ratings_total": 1200,
    "editorial_summary": "...",
    "context": "bar | $$$ | 4.5g (1200 reviews) | ...Seoul... | <summary>"
  }

API key resolution order:
  1. $TASTE_GMAPS_KEY
  2. (Obsidian users) QuickAdd plugin settings in the vault, if present

If no key or no match, returns {"resolved": false, ...} — callers should tell the
judge the candidate is UNVERIFIED and lower confidence.
"""
from __future__ import annotations

from taste import _env  # noqa: F401 -- loads .env into os.environ

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
from taste.paths import cache_path

CACHE_PATH = cache_path(".enrich_cache.json")

FIND_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
DETAIL_FIELDS = "name,types,formatted_address,price_level,rating,user_ratings_total,editorial_summary,reviews,url,geometry/location,photos,address_components"

PLACE_ID_RE = re.compile(r"place_id[:=]([A-Za-z0-9_-]+)")
MAPS_NAME_RE = re.compile(r"/maps/place/([^/@]+)")


def get_api_key() -> str | None:
    key = os.environ.get("TASTE_GMAPS_KEY")
    if key:
        return key
    qa = VAULT / ".obsidian/plugins/quickadd/data.json"
    if qa.exists():
        try:
            data = json.loads(qa.read_text())
            found: list[str] = []

            def walk(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == "Google Maps API Key" and isinstance(v, str) and v:
                            found.append(v)
                        walk(v)
                elif isinstance(obj, list):
                    for v in obj:
                        walk(v)

            walk(data)
            if found:
                return found[0]
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{qs}", timeout=10) as resp:
        return json.loads(resp.read().decode())


# Google's 1-5 ratings cluster tightly (most real businesses land 3.5-4.8) and
# are not proportional to the judge's 1-{scale_max} taste scale. Models
# reliably copy a bare number across scales despite explicit instructions not
# to (measured: 52% of a 2000-note rescore batch had weighted_score ==
# round(gRating)) -- so the judge is shown a qualitative bucket instead of
# the raw digit, removing the anchor rather than instructing around it. The
# raw value is still stored on the note/context for humans, just not phrased
# as a bare number in the judge-facing text.
def grating_bucket(value: float, count: int) -> str:
    if value >= 4.6:
        label = "excellent (Google's top tier)"
    elif value >= 4.3:
        label = "very good (solidly above Google's average)"
    elif value >= 4.0:
        label = "good (typical/average for a legitimate business on Google)"
    elif value >= 3.5:
        label = "mixed (below Google's norm, some real complaints)"
    elif value >= 3.0:
        label = "poor (well below Google's norm)"
    else:
        label = "very poor (rare on Google, strong red flag)"
    return f"Google public consensus: {label} ({count} reviews) — this is crowd approval, NOT her taste; do not reuse any number from this bucket as a score"


def parse_input(raw: str) -> dict:
    """Classify input: place_id link, maps place link, or bare name."""
    raw = raw.strip()
    m = PLACE_ID_RE.search(raw)
    if m:
        return {"kind": "place_id", "value": m.group(1)}
    m = MAPS_NAME_RE.search(raw)
    if m:
        return {"kind": "name", "value": urllib.parse.unquote_plus(m.group(1))}
    if raw.startswith("http"):
        # Unrecognized link shape (shortened goo.gl etc.) — try to follow redirects
        try:
            req = urllib.request.Request(raw, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                final = resp.geturl()
            m = PLACE_ID_RE.search(final) or MAPS_NAME_RE.search(final)
            if m:
                v = m.group(1)
                kind = "place_id" if "place_id" in (PLACE_ID_RE.pattern if PLACE_ID_RE.search(final) else "") else "name"
                if PLACE_ID_RE.search(final):
                    return {"kind": "place_id", "value": PLACE_ID_RE.search(final).group(1)}
                return {"kind": "name", "value": urllib.parse.unquote_plus(v)}
        except OSError:
            pass
        return {"kind": "unresolvable_link", "value": raw}
    return {"kind": "name", "value": raw}


PRICE = {0: "free", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

# Google returns official/localized city names that don't match how she
# actually refers to places in her vault. Normalize at the source so every
# consumer (intake, batch_intake) gets consistent loc links.
LOCALITY_ALIASES = {
    "Ciudad de México": "CDMX",
    "Mexico City": "CDMX",
}


def normalize_locality(name: str) -> str:
    return LOCALITY_ALIASES.get(name, name)


def _cache_load() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _cache_save(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False))
    except OSError:
        pass


def enrich(raw: str, api_key: str | None = None, use_cache: bool = True) -> dict:
    cache = _cache_load() if use_cache else {}
    cache_key = raw.strip().lower()
    if use_cache and cache_key in cache:
        return cache[cache_key]

    result = _enrich_uncached(raw, api_key)

    if use_cache and result.get("resolved"):
        cache[cache_key] = result
        _cache_save(cache)
    return result


def _enrich_uncached(raw: str, api_key: str | None = None) -> dict:
    api_key = api_key or get_api_key()
    parsed = parse_input(raw)

    if parsed["kind"] == "unresolvable_link":
        return {"name": raw, "resolved": False, "reason": "unrecognized link format"}
    if not api_key:
        return {"name": parsed["value"], "resolved": False, "reason": "no Google Maps API key"}

    place_id = None
    if parsed["kind"] == "place_id":
        place_id = parsed["value"]
    else:
        data = _get(FIND_URL, {
            "input": parsed["value"],
            "inputtype": "textquery",
            "fields": "place_id",
            "key": api_key,
        })
        cands = data.get("candidates", [])
        if not cands:
            from taste.enrich_naver import enrich_naver, looks_korean

            if looks_korean(parsed["value"]):
                naver = enrich_naver(parsed["value"])
                if naver.get("resolved"):
                    return naver
            return {"name": parsed["value"], "resolved": False, "reason": "no Places match"}
        place_id = cands[0]["place_id"]

    data = _get(DETAILS_URL, {"place_id": place_id, "fields": DETAIL_FIELDS, "key": api_key})
    r = data.get("result")
    if not r:
        return {"name": parsed["value"], "resolved": False, "reason": f"details lookup failed: {data.get('status')}"}

    types = [t for t in r.get("types", []) if t not in ("point_of_interest", "establishment")]
    summary = (r.get("editorial_summary") or {}).get("overview", "")
    price = PRICE.get(r.get("price_level"), "")
    review_snippets = [
        rv["text"].strip().replace("\n", " ")[:200]
        for rv in (r.get("reviews") or [])[:3]
        if rv.get("text", "").strip()
    ]
    reviews_bit = " || ".join(f'"{s}"' for s in review_snippets)
    bits = [
        "/".join(types) if types else "",
        price,
        f"{r.get('rating')}g ({r.get('user_ratings_total')} reviews)" if r.get("rating") else "",
        r.get("formatted_address", ""),
        summary,
        f"review excerpts: {reviews_bit}" if reviews_bit else "",
    ]
    loc = (r.get("geometry") or {}).get("location") or {}
    photos = r.get("photos") or []
    photo_url = ""
    if photos:
        ref = photos[0].get("photo_reference", "")
        if ref:
            photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={ref}&key={api_key}"
    localities = list(dict.fromkeys(
        normalize_locality(c["long_name"])
        for c in r.get("address_components", [])
        if any(t in c.get("types", []) for t in ("locality", "sublocality_level_1", "neighborhood", "administrative_area_level_1", "country"))
    ))
    return {
        "name": r.get("name", parsed["value"]),
        "resolved": True,
        "place_id": place_id,
        "types": types,
        "formatted_address": r.get("formatted_address", ""),
        "price_level": r.get("price_level"),
        "google_rating": r.get("rating"),
        "user_ratings_total": r.get("user_ratings_total"),
        "editorial_summary": summary,
        "review_snippets": review_snippets,
        "url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "photo_url": photo_url,
        "localities": localities,
        "context": " | ".join(b for b in bits if b),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: enrich.py <name or maps link>", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(enrich(sys.argv[1]), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
