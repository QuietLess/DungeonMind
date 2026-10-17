"""Pick the LLM backend: paid Claude or a free local Ollama model (M8b contract P1, P6)."""

from __future__ import annotations

PROVIDERS = ("claude", "ollama")
TIMEOUTS = {"claude": 60.0, "ollama": 300.0}


def check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; choose {' or '.join(PROVIDERS)}")
    return provider


def make_llm(provider: str, models):
    check_provider(provider)
    if provider == "ollama":
        from dungeonmind.graph.ollama import OllamaLLM

        return OllamaLLM(models=models)
    from dungeonmind.graph.llm import ClaudeLLM

    return ClaudeLLM(models=models)


def provider_timeout(provider: str) -> float:
    return TIMEOUTS[check_provider(provider)]
