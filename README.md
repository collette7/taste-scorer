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

## Quickstart — try it in 60 seconds, no setup

The repo ships with `sample_data/ratings.json` (20 fictional but coherent
ratings) so you can see real output before touching your own data:

```bash
python3 taste.py setup --sample     # writes taste.config.json pointed at sample_data/
python3 taste.py refresh      # Balanced rater (mean 5.2/7) ...
python3 taste.py score "Blue Bottle Coffee" --json
```

## Setup wizard — for your own data

```bash
python3 taste.py setup
```

Walks you through:
1. **Where your ratings live** — CSV, JSON, Obsidian vault, or a mix
2. **Your rating scale** — 1-5, 1-7 (default), 1-10, or custom; verdict
   thresholds (go/maybe/skip) adapt automatically
3. **Gate threshold** — what score counts as "worth it" for `taste gate`
4. **Where scored output goes** — `TASTE_OUTPUT_DIR` for ranked reports and
   (if you use `--intake`) per-place records
5. **API keys** — `ANTHROPIC_API_KEY` (required to run the judge directly),
   optionally `TASTE_GMAPS_KEY` / `TASTE_TMDB_KEY` for fact-checking
   candidates before judging. Writes a gitignored `.env`, loaded
   automatically by every script — no `source` needed, no key ever touches
   your shell history or gets committed.

No key yet? Skip that step — every script supports the BYO-model pipe
pattern (`--prompt` / `--parse`) with zero Anthropic dependency.

Prefer manual config? Copy `taste.config.example.json` → `taste.config.json`:

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
`review/notes/description/comment`, `tags`, `location/city`). Non-standard headers:

```json
{"kind": "csv", "path": "~/my.csv", "mapping": {"name": "Restaurant", "rating": "MyScore"}}
```

JSON works too — an array of `{"name": ..., "rating": ..., "review": ...}`.
Obsidian users get a vault backend (frontmatter-driven). Multiple roots merge;
later wins on name collisions.

**Letterboxd users**: your export works directly. Settings → Import & Export →
Export Your Data, then point a root at `ratings.csv` with `"scale_max": 5`:

```json
{"roots": [{"kind": "csv", "path": "~/letterboxd/ratings.csv"}], "scale_max": 5}
```

Half-star ratings (3.5, 4.5) round to the nearest integer. Use it with
`--domain movies` for film-specific judging dimensions.

**The single highest-leverage thing you can do**: write a short `review` field
on your extreme ratings explaining *why*. "Showy, tourist-trap, trying too hard"
on a 2 teaches the model your actual anti-signal — without it, the model can
only guess from tags. (`notes` also still works, as a legacy alias.)

## 2. Build your profile

```bash
python3 taste.py refresh
#   521 records | 96 rated | persona: GENEROUS rater (mean 6.0/7) ...
```

## 2b. Synthesize taste principles (optional, recommended)

```bash
python3 taste.py synthesize
```

