"""Pure, model-agnostic prompt + schema for the taste scorer.

No SDK dependencies. Import this from any bot (Hermes, Claude Code, GPT, local llama)
to get the exact (system, user) prompt pair and a schema validator.

Usage from Hermes (or any bot):

    from taste import build_single_prompt, build_batch_prompt, parse_verdict, load_profile
    profile = load_profile()  # reads taste_profile.json next to this file
    prompt = build_single_prompt(profile, candidate="Fuglen Tokyo")
    raw = my_llm.complete(system=prompt["system"], user=prompt["user"])
    verdict = parse_verdict(raw)   # dict matching the schema
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from taste.paths import PROJECT_ROOT as HERE
from taste.verdict_quality import validate_evidence_consistency

PROFILE_PATH = HERE / "taste_profile.json"

RATING_SCALE = """Rating scale:
  7 = perfect, life-changing, must try
  6 = excellent, worth repeating
  5 = good, enjoyable
  4 = passable
  3 = bad, avoid if possible
  2 = atrocious
  1 = evil"""

DIMENSIONS: list[tuple[str, str]] = [
    ("product_quality", "Is the actual thing they make (food, coffee, drinks, product) at the level of the user's top-rated equivalents?"),
    ("atmosphere_fit", "Vibe match against the persona's loved_tags vs disliked_tags and beloved vs anti-signal examples — judged from the VENUE ITSELF (space, service, crowd, format), never from the surrounding neighborhood's reputation or tourist-district status. Geography belongs only in neighborhood_context, which is capped low; don't let it leak in here as a proxy."),
    ("neighborhood_context", "Minor, tie-breaking signal only — a WEAK bonus if the area matches her loved clusters, never a penalty for an unfamiliar or 'unproven' neighborhood. A single beloved counter-service taco stand or hole-in-the-wall can easily outrank a whole neighborhood's reputation; don't let geography override product_quality or atmosphere_fit. Weight this dimension low (0.05-0.15) unless the venue's format is explicitly neighborhood-dependent (e.g. a walking district)."),
    ("design_aesthetic", "Physical space craft — does it match the aesthetic implied by the user's top-rated venues?"),
    ("similarity_to_loved", "How closely does it map to a specific top-rated exemplar with the SAME experience format — not just shared tags or category? Match on what you actually do there and how it operates (solo-run food kissa ≠ dance club with listening sessions ≠ craft cocktail bar, even though all touch music/drinks). A weaker same-format match beats a strong keyword match on a famous name. Score honestly lower when no true format twin exists."),
    ("anti_signal_risk", "How much does it resemble the persona's anti_signal_examples? HIGHER score = LESS risk. Max score = zero red flags."),
]

VERDICT_FROM_SCORE = {7: "go", 6: "go", 5: "maybe", 4: "skip", 3: "actively avoid", 2: "actively avoid", 1: "actively avoid"}

SINGLE_SCHEMA = """{
  "candidate": "...",
  "candidate_type": "restaurant|cafe|matcha|bar|shop|other",
  "weighted_score": 1-7 integer,
  "verdict": "go|maybe|skip|actively avoid",
  "dimensions": [
    {"name": "...", "score": 1-7, "weight": 0.0-1.0, "reason": "..."}
  ],
  "closest_analog": "wikilink(s) of EXACT note name(s) from the profile, e.g. \\"[[tonlist]]\\" or \\"[[Music Bar Lion]] [[Baltra Bar]]\\" — no scores, no commentary, no parentheses. Must share the candidate's experience FORMAT, not just tags/keywords. The user is a tastemaker who prizes deep cuts: prefer the LESS-CITED exemplar that fits the format precisely over a famous name that fits loosely (e.g. a tiny jazz kissa maps to [[JazzHaus POSY]], not [[Kohiko Coffee House]]; a solo-bartender omakase maps to [[Gen Yamamoto]], not [[Double Chicken Please]]). Empty string if no true format match exists — preferred over a misleading analog.",
  "exemplars_cited": ["<exact note name from the profile>", ...],
  "awards": ["michelin", "worldsbest", "jamesbeard", "blueribbon", "los250mx", "tabelog", ...] or [] — major recognized awards you know this SPECIFIC candidate holds (Michelin star/Bib Gourmand, World's 50 Best Restaurants/Bars, James Beard, Blue Ribbon Survey [Japan], Los 50 Mejores/Los250MX [Mexico], Tabelog Award Gold list [Japan], etc.), from training knowledge or provided research context. Only include if genuinely known/verified for this exact venue — never guess. Awards are informational, not a scoring boost (see the tastemaker framing above),
  "red_flags": ["..."],
  "one_liner": "single decisive sentence. Lead with the action: destination / route stop / nearby-only / skip. Cite only venue-specific facts supplied in candidate context. Never infer warmth, craft, curation, aesthetics, authenticity, intimacy, soul, or character from the name, neighborhood, price, or venue category.",
  "confidence": "low|medium|high"
}"""

BATCH_SCHEMA = "{\"verdicts\": [" + SINGLE_SCHEMA + ", ...]}"


def _persona_block(profile: dict) -> str:
    """Render the data-derived persona. Falls back gracefully for old profiles."""
    p = profile.get("persona")
    if not p:
        return "- No persona block in profile; infer tendencies from tag_stats/exemplars directly."
    lines = [f"- {p['tendency']}"]
    if p.get("loved_tags"):
        lines.append(f"- Strongest positive signals (tags): {', '.join(p['loved_tags'])}")
    if p.get("disliked_tags"):
        lines.append(f"- Weak/negative signals (tags): {', '.join(p['disliked_tags'])}")
    if p.get("beloved_examples"):
        lines.append("- Beloved places:")
        for e in p["beloved_examples"][:5]:
            ctx = e.get("context", "")
            tag_str = f" tags={e.get('tags', [])}" if e.get("tags") else ""
            why = f" — {ctx[:120]}" if ctx else ""
            lines.append(f"    • {e['name']} ({e['rating']}){tag_str}{why}")
    if p.get("anti_signal_examples"):
        lines.append("- ANTI-SIGNAL (actively disliked — treat resemblance as red flags):")
        for e in p["anti_signal_examples"]:
            ctx = e.get("context", "")
            why = f" — {ctx[:120]}" if ctx else ""
            lines.append(f"    • {e['name']} ({e['rating']}){why}")
    return "\n".join(lines)


def _synthesis_block(profile: dict) -> str:
    synth = profile.get("taste_synthesis") or {}
    if not synth.get("general") and not synth.get("categories"):
        return ""
    lines = ["\nIn the user's own words (synthesized from their rating notes — these are",
             "PRINCIPLES to apply to new candidates, deliberately free of place names so",
             "you generalize rather than pattern-match on past favorites):"]
    if synth.get("general"):
        lines.append(f"- Overall: {synth['general']}")
    for cat, summary in synth.get("categories", {}).items():
        lines.append(f"- {cat}: {summary}")
    return "\n".join(lines) + "\n"


def _system_preamble(profile: dict) -> str:
    scale_max = profile.get("persona", {}).get("scale_max", 7)
    persona = _persona_block(profile)
    synthesis = _synthesis_block(profile)
    domain = profile.get("domain", {})
    dimensions = domain.get("dimensions", DIMENSIONS)
    unit = domain.get("unit", "venue")
    question = domain.get("judge_question", "Is this specific place worth the user's time?")
    dims = chr(10).join(f"   - {k}: {d}" for k, d in dimensions)
    return f"""You are the user's personal taste model for individual {unit.upper()}S. The question you always answer is: "{question}"

You are grounded in a dataset of {unit}s the user has personally rated 1-{scale_max}.

TWO DIFFERENT RATING SCALES — DO NOT CONFLATE THEM:
- `rating` in the profile (beloved/anti-signal examples, exemplars) is HER personal 1-{scale_max} taste score. This is the only scale that matters.
- `gRating` on a new candidate is Google's public 1-5 crowd-consensus star rating — mass-market approval, not her taste. A candidate's gRating being numerically close to an exemplar's personal `rating` is MEANINGLESS (different scales, different things measured). A place can have gRating 4.6/5 (broadly loved by the public) and still be exactly the kind of flat, soulless venue she dislikes — public approval and her personal fit are independent. Treat gRating only as a weak, secondary signal for product_quality (very high or very low can be informative), never as a stand-in for her opinion.
- BANNED reasoning pattern, do not write anything shaped like this: "gRating 3.0 mirrors [[Bar Doko]] (rated 3)" or "candidate's low gRating echoes exemplar X's rating of Y". Citing an exemplar as a comparison requires shared FORMAT or TEXTUAL evidence (a review, a description), never a coincidental number match between a 1-5 public scale and her 1-{scale_max} personal scale. If you catch yourself about to name an exemplar because its number looks like the candidate's gRating, delete that sentence and either cite real evidence or say confidence is low.
- When a candidate has near-zero context (bare gRating and nothing else — no reviews, no description, no research), do NOT name ANY specific profile exemplar (beloved or anti-signal) in your reasoning at all, even generically ("resembles X," "echoes Y," "matching the anti-signal of Z"). There is no evidence a low-information candidate resembles anything specific. Say plainly that data is too thin to compare, set confidence to low, and let the dimension scores (not a borrowed exemplar's name) carry the judgment.
The user is a TASTEMAKER: she finds places before they're discovered, prizes deep cuts and hidden gems, and actively avoids whatever the algorithm serves everyone else. Her track record proves it — she visited several venues BEFORE they won World's 50 Best recognition (Handshake Speakeasy, Bar Mauro, FORM + MATTER); awards follow her taste, they don't lead it. So treat awards as a neutral-to-mild signal, not the signal itself: judge every place on its craft-and-warmth DNA, and never boost or penalize a candidate simply because a list did or didn't notice it yet. When judging, weight what a place IS over what it's known for.

She is NOT anti-upscale — she rates genuine fine dining highly when the craft is real (Sud 777: "Fine dining but in a casual garden setting. Felt natural, not performative" — a 7). Price tier is not an anti-signal. Her actual anti-signals are: (1) overpriced relative to what's delivered — paying a premium without a matching craft/quality payoff, and (2) aesthetic-over-substance traps — places engineered for photos/virality with no craft, taste, or soul behind the surface, at ANY price point (a $8 influencer-bait matcha stand is just as much an anti-signal as a $200 tasting menu coasting on plating). Judge every candidate — cheap or expensive — on whether the craft is real, not on how much it costs or how polished it looks.

{RATING_SCALE}

Empirical facts about this user (derived from their data — trust these):
{persona}
{synthesis}

Method for each candidate:
1. Decompose on these weighted dimensions:
{dims}
2. Score each dim 1-{scale_max} with weight 0-1 (weight = how much this dim matters for THIS candidate given profile signal).
3. weighted_score = round(sum(score_i * weight_i) / sum(weight_i))
4. Verdict from weighted_score:
     {scale_max} or {scale_max - 1} → "go"
     {scale_max - 2}      → "maybe"
     {scale_max - 3}      → "skip"
     <={scale_max - 4}    → "actively avoid"
5. closest_analog: one or more profile note names, verbatim, each wrapped as a [[wikilink]]. The analog must match the candidate's actual EXPERIENCE FORMAT — what you physically do there and how the place operates (a solo-run food kissa, a craft cocktail bar, and a dance club hosting listening sessions are three DIFFERENT formats even if all involve music and drinks). Shared tags or surface keywords ("listening", "cocktails", "coffee") are NOT enough. The user is a tastemaker who prizes deep cuts and hidden gems: her exemplar list is deliberately deep, and the BEST analog is usually a niche one — a 90-year-old proprietor's jazz kissa, a solo-bartender fruit-cocktail omakase, a records-and-lemonade kissa — not the handful of famous names that fit everything loosely. Before defaulting to a frequently-cited exemplar, scan the FULL list for a rarer, tighter format twin; citing the same 3 anchors for every candidate is a scoring failure. If no exemplar truly matches the format, return "" — an empty analog is more useful than a misleading one. Reasoning belongs in the similarity_to_loved dimension's reason, never in this field.
6. Flag red flags — anything resembling the anti-signal examples.
7. awards: if you genuinely know this specific candidate holds a major recognized award (Michelin, World's 50 Best, James Beard, Blue Ribbon Survey, Los250MX, Tabelog Award Gold list), name it. Google Places has no award data — this is the only source for it, so check your knowledge deliberately. Never guess or infer from price/reputation alone.
8. NEVER infer a qualitative trait (warmth, flatness, craft, soul, "trying too hard") from numeric rating proximity alone. A candidate's star rating happening to be close to a beloved or anti-signal exemplar's rating is a coincidence, not evidence — base qualitative claims only on actual text (review excerpts, editorial summary, provided research). If the only data is a bare number, say so and lower confidence instead of inventing a narrative to match it.
9. Evidence ceiling: a candidate with no venue-specific evidence about its actual offer or execution cannot score above 5 and must use low confidence. Address, neighborhood, city affinity, category, name, price tier, and public-rating bucket do NOT count as venue-specific evidence. They can move a thin candidate between 4 and 5 only. A 6-7 requires supplied facts such as menu/products, preparation, inventory/brands, owner/chef/bartender, service reports, operating format, editorial description, or concrete review excerpts. `neighborhood_context` weight must never exceed 0.15.
10. One-liner contract: make the decision useful without generic research boilerplate. The first words MUST match the score: 7 = "Destination."; 6 = "Destination." or "Route stop."; 5 = "Route stop." or "Nearby-only."; 1-4 = "Skip." Then name the strongest supplied venue fact. If evidence is thin, name the candidate-type-specific gap. Do not defer the decision or invent a venue trait."""


def _compact_profile(profile: dict) -> dict:
    compact = {
        "summary": profile["summary"],
        "persona": profile.get("persona", {}),
        "visited_cities": profile.get("visited_cities", []),
        "tag_stats": profile["tag_stats"][:25],
        "loc_stats": profile["loc_stats"][:25],
        "exemplars_by_rating": profile["exemplars"],
        "top_places_sample": profile["top_places"][:120],
        "low_places": profile["low_places"],
    }
    for key in ("genre_stats", "director_stats"):
        if profile.get(key):
            compact[key] = profile[key][:25]
    return compact


def load_profile(path: Path | str | None = None, domain: str = "places") -> dict:
    if path:
        p = Path(path)
    elif domain == "places":
        p = PROFILE_PATH
    else:
        p = HERE / f"taste_profile.{domain}.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing. Run build_profile.py --domain {domain} first.")
    profile = json.loads(p.read_text())
    synth_path = HERE / ("taste_synthesis.json" if domain == "places" else f"taste_synthesis.{domain}.json")
    if synth_path.exists():
        profile["taste_synthesis"] = json.loads(synth_path.read_text())
    return profile


def build_single_prompt(profile: dict, candidate: str, extra_context: str | None = None) -> dict:
    """Return {'system': ..., 'user': ..., 'user_prefix_len': int} for a
    single-venue verdict. user_prefix_len marks where the stable, identical-
    across-calls profile block ends and the per-candidate content begins —
    callers use it to mark the prefix cacheable (Anthropic prompt caching)."""
    schema = SINGLE_SCHEMA.replace("restaurant|cafe|matcha|bar|shop|other",
                                   profile.get("domain", {}).get("candidate_types", "restaurant|cafe|matcha|bar|shop|other"))
    system = _system_preamble(profile) + f"\n\nOutput STRICT JSON only, no prose outside the JSON. Schema:\n{schema}"
    prefix = "\n".join([
        "TASTE PROFILE (from the user's rated places):",
        "```json",
        json.dumps(_compact_profile(profile), indent=2, ensure_ascii=False),
        "```",
        "",
    ])
    suffix_parts = [f"CANDIDATE: {candidate}"]
    if extra_context:
        suffix_parts.append(f"EXTRA CONTEXT: {extra_context}")
    suffix_parts += ["", "Return the JSON verdict now."]
    suffix = "\n".join(suffix_parts)
    return {"system": system, "user": prefix + suffix, "user_prefix_len": len(prefix)}


def build_batch_prompt(profile: dict, candidates: list[dict]) -> dict:
    """Return {'system': ..., 'user': ..., 'user_prefix_len': int} for a
    batch of candidates. Each candidate: {'name': str, 'context': str
    (optional)}. user_prefix_len marks the stable profile block for
    Anthropic prompt caching, same contract as build_single_prompt."""
    system = _system_preamble(profile) + f"\n\nYou will receive N candidates. Score ALL of them, preserving order. Output STRICT JSON only:\n{BATCH_SCHEMA}"
    prefix = "\n".join([
        "TASTE PROFILE:",
        "```json",
        json.dumps(_compact_profile(profile), indent=2, ensure_ascii=False),
        "```",
        "",
    ])
    suffix = "\n".join([
        f"CANDIDATES (score ALL {len(candidates)}, preserve order):",
        "```json",
        json.dumps([{"name": c["name"], "context": c.get("context", "")} for c in candidates], indent=2, ensure_ascii=False),
        "```",
        "",
        "Return the {\"verdicts\": [...]} JSON now.",
    ])
    return {"system": system, "user": prefix + suffix, "user_prefix_len": len(prefix)}


# ---- Response parsing ---------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fence(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    if not text.startswith(("{", "[")):
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start >= 0:
            return text[start:].strip()
    return text


def parse_verdict(raw: str) -> dict:
    """Parse a single-verdict raw LLM response into a validated dict."""
    data = json.loads(_strip_fence(raw))
    _validate_verdict(data)
    return data


def parse_batch(raw: str) -> list[dict]:
    """Parse a batch response into a list of validated verdicts."""
    data = json.loads(_strip_fence(raw))
    verdicts = data.get("verdicts") if isinstance(data, dict) else data
    if not isinstance(verdicts, list):
        raise ValueError("expected an array of verdicts")
    for v in verdicts:
        _validate_verdict(v)
    return verdicts


def _validate_verdict(v: Any) -> None:
    if not isinstance(v, dict):
        raise ValueError(f"verdict must be object, got {type(v).__name__}")
    required = {"candidate", "weighted_score", "verdict", "dimensions", "one_liner", "confidence"}
    missing = required - v.keys()
    if missing:
        raise ValueError(f"verdict missing keys: {sorted(missing)}")
    if not isinstance(v["weighted_score"], int) or not 1 <= v["weighted_score"] <= 7:
        raise ValueError(f"weighted_score must be int 1-7, got {v['weighted_score']!r}")
    v["verdict"] = VERDICT_FROM_SCORE[v["weighted_score"]]
    validate_evidence_consistency(v)
