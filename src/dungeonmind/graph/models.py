"""Which model and effort each graph step uses, and what a call costs (M8 contract M1–M4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STEPS = (
    "route",
    "contextualize",
    "rules_answer",
    "agent",
    "extract_encounter",
    "present_encounter",
    "campaign",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_LOCAL_MODEL = "qwen3:4b"  # Ollama; fits in 8 GB alongside the app (M8b contract P2)
LOCAL_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,100}")
LOW_EFFORT_STEPS = frozenset({"route", "contextualize", "extract_encounter"})


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


def _price(input_: float, output: float, cache_read: float) -> Price:
    return Price(input_, output, cache_read, cache_write=round(input_ * 1.25, 4))


# Claude API reference (cached 2026-10-06). Cache writes are 1.25x input (5-minute TTL).
# Haiku 5.5 prices are for prompts up to 100K tokens; DungeonMind's prompts stay far below.
PRICING: dict[str, Price] = {
    "claude-opus-5-5": _price(4.00, 20.00, 0.20),
    "claude-sonnet-5-5": _price(2.00, 10.00, 0.20),
    "claude-haiku-5-5": _price(0.10, 0.50, 0.01),
}

# Models that accept the server-side refusal fallback (M4); Haiku 5.5 has none.
FALLBACK_MODELS: frozenset[str] = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})


@dataclass(frozen=True)
class StepModel:
    model: str
    effort: str


def _default_steps(model: str = DEFAULT_MODEL) -> dict[str, StepModel]:
    return {
        step: StepModel(model, "low" if step in LOW_EFFORT_STEPS else "medium") for step in STEPS
    }


@dataclass
class ModelConfig:
    steps: dict[str, StepModel] = field(default_factory=_default_steps)

    @classmethod
    def default(cls, provider: str = "claude") -> ModelConfig:
        return cls(_default_steps(DEFAULT_LOCAL_MODEL if provider == "ollama" else DEFAULT_MODEL))

    @classmethod
    def parse(cls, spec: str | None, provider: str = "claude") -> ModelConfig:
        """`step=model[:effort]` pairs separated by commas; `all=` sets every step.

        Claude mode accepts only priced Claude models; Ollama mode any local model name (P2).
        """
        config = cls.default(provider)
        for item in (spec or "").split(","):
            item = item.strip()
            if not item:
                continue
            step, sep, value = item.partition("=")
            step = step.strip()
            if not sep or not value.strip():
                raise ValueError(f"{item!r}: expected step=model[:effort]")
            model, effort = _split_model(value.strip(), provider)
            if provider == "ollama":
                if not LOCAL_MODEL_NAME.fullmatch(model):
                    raise ValueError(f"invalid Ollama model name {model!r}")
            elif model not in PRICING:
                raise ValueError(f"unknown model {model!r}; known: {', '.join(PRICING)}")
            if effort and effort not in EFFORTS:
                raise ValueError(f"unknown effort {effort!r}; known: {', '.join(EFFORTS)}")
            targets = STEPS if step == "all" else (step,)
            if step != "all" and step not in STEPS:
                raise ValueError(f"unknown step {step!r}; known: all, {', '.join(STEPS)}")
            for target in targets:
                current = config.steps[target]
                config.steps[target] = StepModel(model, effort or current.effort)
        return config

    def for_step(self, step: str) -> StepModel:
        return self.steps[step]

    def describe(self) -> dict[str, str]:
        return {step: f"{m.model}:{m.effort}" for step, m in self.steps.items()}


def _split_model(value: str, provider: str) -> tuple[str, str]:
    """`model[:effort]`. Ollama names contain ':' themselves (qwen3:4b), so there an effort is
    only split off when the last part is a known effort level."""
    head, sep, tail = value.rpartition(":")
    if provider == "ollama":
        return (head, tail) if sep and tail in EFFORTS else (value, "")
    model, _, effort = value.partition(":")
    return model, effort


def cost(
    model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float:
    """US dollars for one call."""
    price = PRICING.get(model)
    if price is None:
        raise ValueError(f"no price for model {model!r}")
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read * price.cache_read
        + cache_write * price.cache_write
    ) / 1_000_000
