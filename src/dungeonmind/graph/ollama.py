"""Local open-source models through Ollama (M8b contract P1–P7).

`OllamaLLM` subclasses `ClaudeLLM` and replaces only the request: every prompt, schema and
parser is shared, so the graph, citations and visibility rules behave the same on a free local
model as on Claude. Responses are adapted to the shape `ClaudeLLM` already parses.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx

from dungeonmind.graph.llm import ClaudeLLM
from dungeonmind.graph.models import DEFAULT_LOCAL_MODEL, ModelConfig
from dungeonmind.rag.answer import INLINE_CITATION, MAX_TOKENS

DEFAULT_URL = "http://127.0.0.1:11434"
NUM_CTX = 8192  # DungeonMind's prompts are ~1–6k tokens; Ollama's default context truncates
LOCAL_TIMEOUT_S = 300.0  # CPU inference is slow (P6)
_CANONICAL = ("answer", "citations", "confidence", "insufficient_evidence")

__all__ = [
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_URL",
    "NUM_CTX",
    "OllamaError",
    "OllamaLLM",
    "OllamaTimeout",
    "check_ollama",
]


class OllamaError(RuntimeError):
    pass


class OllamaTimeout(OllamaError):
    """The local model didn't answer in time (common on CPU-only machines)."""


def ollama_url() -> str:
    return os.environ.get("DUNGEONMIND_OLLAMA_URL", DEFAULT_URL).rstrip("/")


def _get(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _to_ollama(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-format turns (incl. tool_use / tool_result blocks) → Ollama chat messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    names: dict[str, str] = {}  # tool_use id → tool name, for the tool messages
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        texts, calls = [], []
        for block in content:
            kind = _get(block, "type")
            if kind == "text":
                texts.append(_get(block, "text", ""))
            elif kind == "tool_use":
                names[_get(block, "id")] = _get(block, "name")
                calls.append(
                    {
                        "function": {
                            "name": _get(block, "name"),
                            "arguments": _get(block, "input", {}),
                        }
                    }
                )
            elif kind == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_name": names.get(_get(block, "tool_use_id"), ""),
                        "content": _get(block, "content", ""),
                    }
                )
        if m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif texts:
            out.append({"role": m["role"], "content": "\n".join(texts)})
    return out


def _tool(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": definition["name"],
            "description": definition.get("description", ""),
            "parameters": definition.get("input_schema", {"type": "object"}),
        },
    }


def _as_answer_json(text: str) -> str:
    """P4: a small model's plain-text final reply is kept as the answer, with its citations."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and all(k in data for k in _CANONICAL):
            return text
    except ValueError:
        pass
    cited: list[str] = []
    for m in INLINE_CITATION.finditer(text):
        cited += [i.strip() for i in m.group(1).split(",")]
    return json.dumps(
        {
            "answer": text.strip(),
            "citations": list(dict.fromkeys(cited)),
            "confidence": "low",
            "insufficient_evidence": False,
        }
    )


class OllamaLLM(ClaudeLLM):
    """The LLM port on a local Ollama server; same prompts and parsing as Claude."""

    def __init__(
        self,
        models: ModelConfig | None = None,
        base_url: str | None = None,
        http: httpx.Client | None = None,
        timeout: float = LOCAL_TIMEOUT_S,
    ) -> None:
        # Deliberately no super().__init__(): there is no Anthropic client in local mode.
        self.models = models or ModelConfig.default(provider="ollama")
        self.model_override = None
        self.effort_override = None
        self.usage_log: list[dict[str, Any]] = []
        self.timeout = timeout
        self.http = http or httpx.Client(base_url=base_url or ollama_url())
        self._calls = 0

    def _create(
        self,
        step: str,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = MAX_TOKENS,
        tools: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> Any:
        model = self.models.for_step(step).model
        body: dict[str, Any] = {
            "model": model,
            "messages": _to_ollama(system, messages),
            "stream": False,
            "think": False,
            "options": {"num_ctx": NUM_CTX, "num_predict": min(max_tokens, 4096)},
        }
        if tools:
            body["tools"] = [_tool(t) for t in tools]
        else:
            body["format"] = schema  # structured output for every non-tool step
        started = time.perf_counter()
        data = self._post(body, timeout or self.timeout)
        response = self._adapt(data, model, bool(tools))
        self._record(step, model, response, (time.perf_counter() - started) * 1000)
        return response

    def _post(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            r = self.http.post("/api/chat", json=body, timeout=timeout)
            if r.status_code == 400 and "think" in r.text and body.get("think") is not None:
                body = {k: v for k, v in body.items() if k != "think"}  # older servers/models
                r = self.http.post("/api/chat", json=body, timeout=timeout)
        except httpx.TimeoutException as e:
            raise OllamaTimeout(f"the local model didn't answer within {timeout:.0f} s") from e
        except httpx.HTTPError as e:
            raise OllamaError(
                f"Ollama isn't reachable ({type(e).__name__}); is `ollama serve` running?"
            ) from e
        if r.status_code != 200:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text
            raise OllamaError(f"Ollama {r.status_code}: {str(detail)[:300]}")
        return r.json()

    def _adapt(self, data: dict[str, Any], model: str, with_tools: bool) -> Any:
        """An Ollama reply in the shape ClaudeLLM parses (stop_reason, content blocks, usage)."""
        message = data.get("message") or {}
        usage = SimpleNamespace(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        calls = message.get("tool_calls") or []
        if with_tools and calls:
            blocks = []
            for call in calls:
                fn = call.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                self._calls += 1
                blocks.append(
                    SimpleNamespace(
                        type="tool_use",
                        id=f"call_{self._calls}",
                        name=fn.get("name", ""),
                        input=args or {},
                    )
                )
            return SimpleNamespace(stop_reason="tool_use", content=blocks, usage=usage, model=model)
        text = message.get("content", "") or ""
        if with_tools:
            text = _as_answer_json(text)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=usage,
            model=model,
        )


def _normalize(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def check_ollama(
    base_url: str | None, models: ModelConfig, http: httpx.Client | None = None
) -> list[str]:
    """P5: problems that would stop a local run, each with the command that fixes it."""
    url = (base_url or ollama_url()).rstrip("/")
    client = http or httpx.Client(base_url=url)
    try:
        r = client.get("/api/tags", timeout=5.0)
        r.raise_for_status()
        pulled = {_normalize(m.get("name", "")) for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return [
            f"Ollama isn't running at {url}. Install it from https://ollama.com, then start "
            "it with `ollama serve`."
        ]
    wanted = sorted({m.model for m in models.steps.values()})
    return [
        f"Model {m} isn't downloaded yet: run `ollama pull {m}`."
        for m in wanted
        if _normalize(m) not in pulled
    ]
