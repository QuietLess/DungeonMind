"""Local open-source models through Ollama (M8b contract P1–P7). Stub (RED)."""

from __future__ import annotations

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "qwen3:4b"
NUM_CTX = 8192
LOCAL_TIMEOUT_S = 300.0


class OllamaError(RuntimeError):
    pass


class OllamaLLM:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError


def check_ollama(base_url, models, http=None) -> list[str]:
    raise NotImplementedError
