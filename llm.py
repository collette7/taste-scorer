"""Unified LLM provider layer — bring your own model, one function.

Every script calls llm.complete(system, user) and gets text back. Which
provider runs is decided by (in order):

  1. TASTE_PROVIDER env var: "anthropic" | "openai" | "gemini" | "ollama"
  2. Auto-detect from available keys:
       ANTHROPIC_API_KEY -> anthropic
       OPENAI_API_KEY    -> openai
       GEMINI_API_KEY    -> gemini
       OLLAMA_HOST set or localhost:11434 reachable -> ollama (no key needed)

Model override per provider via TASTE_MODEL (e.g. "gpt-4o-mini",
"gemini-2.0-flash", "llama3.1"). Defaults are cheap-and-capable per provider.

Only the Anthropic path uses an SDK (optional dep, already in the README);
OpenAI/Gemini/Ollama go through stdlib urllib so there are no new hard
dependencies. If nothing is configured, complete() raises ProviderError with
setup guidance — callers surface the BYO-model pipe pattern as the fallback.
"""
from __future__ import annotations

import _env  # noqa: F401 -- loads .env into os.environ before any env reads below

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "ollama": "llama3.1",
}


class ProviderError(RuntimeError):
    pass


def detect_provider() -> str | None:
    explicit = os.environ.get("TASTE_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in DEFAULT_MODELS:
            raise ProviderError(f"unknown TASTE_PROVIDER {explicit!r} (have: {sorted(DEFAULT_MODELS)})")
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OLLAMA_HOST"):
        return "ollama"
    return None


def _model_for(provider: str) -> str:
    return os.environ.get("TASTE_MODEL") or DEFAULT_MODELS[provider]


def _post_json(url: str, body: dict, headers: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        raise ProviderError(f"{url} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"{url} unreachable: {e.reason}") from e


def _complete_anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=_model_for("anthropic"), max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _complete_openai(system: str, user: str, max_tokens: int) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data = _post_json(
        f"{base}/chat/completions",
        {
            "model": _model_for("openai"),
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
    )
    return data["choices"][0]["message"]["content"].strip()


def _complete_gemini(system: str, user: str, max_tokens: int) -> str:
    model = _model_for("gemini")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def _complete_ollama(system: str, user: str, max_tokens: int) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    data = _post_json(
        f"{host}/api/chat",
        {
            "model": _model_for("ollama"),
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        {},
    )
    return data["message"]["content"].strip()


_COMPLETERS = {
    "anthropic": _complete_anthropic,
    "openai": _complete_openai,
    "gemini": _complete_gemini,
    "ollama": _complete_ollama,
}

NO_PROVIDER_HELP = """No LLM provider configured. Options (any ONE of these):
  ANTHROPIC_API_KEY=sk-ant-...   https://console.anthropic.com/settings/keys
  OPENAI_API_KEY=sk-...          https://platform.openai.com/api-keys
  GEMINI_API_KEY=...             https://aistudio.google.com/apikey
  OLLAMA_HOST=localhost:11434    local models, no key (https://ollama.com)
Put it in .env (see setup.py) or export it. Force a provider with
TASTE_PROVIDER=<name>; pick a model with TASTE_MODEL=<model>.

Or skip providers entirely with the BYO-model pipe pattern:
  --prompt > p.json   ->  run through ANY LLM  ->  --parse < raw.json"""


def complete(system: str, user: str, max_tokens: int = 8000) -> str:
    provider = detect_provider()
    if provider is None:
        raise ProviderError(NO_PROVIDER_HELP)
    return _COMPLETERS[provider](system, user, max_tokens)


def provider_status() -> str:
    provider = detect_provider()
    if provider is None:
        return "no provider configured"
    return f"{provider} ({_model_for(provider)})"
