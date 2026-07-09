"""taste-scorer: a personal taste model.

Judges new candidates (places, films, shows) against things you've actually
rated, using any LLM. Public API for embedding in your own bot:

    from taste import build_single_prompt, load_profile, parse_verdict
    prompt  = build_single_prompt(load_profile(), "Some Place")
    verdict = parse_verdict(your_llm(system=prompt["system"], user=prompt["user"]))
"""
from taste.rubric import (
    build_batch_prompt,
    build_single_prompt,
    load_profile,
    parse_batch,
    parse_verdict,
)

__all__ = [
    "build_single_prompt",
    "build_batch_prompt",
    "load_profile",
    "parse_verdict",
    "parse_batch",
]
