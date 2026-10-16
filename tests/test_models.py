"""M8 per-step models, usage and cost (docs/ARCHITECTURE.md › M8 contract, M1–M4)."""

import json
from types import SimpleNamespace

import pytest

from dungeonmind.graph.llm import ClaudeLLM
from dungeonmind.graph.models import PRICING, STEPS, ModelConfig, cost


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(payload, input_tokens=1000, output_tokens=200, cache_read=0, cache_write=0):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


ROUTE = {"intent": "rules_qa", "reason": "r", "other_edition": False}
ANSWER = {"answer": "Yes [E1].", "citations": ["E1"], "confidence": "high",
          "insufficient_evidence": False}


def _client(*payloads, **usage):
    messages = FakeMessages([_response(p, **usage) for p in payloads])
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


# --- M1/M2 configuration ----------------------------------------------------------------------


def test_m1_default_is_opus_everywhere_with_low_effort_for_small_steps():
    config = ModelConfig.default()
    assert {config.for_step(s).model for s in STEPS} == {"claude-opus-5-5"}
    low = {s for s in STEPS if config.for_step(s).effort == "low"}
    assert low == {"route", "contextualize", "extract_encounter"}
    assert {config.for_step(s).effort for s in set(STEPS) - low} == {"medium"}


def test_m2_parse_overrides_steps_and_optional_effort():
    config = ModelConfig.parse("route=claude-haiku-5-5, agent=claude-sonnet-5-5:high")
    assert config.for_step("route").model == "claude-haiku-5-5"
    assert config.for_step("route").effort == "low"  # keeps the step's default effort
    assert (config.for_step("agent").model, config.for_step("agent").effort) == (
        "claude-sonnet-5-5", "high")
    assert config.for_step("campaign").model == "claude-opus-5-5"


def test_m2_all_shorthand_sets_every_step():
    config = ModelConfig.parse("all=claude-sonnet-5-5")
    assert {config.for_step(s).model for s in STEPS} == {"claude-sonnet-5-5"}


@pytest.mark.parametrize(
    "spec, word",
    [
        ("routing=claude-haiku-5-5", "routing"),
        ("route=gpt-5", "gpt-5"),
        ("route=claude-haiku-5-5:turbo", "turbo"),
        ("route", "route"),
    ],
)
def test_m2_bad_specs_name_the_problem(spec, word):
    with pytest.raises(ValueError, match=word):
        ModelConfig.parse(spec)


def test_m2_empty_spec_is_default():
    assert ModelConfig.parse("") == ModelConfig.default()
    assert ModelConfig.parse(None) == ModelConfig.default()


# --- M3 cost ----------------------------------------------------------------------------------


def test_m3_pricing_covers_the_current_models():
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"} <= set(PRICING)
    assert PRICING["claude-opus-5-5"].input == 4.0 and PRICING["claude-opus-5-5"].output == 20.0
    assert PRICING["claude-haiku-5-5"].output == 0.5


def test_m3_cost_counts_every_token_kind():
    # 1M in + 1M out + 1M cache read + 1M cache write on Opus 5.5 = 4 + 20 + 0.20 + 5
    assert cost("claude-opus-5-5", 10**6, 10**6, 10**6, 10**6) == pytest.approx(29.2)
    assert cost("claude-haiku-5-5", 2000, 1000) == pytest.approx(0.0007)


def test_m3_unknown_model_cost_is_an_error():
    with pytest.raises(ValueError, match="claude-unknown"):
        cost("claude-unknown", 1, 1)


# --- ClaudeLLM uses the config and records usage ----------------------------------------------


def test_m1_each_step_uses_its_configured_model_and_effort():
    client, messages = _client(ROUTE, ANSWER)
    config = ModelConfig.parse("route=claude-haiku-5-5,rules_answer=claude-sonnet-5-5:high")
    llm = ClaudeLLM(client=client, models=config)
    llm.route("q")
    from dungeonmind.rag.hybrid import Evidence  # noqa: F401  (evidence list may be empty)

    llm.answer("q", [])
    assert messages.calls[0]["model"] == "claude-haiku-5-5"
    assert messages.calls[0]["output_config"]["effort"] == "low"
    assert messages.calls[1]["model"] == "claude-sonnet-5-5"
    assert messages.calls[1]["output_config"]["effort"] == "high"


def test_m3_usage_log_records_step_model_tokens_latency_and_cost():
    client, _ = _client(ROUTE, input_tokens=1200, output_tokens=80, cache_read=300)
    llm = ClaudeLLM(client=client, models=ModelConfig.parse("route=claude-haiku-5-5"))
    llm.route("q")
    [entry] = llm.usage_log
    assert entry["step"] == "route" and entry["model"] == "claude-haiku-5-5"
    assert (entry["input_tokens"], entry["output_tokens"], entry["cache_read_tokens"]) == (
        1200, 80, 300)
    assert entry["cost"] == pytest.approx(cost("claude-haiku-5-5", 1200, 80, 300))
    assert entry["latency_ms"] >= 0


def test_m4_haiku_requests_omit_the_refusal_fallback():
    client, messages = _client(ROUTE, ROUTE)
    ClaudeLLM(client=client, models=ModelConfig.parse("route=claude-haiku-5-5")).route("q")
    ClaudeLLM(client=client).route("q")
    haiku, opus = messages.calls
    assert "fallbacks" not in haiku and "betas" not in haiku
    assert opus["fallbacks"] == "default"
    assert opus["betas"] == ["server-side-fallback-2026-07-01"]