Distills your `review` text into short per-category preference summaries —
**with place names deliberately stripped out** — so the judge learns
transferable principles ("craft + warmth earns top marks, scene-y polish
loses points") instead of pattern-matching on your specific past favorites.
Works the same regardless of which root backend you use. Re-run whenever you
add a batch of new reviews.

## 3. Score things

**With any configured provider** — the judge runs on whichever LLM you have.
Set ONE of these (in `.env` via `setup.py`, or exported):

| Env var | Provider | Default model |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | `claude-haiku-4-5` |
| `OPENAI_API_KEY` | OpenAI (or any compatible API via `OPENAI_BASE_URL`) | `gpt-4o-mini` |
| `GEMINI_API_KEY` | Google Gemini | `gemini-2.0-flash` |
| `OLLAMA_HOST` | Ollama — local models, no key at all | `llama3.1` |

Auto-detected in that order; force one with `TASTE_PROVIDER=<name>`, pick a
model with `TASTE_MODEL=<model>`. Only the Anthropic path needs an SDK —
OpenAI/Gemini/Ollama run on stdlib HTTP, zero extra installs.

```bash
python3 taste.py score "Some New Restaurant"
python3 taste.py score "Porto cafe A" "Porto cafe B"       # ranked
python3 taste.py score --domain movies "Perfect Days"
```

**With no provider at all** (the pipe pattern — works with literally any LLM,
including chat UIs):

```bash
python3 taste.py score "Some Place" --prompt > p.json      # {system, user} out
# run p.json through YOUR model; it returns the JSON verdict
cat verdict.json | python3 taste.py score --parse           # validate + pretty-print
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
python3 taste.py refresh --domain movies
python3 taste.py score --domain movies "Aftersun"
```

## Enrichment keys

Optional but strongly recommended (prevents genre hallucination):

- `TASTE_GMAPS_KEY` — Google Places (venue type, price level, rating, address)
- `TASTE_TMDB_KEY` — TMDB (year, genres, director, cast)

No key → candidates are marked UNVERIFIED in the prompt and the judge is told to
lower confidence rather than guess. Lookups are disk-cached.

## Batch + gate

```bash
python3 taste.py list mylist.md --city LA        # extract venues from markdown
                                                  # (tables/bullets/links), rank all
python3 taste.py clean places.csv --city Kyoto   # bulk CSV: filter, dedupe, batch-score
                                                  # -> one ranked go/maybe/skip report
python3 taste.py clean places.csv --city Kyoto --min-mentions 2 --limit 30
python3 taste.py clean places.csv --intake all   # + a per-place record for every verdict
python3 taste.py gate "Some Place" --min-score 6        # exit 0 = clears your bar, 1 = below
                                                  # (for bot notification branching)
```

Bulk mode is built for research dumps (thousands of scraped places): scoring
always writes ONE ranked report first (`--min-mentions` narrows scope for cost,
never as a quality filter — popularity isn't taste). `--intake` additionally
creates a record per verdict — go, maybe, skip, and avoid all get one, tagged
by verdict, so nothing is silently dropped. Obsidian users get full enriched
Place notes; everyone else gets plain markdown files in `TASTE_OUTPUT_DIR`
(default `./taste_notes/`) automatically, no vault required.

## Research + rescore (the confidence loop)

First-pass scores on thin data cap out mid-scale on purpose — the judge is
told to default to a middling score rather than invent confidence from a bare
name and star rating. The way up is evidence:

```bash
python3 taste.py research "Some Place" --notes "12-seat solo-roaster kissaten,
  owner roasts on a vintage Fuji Royal. https://www.instagram.com/someplace/"
                                                  # append findings + re-judge
python3 taste.py research "Some Place" --file findings.md --no-rescore
python3 taste.py rescore "Some Place"             # refresh facts, then re-judge
python3 taste.py rescore "Some Place" --no-refresh # use record contents only
python3 taste.py rescore "Some Place" --dry-run   # show the delta, write nothing
```

`research` appends your findings to the record under a dated heading, saves
the first Instagram link it sees as the venue's social link (`--social` to
set it explicitly), then re-judges with the findings as context and logs the
score delta on the record. `rescore` also fetches current provider details and
bypasses its disk cache before judging. Use `--no-refresh` for offline or
cost-sensitive runs. Past verdicts are stripped from the judge's context so it
cannot anchor on its own previous opinion. Both commands support the same
BYO-model flow (`--prompt` out, `--verdict-json` in) as `score`.

The parser rejects internally inconsistent verdicts before they reach your
records. Low-confidence results cannot score above 5. Neighborhood weight is
capped at 0.15. Scores of 6-7 require venue-specific product or execution
evidence. Each one-line recommendation must start with an action that matches
its score: `Destination`, `Route stop`, `Nearby-only`, or `Skip`. Direct LLM
mode retries one rejected response once with the rejection reason.

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

## Layout

Project root holds everything you touch; `taste/` holds everything you don't.
Generated files (profile, synthesis, caches, `taste_notes/`) land at root too.

```
taste-scorer/
├── taste.py                     CLI entry point — taste.py <command> [...]
├── setup.py                     interactive setup wizard
├── taste.config.example.json    copy to taste.config.json (or let setup write it)
├── domains/                     domain specs: places / movies / shows — add your own
├── sample_data/                 bundled demo ratings for `setup --sample`
└── taste/                       implementation
    ├── __init__.py              public API for embedding in your own bot
    ├── rubric.py                prompt builder + verdict parser
    ├── verdict_quality.py       evidence and score consistency checks
    ├── root.py                  rating backends: CSV / JSON / Obsidian
    ├── llm.py                   providers: Anthropic / OpenAI / Gemini / Ollama
    ├── build_profile.py         roots → profile (persona, stats, exemplars)
    ├── synthesize.py            reviews → taste principles (place names stripped)
    ├── score.py                 single-candidate judging
    ├── list_scorer.py           extract + score candidates from markdown
    ├── batch_intake.py          bulk CSV pipeline (filter/dedupe/score/report)
    ├── gate.py                  score-only, exit code = verdict (for bots)
    ├── enrich.py, enrich_tmdb.py  Places / TMDB fact resolution, cached
    ├── rescore*.py              evidence refresh, context, and persistence
    ├── freshness.py             auto-refresh stale profiles
    ├── paths.py                 canonical file locations
    └── _env.py                  zero-dependency .env loader
```

Embedding in your own bot:

```python
from taste import build_single_prompt, load_profile, parse_verdict
prompt  = build_single_prompt(load_profile(), "Some Place")
verdict = parse_verdict(your_llm(system=prompt["system"], user=prompt["user"]))
```

MIT. Built for personal use; PRs for new domains and root backends welcome.
