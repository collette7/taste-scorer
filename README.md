# taste-scorer

A personal taste model. Predicts whether *you* will like a place, film, or show —
by judging candidates against things you've actually rated, using any LLM as the
judge.

Not a recommender system. It doesn't know what's popular. It knows what **you**
rated a 7 and what you rated a 2, reads *why* (your notes), and asks: is this new
thing shaped like your 7s or your 2s?

```
$ taste "Zest Seoul"

[GO] ZEST SEOUL  (bar)
  ★★★★★★☆  6/7  ·  medium-high confidence
  → Acclaimed craft without the show — Double Chicken Please-shaped.
  ≈ closest analog in your data: Double Chicken Please
```

## How it works

```
 YOUR RATING HISTORY ("roots")          NEW THING ("candidate")
 ┌─────────────────────────┐
 │ CSV / JSON / Obsidian   │            "Zest Seoul"
 └───────────┬─────────────┘                 │
             ▼                               ▼
      build_profile.py                 enrichment (Google
             │                         Places / TMDB) —
             ▼                         verified genre, price,
      taste_profile.json               rating, address
      ├─ persona (derived:                   │
      │  rater generosity,                   │
      │  loved/disliked tags,                │
      │  beloved + anti-signal               │
      │  exemplars WITH your                 │
      │  written reasons)                    │
      ├─ tag/genre/director stats            │
      └─ exemplars per rating                │
             │                               │
             └──────────┬────────────────────┘
                        ▼
              LLM-as-judge (any model)
              6 weighted dimensions,
              score 1-7 + weight 0-1 each
                        ▼
              weighted_score → verdict
              go / maybe / skip / avoid
              + closest analog + reasons
```

Three design decisions worth knowing:

1. **Persona is derived, not hardcoded.** Rater generosity, favorite tags,
   anti-signals — all computed from your data at build time. Ship the code to
   anyone; the model becomes *theirs* when they point it at their ratings.
2. **The judge never guesses what a thing is.** Candidates are resolved through
   Google Places / TMDB first and the verified facts go into the prompt. No
   hallucinated genres.
3. **Predictions never contaminate your ratings.** The model's score is stored
   separately (`taste`) from yours (`rating`). Once you rate something it
   predicted, you get calibration data for free.

## Install

```bash
git clone <this repo> && cd taste-scorer
# deps: python3.10+, pyyaml (only for the Obsidian backend), anthropic (only for direct API mode)
```

## 1. Point it at your ratings

Your **roots** = things you've already rated, on any 1-N scale. Copy
`taste.config.example.json` → `taste.config.json`:

```json
{
  "roots": [
    {"kind": "csv", "path": "~/my_ratings.csv"}
  ],
  "scale_max": 7,
  "gate": {"min_score": 6}
}
```

CSV columns are auto-detected (`name/title/place`, `rating/score/stars`,
`notes/description/comment`, `tags`, `location/city`). Non-standard headers:

```json
{"kind": "csv", "path": "~/my.csv", "mapping": {"name": "Restaurant", "rating": "MyScore"}}
```

JSON works too — an array of `{"name": ..., "rating": ..., "notes": ...}`.
Obsidian users get a vault backend (frontmatter-driven). Multiple roots merge;
later wins on name collisions.

**The single highest-leverage thing you can do**: write a short `notes` field
on your extreme ratings explaining *why*. "Showy, tourist-trap, trying too hard"
on a 2 teaches the model your actual anti-signal — without it, the model can
only guess from tags.

## 2. Build your profile

```bash
python3 build_profile.py
#   521 records | 96 rated | persona: GENEROUS rater (mean 6.0/7) ...
```

## 3. Score things

**With an Anthropic key** (`ANTHROPIC_API_KEY`):

```bash
python3 score.py "Some New Restaurant"
python3 score.py "Porto cafe A" "Porto cafe B"       # ranked
python3 score.py --domain movies "Perfect Days"
```

**With any other LLM** (the pipe pattern — nothing here requires Anthropic):

```bash
python3 score.py "Some Place" --prompt > p.json      # {system, user} out
# run p.json through YOUR model; it returns the JSON verdict
cat verdict.json | python3 score.py --parse           # validate + pretty-print
```

**Or in-process from your own bot** (three lines, stdlib-only module):

```python
from rubric import build_single_prompt, load_profile, parse_verdict
prompt = build_single_prompt(load_profile(), "Some Place")
verdict = parse_verdict(my_llm(system=prompt["system"], user=prompt["user"]))
```

## Domains

`domains/<name>.json` defines what a domain is: the judge question, six scoring
dimensions, candidate types, and which enricher to use. Included:

| Domain | Dimensions tuned for | Enricher |
|--------|---------------------|----------|
| `places` | product quality, atmosphere, neighborhood, design, analogs, anti-signal | Google Places |
| `movies` | craft, tone, genre stats, **auteur track record**, analogs, anti-signal | TMDB |
| `shows` | + **commitment ratio** (seasons vs payoff) | TMDB (tv) |

Add your own domain (books, restaurants-only, wine, whatever) by dropping a JSON
file — no code changes. Per-domain profiles keep personas separate: being a
generous rater about food doesn't leak into how the model reads your film scores.

```bash
python3 build_profile.py --domain movies
python3 score.py --domain movies "Aftersun"
```

## Enrichment keys

Optional but strongly recommended (prevents genre hallucination):

- `TASTE_GMAPS_KEY` — Google Places (venue type, price level, rating, address)
- `TASTE_TMDB_KEY` — TMDB (year, genres, director, cast)

No key → candidates are marked UNVERIFIED in the prompt and the judge is told to
lower confidence rather than guess. Lookups are disk-cached.

## Batch + gate

```bash
python3 list_scorer.py mylist.md --city LA        # extract venues from markdown
                                                  # (tables/bullets/links), rank all
python3 gate.py "Some Place" --min-score 6        # exit 0 = clears your bar, 1 = below
                                                  # (for bot notification branching)
```

## The scoring math (honest version)

The LLM scores each dimension 1-7 and assigns it a weight 0-1 (how much that
dimension matters *for this candidate*, given your profile). Then:

```
weighted_score = round( Σ(score·weight) / Σ(weight) )
verdict:  6-7 → go · 5 → maybe · 4 → skip · ≤3 → actively avoid
```

The weights are the LLM's judgment, not a learned regression — this is a
grounded-LLM-judge, not a statistical model. What keeps it honest: the persona is
empirical, enrichment is verified, the schema is validated, and once you rate
things it predicted, predicted-vs-actual tells you exactly how well it knows you.

## Files

| File | What |
|------|------|
| `rubric.py` | Prompt builder + verdict parser. Stdlib-only, import from anything |
| `root.py` | Rating-history backends: CSV / JSON / Obsidian |
| `build_profile.py` | roots → profile (persona, stats, exemplars) |
| `score.py` | Single-candidate CLI, `--domain`, `--prompt`/`--parse` |
| `list_scorer.py` | Extract candidates from markdown, batch-score, rank |
| `gate.py` | Score-only + exit-code branching |
| `enrich.py` / `enrich_tmdb.py` | Places / TMDB fact resolution, cached |
| `domains/*.json` | Domain specs (question, dimensions, enricher) |

MIT. Built for personal use; PRs for new domains and root backends welcome.
