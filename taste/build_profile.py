#!/usr/bin/env python3
"""Build taste_profile.json from your rating ROOTS (any configured backend).

Usage:
  build_profile.py                                   # roots from taste.config.json
  build_profile.py --csv ~/places.csv                # one-off CSV root
  build_profile.py --obsidian "~/My Vault"           # one-off Obsidian root
  build_profile.py --json ~/ratings.json             # one-off JSON root
  build_profile.py --csv a.csv --obsidian "~/Vault"  # merge (later wins on collisions)

The profile now includes a derived `persona` block (rater generosity, strongest
signals, anti-signals) computed FROM the data — no hardcoded user facts. The
rubric injects this persona into the judge prompt, so the same code distributes
to any user.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from taste.root import all_records, build_roots, load_config

from taste.paths import PROJECT_ROOT, DOMAINS_DIR

OUT = PROJECT_ROOT / "taste_profile.json"

GENERIC_TAGS = {"places", "watched", "taste/go", "taste/maybe", "taste/skip", "taste/avoid"}


def primary_tag(r: dict) -> str:
    specific = [t for t in r.get("tags", []) if t not in GENERIC_TAGS]
    return specific[0] if specific else (r.get("type") or "other")


def stratified_pick(entries: list[dict], cap: int) -> list[dict]:
    """Round-robin across distinct tags so exemplars cover many categories
    instead of whichever N happen first in file iteration order (the bug:
    a coffee-shop candidate kept being shown a furniture-shop exemplar
    just because it sorted first within the same rating)."""
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in entries:
        by_tag[primary_tag(r)].append(r)
    for group in by_tag.values():
        group.sort(key=lambda x: -x["rating"])
    picked, i = [], 0
    tags_cycle = list(by_tag.keys())
    while len(picked) < cap and any(by_tag.values()):
        tag = tags_cycle[i % len(tags_cycle)]
        if by_tag[tag]:
            picked.append(by_tag[tag].pop(0))
        i += 1
        if i > cap * len(tags_cycle):
            break
    return picked


def derive_persona(rated: list[dict], scale_max: int) -> dict:
    """Compute rater tendencies from the data instead of hardcoding them."""
    ratings = [r["rating"] for r in rated]
    overall = mean(ratings) if ratings else 0
    low_cut = scale_max // 2  # e.g. 3 on a 7-scale
    lows = [r for r in rated if r["rating"] <= low_cut]
    highs = [r for r in rated if r["rating"] >= scale_max - 1]

    # Tag signal: mean per tag, n>=2
    tag_ratings = defaultdict(list)
    for r in rated:
        for t in r["tags"]:
            tag_ratings[t].append(r["rating"])
    tag_means = {t: mean(v) for t, v in tag_ratings.items() if len(v) >= 2}
    loved_tags = sorted((t for t in tag_means if tag_means[t] >= overall), key=lambda t: -tag_means[t])[:8]
    disliked_tags = sorted((t for t in tag_means if tag_means[t] < overall - 1), key=lambda t: tag_means[t])[:5]

    if overall >= scale_max * 0.78:
        tendency = (
            f"GENEROUS rater (mean {overall:.1f}/{scale_max}). "
            f"Only {len(lows)} ratings at or below {low_cut} — a low score is a STRONG dislike signal."
        )
    elif overall <= scale_max * 0.55:
        tendency = f"HARSH rater (mean {overall:.1f}/{scale_max}). High scores are rare and meaningful."
    else:
        tendency = f"Balanced rater (mean {overall:.1f}/{scale_max})."

    return {
        "rating_mean": round(overall, 2),
        "scale_max": scale_max,
        "tendency": tendency,
        "loved_tags": loved_tags,
        "disliked_tags": disliked_tags,
        "anti_signal_examples": [
            {"name": r["name"], "rating": r["rating"], "tags": r["tags"], "context": r["context"][:120]}
            for r in sorted(lows, key=lambda x: x["rating"])[:5]
        ],
        "beloved_examples": [
            {"name": r["name"], "rating": r["rating"], "tags": r["tags"], "loc": r["loc"], "context": r["context"][:120]}
            for r in stratified_pick(highs, cap=12)
        ],
    }


def collect(records: list[dict], scale_max: int) -> dict:
    rated = [r for r in records if r["rating"] is not None]
    visited_cities = [r for r in records if r["type"] == "Cities" and r["visited"]]

    tag_ratings, loc_ratings = defaultdict(list), defaultdict(list)
    extra_stats: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for r in rated:
        for t in r["tags"]:
            tag_ratings[t].append(r["rating"])
        for l in r["loc"]:
            loc_ratings[l].append(r["rating"])
        for field in ("genre", "director"):
            for v in r.get(field, []):
                extra_stats[field][v].append(r["rating"])

    def stat_dict(d, min_n=2):
        return sorted(
            [{"key": k, "mean": round(mean(v), 2), "n": len(v)} for k, v in d.items() if len(v) >= min_n],
            key=lambda x: (-x["mean"], -x["n"]),
        )

    top = [r for r in rated if r["rating"] >= scale_max - 1]
    low = [r for r in rated if r["rating"] <= scale_max // 2]

    exemplars = defaultdict(list)
    by_rating = defaultdict(list)
    for r in rated:
        by_rating[r["rating"]].append(r)
    for rating, entries in by_rating.items():
        cap = 20 if rating >= scale_max - 1 else 6
        for r in stratified_pick(entries, cap):
            ex = {"name": r["name"], "type": r["type"], "tags": r["tags"], "loc": r["loc"]}
            for field in ("genre", "director", "year"):
                if r.get(field):
                    ex[field] = r[field]
            exemplars[rating].append(ex)

    def entry(r, with_context=False):
        e = {"name": r["name"], "rating": r["rating"], "tags": r["tags"], "loc": r["loc"]}
        for field in ("genre", "director", "year"):
            if r.get(field):
                e[field] = r[field]
        if with_context:
            e["context"] = r["context"][:120]
        return e

    profile = {
        "summary": {
            "total_records": len(records),
            "rated_count": len(rated),
            "visited_cities": len(visited_cities),
            "top_rated_count": len(top),
            "low_rated_count": len(low),
        },
        "persona": derive_persona(rated, scale_max),
        "visited_cities": sorted(
            ({"name": r["name"], "rating": r["rating"]} for r in visited_cities), key=lambda x: x["name"]
        ),
        "top_places": [entry(r) for r in stratified_pick(top, cap=len(top))],
        "low_places": [entry(r, with_context=True) for r in sorted(low, key=lambda x: x["rating"])],
        "tag_stats": stat_dict(tag_ratings)[:40],
        "loc_stats": stat_dict(loc_ratings)[:40],
        "exemplars": {str(k): v for k, v in sorted(exemplars.items(), reverse=True)},
    }
    for field, d in extra_stats.items():
        stats = stat_dict(d)
        if stats:
            profile[f"{field}_stats"] = stats[:40]
    return profile


def load_domain(name: str) -> dict:
    path = DOMAINS_DIR / f"{name}.json"
    if not path.exists():
        available = sorted(p.stem for p in DOMAINS_DIR.glob("*.json"))
        raise SystemExit(f"unknown domain {name!r} (have: {available})")
    return json.loads(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description="Build taste_profile.json from your rating roots (configured or ad-hoc backends).")
    ap.add_argument("--domain", default="places", help="Domain spec from domains/ (default: places)")
    ap.add_argument("--csv", action="append", help="Add a CSV root (repeatable)")
    ap.add_argument("--obsidian", action="append", help="Add an Obsidian vault root (repeatable)")
    ap.add_argument("--json", action="append", dest="json_src", help="Add a JSON root (repeatable)")
    ap.add_argument("--out", help="Output path (default: taste_profile.<domain>.json, places uses legacy name)")
    args = ap.parse_args()

    config = load_config()
    scale_max = int(config.get("scale_max", 7))
    domain = load_domain(args.domain)

    overrides = []
    for p in args.csv or []:
        overrides.append({"kind": "csv", "path": p, "scale_max": scale_max})
    for p in args.obsidian or []:
        overrides.append({"kind": "obsidian", "vault": p})
    for p in args.json_src or []:
        overrides.append({"kind": "json", "path": p, "scale_max": scale_max})

    domain_root_kwargs = {
        "category": domain["category"],
        "type_field_values": domain.get("type_field_values", []),
        "signal_fields": domain.get("signal_fields", ["tags", "loc"]),
    }
    if args.domain == "places":
        specs = None if not overrides else overrides
    else:
        specs = overrides or [{"kind": "obsidian", **domain_root_kwargs}]
        for s in specs:
            if s["kind"] == "obsidian":
                s.update(domain_root_kwargs)

    if args.domain == "places":
        backends = build_roots(config, specs)
    else:
        backends = build_roots(config, specs)
    records = all_records(backends)
    profile = collect(records, scale_max)
    profile["domain"] = {k: domain[k] for k in ("name", "unit", "judge_question", "candidate_types", "dimensions")}

    default_name = "taste_profile.json" if args.domain == "places" else f"taste_profile.{args.domain}.json"
    out = Path(args.out) if args.out else PROJECT_ROOT / default_name
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    s = profile["summary"]
    kinds = "+".join(b.kind for b in backends)
    print(f"Wrote {out}  (roots: {kinds})")
    print(
        f"  {s['total_records']} records | {s['rated_count']} rated | "
        f"{s['visited_cities']} visited cities | top={s['top_rated_count']} low={s['low_rated_count']}"
    )
    print(f"  persona: {profile['persona']['tendency']}")


if __name__ == "__main__":
    main()
