#!/usr/bin/env python3
"""Enrich a movie/show title into verified TMDB facts for the judge.

Usage:
  enrich_tmdb.py "Perfect Days"
  enrich_tmdb.py "Severance" --tv

API key: $TASTE_TMDB_KEY, else auto-read from the vault's QuickAdd settings
(same key Movies.js uses).
"""
from __future__ import annotations

from taste import _env  # noqa: F401 -- loads .env into os.environ

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path(os.path.expanduser(os.environ.get("TASTE_VAULT_PATH", "~/Documents/Obsidian Vault")))
from taste.paths import cache_path

CACHE_PATH = cache_path(".enrich_tmdb_cache.json")
BASE = "https://api.themoviedb.org/3"


def get_api_key() -> str | None:
    key = os.environ.get("TASTE_TMDB_KEY")
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
                        if k == "TMDB API Key" and isinstance(v, str) and v:
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


def _get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{BASE}{path}?{qs}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def _cache_load() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def enrich_tmdb(title: str, tv: bool = False, api_key: str | None = None, use_cache: bool = True) -> dict:
    cache = _cache_load() if use_cache else {}
    cache_key = f"{'tv' if tv else 'movie'}:{title.strip().lower()}"
    if use_cache and cache_key in cache:
        return cache[cache_key]

    api_key = api_key or get_api_key()
    if not api_key:
        return {"name": title, "resolved": False, "reason": "no TMDB API key"}

    kind = "tv" if tv else "movie"
    data = _get(f"/search/{kind}", {"query": title, "api_key": api_key})
    results = data.get("results", [])
    if not results:
        return {"name": title, "resolved": False, "reason": "no TMDB match"}

    hit = results[0]
    tmdb_id = hit["id"]
    detail = _get(f"/{kind}/{tmdb_id}", {"api_key": api_key, "append_to_response": "credits"})

    name = detail.get("title") or detail.get("name") or title
    year = (detail.get("release_date") or detail.get("first_air_date") or "")[:4]
    genres = [g["name"] for g in detail.get("genres", [])]
    directors = [c["name"] for c in detail.get("credits", {}).get("crew", []) if c.get("job") == "Director"]
    creators = [c["name"] for c in detail.get("created_by", [])]
    cast = [c["name"] for c in detail.get("credits", {}).get("cast", [])[:5]]
    overview = (detail.get("overview") or "")[:300]
    rating = detail.get("vote_average")
    votes = detail.get("vote_count")
    seasons = detail.get("number_of_seasons")
    episodes = detail.get("number_of_episodes")

    bits = [
        f"{name} ({year})" if year else name,
        "/".join(genres),
        f"dir: {', '.join(directors)}" if directors else (f"created by: {', '.join(creators)}" if creators else ""),
        f"cast: {', '.join(cast[:3])}" if cast else "",
        f"TMDB {rating}/10 ({votes} votes)" if rating else "",
        f"{seasons} seasons / {episodes} eps" if seasons else "",
        overview,
    ]
    result = {
        "name": name,
        "resolved": True,
        "tmdb_id": tmdb_id,
        "year": year,
        "genres": genres,
        "directors": directors or creators,
        "cast": cast,
        "tmdb_rating": rating,
        "overview": overview,
        "seasons": seasons,
        "poster": f"https://image.tmdb.org/t/p/w500{detail['poster_path']}" if detail.get("poster_path") else "",
        "context": " | ".join(b for b in bits if b),
    }
    if use_cache:
        cache[cache_key] = result
        try:
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False))
        except OSError:
            pass
    return result


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--tv"]
    tv = "--tv" in sys.argv
    if not args:
        print("usage: enrich_tmdb.py <title> [--tv]", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(enrich_tmdb(args[0], tv=tv), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
