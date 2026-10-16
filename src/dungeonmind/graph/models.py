"""Which model and effort each graph step uses, and what a call costs (M8 contract M1–M4)."""

from __future__ import annotations

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


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Claude API reference (cached 2026-10-06). Cache writes are 1.25x input (5-minute TTL).
PRICING: dict[str, Price] = {}

# Models that accept the server-side refusal fallback (M4); Haiku 5.5 has none.
FALLBACK_MODELS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StepModel:
    model: str
    effort: str


@dataclass
class ModelConfig:
    steps: dict[str, StepModel] = field(default_factory=dict)

    @classmethod
    def default(cls) -> ModelConfig:
        raise NotImplementedError

    @classmethod
    def parse(cls, spec: str | None) -> ModelConfig:
        raise NotImplementedError

    def for_step(self, step: str) -> StepModel:
        raise NotImplementedError

    def describe(self) -> dict[str, str]:
        raise NotImplementedError


def cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0,
         cache_write: int = 0) -> float:
    raise NotImplementedError
