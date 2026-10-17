"""Pick the LLM backend: paid Claude or a free local Ollama model (M8b contract P1). Stub."""

from __future__ import annotations

PROVIDERS = ("claude", "ollama")


def make_llm(provider: str, models):
    raise NotImplementedError


def provider_timeout(provider: str) -> float:
    raise NotImplementedError
