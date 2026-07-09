"""Canonical locations for user-facing files.

Code lives in taste/, but everything a user touches or that gets generated
(config, .env, profiles, synthesis, caches, domain specs, sample data) lives
at the project root so the working directory stays the obvious single place
to look.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent

CONFIG_PATH = PROJECT_ROOT / "taste.config.json"
ENV_PATH = PROJECT_ROOT / ".env"
DOMAINS_DIR = PROJECT_ROOT / "domains"
SAMPLE_DATA = PROJECT_ROOT / "sample_data"


def profile_path(domain: str = "places") -> Path:
    name = "taste_profile.json" if domain == "places" else f"taste_profile.{domain}.json"
    return PROJECT_ROOT / name


def synthesis_path(domain: str = "places") -> Path:
    name = "taste_synthesis.json" if domain == "places" else f"taste_synthesis.{domain}.json"
    return PROJECT_ROOT / name


def cache_path(name: str) -> Path:
    return PROJECT_ROOT / name
