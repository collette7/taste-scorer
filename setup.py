#!/usr/bin/env python3
"""Interactive setup wizard. Walks you through configuring taste-scorer for
your own data: where your ratings live, what scale you rate on, where scored
output goes, and your API keys. Writes taste.config.json and .env (both
gitignored -- your answers never get committed).

Usage:
  python3 setup.py            # interactive wizard
  python3 setup.py --sample   # skip straight to the bundled sample dataset
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "taste.config.json"
ENV_PATH = HERE / ".env"


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def ask_choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    print(f"\n{prompt}")
    for key, label in options:
        marker = " (default)" if key == default else ""
        print(f"  [{key}] {label}{marker}")
    choice = input(f"> ").strip().lower() or default
    valid = {k for k, _ in options}
    while choice not in valid:
        choice = input(f"Please enter one of {sorted(valid)}: ").strip().lower() or default
    return choice


def ask_yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    val = input(f"{prompt} [{d}]: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def section(title: str) -> None:
    print(f"\n{'─' * 60}\n{title}\n{'─' * 60}")


def run_sample_setup() -> None:
    section("Sample dataset setup")
    print("Using the bundled sample data (sample_data/ratings.json) so you can")
    print("try the tool immediately, no ratings of your own required yet.\n")
    cfg = {
        "roots": [{"kind": "json", "path": str(HERE / "sample_data" / "ratings.json")}],
        "scale_max": 7,
        "gate": {"min_score": 6},
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print(f"Wrote {CONFIG_PATH.name}")
    print("\nTry it now:")
    print("  python3 build_profile.py")
    print('  python3 score.py "Blue Bottle Coffee" --prompt   # BYO-model, no key needed')
    write_env_prompt(skip_confirm=False)


def collect_roots() -> list[dict]:
    section("1. Where do your ratings live?")
    roots: list[dict] = []
    kind = ask_choice(
        "Pick a source",
        [("c", "CSV file"), ("j", "JSON file"), ("o", "Obsidian vault"), ("m", "Multiple sources")],
        default="c",
    )
    kinds = ["c", "j", "o"] if kind == "m" else [kind]
    for k in kinds:
        if k == "c":
            path = ask("Path to your CSV", "~/places.csv")
            roots.append({"kind": "csv", "path": path})
        elif k == "j":
            path = ask("Path to your JSON file", "~/ratings.json")
            roots.append({"kind": "json", "path": path})
        elif k == "o":
            vault = ask("Path to your Obsidian vault", "~/Documents/Obsidian Vault")
            refs = ask("Subfolder where place notes live", "07 References")
            roots.append({"kind": "obsidian", "vault": vault, "refs": refs})
    return roots


def collect_scale() -> int:
    section("2. What rating scale do you use?")
    scale = ask_choice(
        "Pick your scale (verdict thresholds adapt automatically)",
        [("5", "1-5 stars"), ("7", "1-7 (Steph Ango convention)"), ("10", "1-10"), ("c", "custom")],
        default="7",
    )
    if scale == "c":
        while True:
            raw = ask("Max value of your scale (min is always 1)", "7")
            if raw.isdigit() and int(raw) >= 2:
                return int(raw)
            print("Enter a whole number >= 2.")
    return int(scale)


def collect_gate(scale_max: int) -> int:
    section("3. Gate threshold")
    print(f"When you use `taste gate` or `taste intake --min-score N`, what score")
    print(f"(out of {scale_max}) counts as \"worth it\"?")
    default = max(2, round(scale_max * 0.85))
    while True:
        raw = ask(f"Minimum score to pass (1-{scale_max})", str(default))
        if raw.isdigit() and 1 <= int(raw) <= scale_max:
            return int(raw)
        print(f"Enter a whole number between 1 and {scale_max}.")


def collect_output() -> dict:
    section("4. Where should scored output go?")
    print("Ranked reports and (if you use --intake) per-place records.")
    out_dir = ask("Output directory", "./taste_notes")
    return {"TASTE_OUTPUT_DIR": out_dir}


def write_env_prompt(skip_confirm: bool = True) -> None:
    section("5. API keys")
    existing = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    print("The judge needs ONE LLM provider. Pick whichever you already have:")
    provider = ask_choice(
        "LLM provider",
        [
            ("a", "Anthropic (console.anthropic.com/settings/keys)"),
            ("o", "OpenAI (platform.openai.com/api-keys)"),
            ("g", "Google Gemini (aistudio.google.com/apikey)"),
            ("l", "Ollama -- local models, no key needed (ollama.com)"),
            ("s", "Skip -- I'll use the BYO-model pipe pattern (--prompt/--parse)"),
        ],
        default="a",
    )

    lines = ["# taste-scorer environment -- gitignored, never commit this file"]
    if provider == "a":
        key = ask("ANTHROPIC_API_KEY", existing.get("ANTHROPIC_API_KEY", "")) or existing.get("ANTHROPIC_API_KEY", "")
        if key:
            lines.append(f"ANTHROPIC_API_KEY={key}")
    elif provider == "o":
        key = ask("OPENAI_API_KEY", existing.get("OPENAI_API_KEY", "")) or existing.get("OPENAI_API_KEY", "")
        if key:
            lines.append(f"OPENAI_API_KEY={key}")
    elif provider == "g":
        key = ask("GEMINI_API_KEY", existing.get("GEMINI_API_KEY", "")) or existing.get("GEMINI_API_KEY", "")
        if key:
            lines.append(f"GEMINI_API_KEY={key}")
    elif provider == "l":
        host = ask("OLLAMA_HOST", existing.get("OLLAMA_HOST", "localhost:11434"))
        model = ask("Local model to use (TASTE_MODEL)", "llama3.1")
        lines.append(f"OLLAMA_HOST={host}")
        lines.append(f"TASTE_PROVIDER=ollama")
        if model:
            lines.append(f"TASTE_MODEL={model}")

    print("\nOptional enrichment keys -- without these, candidates score as")
    print("UNVERIFIED (the judge is told not to guess the category, it just")
    print("lowers confidence). Skip freely.")
    gmaps_key = ask("TASTE_GMAPS_KEY (Google Places, for places)", existing.get("TASTE_GMAPS_KEY", "")) or existing.get("TASTE_GMAPS_KEY", "")
    tmdb_key = ask("TASTE_TMDB_KEY (TMDB, for movies/shows)", existing.get("TASTE_TMDB_KEY", "")) or existing.get("TASTE_TMDB_KEY", "")
    if gmaps_key:
        lines.append(f"TASTE_GMAPS_KEY={gmaps_key}")
    if tmdb_key:
        lines.append(f"TASTE_TMDB_KEY={tmdb_key}")

    if len(lines) > 1:
        ENV_PATH.write_text("\n".join(lines) + "\n")
        print(f"\nWrote {ENV_PATH.name} -- loaded automatically by every script, no `source` needed.")
    else:
        print("\nNo keys entered -- skipping .env. Use --prompt/--parse, or run setup again later.")


def main() -> None:
    if "--sample" in sys.argv:
        run_sample_setup()
        return

    print("taste-scorer setup")
    print("Configures where your ratings live, your scale, and your API keys.")
    print(f"Writes {CONFIG_PATH.name} and .env, both gitignored.\n")

    if not ask_yesno("Ready to configure your own data? (choose no to try the bundled sample first)"):
        run_sample_setup()
        return

    roots = collect_roots()
    scale_max = collect_scale()
    min_score = collect_gate(scale_max)
    output_env = collect_output()

    cfg = {"roots": roots, "scale_max": scale_max, "gate": {"min_score": min_score}}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print(f"\nWrote {CONFIG_PATH.name}:")
    print(json.dumps(cfg, indent=2))

    write_env_prompt()

    if output_env.get("TASTE_OUTPUT_DIR") and output_env["TASTE_OUTPUT_DIR"] != "./taste_notes":
        with open(ENV_PATH, "a") as f:
            f.write(f"TASTE_OUTPUT_DIR={output_env['TASTE_OUTPUT_DIR']}\n")

    section("Done")
    print("Next steps:")
    print("  python3 build_profile.py     # build your taste profile")
    print('  python3 score.py "Some Place"')
    print("\nNo ANTHROPIC_API_KEY? Use the BYO-model pipe pattern instead:")
    print('  python3 score.py "Some Place" --prompt > p.json')
    print("  <run p.json through any LLM>")
    print("  cat raw.json | python3 score.py --parse")


if __name__ == "__main__":
    main()
