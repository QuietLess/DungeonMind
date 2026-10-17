"""M8b local models through Ollama (docs/ARCHITECTURE.md › M8b contract), against a fake server."""

import json

import httpx
import pytest

from dungeonmind.graph.models import ModelConfig
from dungeonmind.graph.ollama import (
    DEFAULT_LOCAL_MODEL,
    NUM_CTX,
    OllamaError,
    OllamaLLM,
    check_ollama,
)
from dungeonmind.graph.providers import make_llm, provider_timeout
from dungeonmind.graph.state import Intent

ROUTE = {"intent": "rules_qa", "reason": "a rule", "other_edition": False}
ANSWER = {
    "answer": "Yes [E1].",
    "citations": ["E1"],
    "confidence": "high",
    "insufficient_evidence": False,
}


class FakeOllama:
    """Records /api/chat requests and answers from a script."""

    def __init__(self, *replies, tags=("qwen3:4b",)):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.tags = tags

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": t} for t in self.tags]})
        body = json.loads(request.content)
        self.requests.append(body)
        reply = self.replies.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "message": reply,
                "done": True,
                "prompt_eval_count": 1200,
                "eval_count": 90,
            },
        )

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handler), base_url="http://ollama.test"
        )


def _llm(fake: FakeOllama, models=None) -> OllamaLLM:
    return OllamaLLM(models=models, http=fake.client())


def _json(payload) -> dict:
    return {"role": "assistant", "content": json.dumps(payload)}


# --- P2 models --------------------------------------------------------------------------------


def test_p2_ollama_defaults_every_step_to_the_local_model():
    config = ModelConfig.default(provider="ollama")
    assert {m.model for m in config.steps.values()} == {DEFAULT_LOCAL_MODEL}


def test_p2_any_ollama_model_name_is_accepted_but_claude_mode_stays_strict():
    config = ModelConfig.parse("all=llama3.2:3b,route=qwen3:1.7b", provider="ollama")
    assert config.for_step("route").model == "qwen3:1.7b"
    assert config.for_step("campaign").model == "llama3.2:3b"
    with pytest.raises(ValueError):
        ModelConfig.parse("route=llama3.2:3b")  # claude mode
    with pytest.raises(ValueError):
        ModelConfig.parse("route=bad name!", provider="ollama")


# --- P3 requests ------------------------------------------------------------------------------


def test_p3_route_sends_schema_context_and_no_thinking_then_parses():
    fake = FakeOllama(_json(ROUTE))
    decision = _llm(fake).route("How does grappling work?")
    assert decision.intent is Intent.RULES_QA
    [req] = fake.requests
    assert req["model"] == DEFAULT_LOCAL_MODEL
    assert req["stream"] is False and req["think"] is False
    assert req["options"]["num_ctx"] == NUM_CTX
    assert req["format"]["properties"]["intent"]["enum"]
    assert req["messages"][0]["role"] == "system"
    assert req["messages"][-1] == {"role": "user", "content": "How does grappling work?"}


def test_p3_each_step_uses_its_configured_local_model():
    fake = FakeOllama(_json(ROUTE), _json(ANSWER))
    llm = _llm(fake, ModelConfig.parse("route=qwen3:1.7b", provider="ollama"))
    llm.route("q")
    llm.answer("q", [])
    assert [r["model"] for r in fake.requests] == ["qwen3:1.7b", DEFAULT_LOCAL_MODEL]


def test_p3_campaign_answers_go_through_the_same_prompts():
    from dungeonmind.graph.state import NoteHit

    fake = FakeOllama(_json({**ANSWER, "answer": "Spy [N1].", "citations": ["N1"]}))
    notes = [NoteHit(entry_id=1, source="Lore › NPC › Elara", text="Elara is a spy.", canon=True)]
    out = _llm(fake).answer_campaign("Who is Elara?", notes, [], mode="memory")
    assert out.citations == ["N1"]
    content = fake.requests[0]["messages"][-1]["content"]
    assert '<note id="N1"' in content and "canon" in fake.requests[0]["messages"][0]["content"]


# --- P4 tools ---------------------------------------------------------------------------------

TOOLS = [
    {
        "name": "probability",
        "description": "Odds of a d20 roll.",
        "input_schema": {"type": "object", "properties": {"bonus": {"type": "integer"}}},
    }
]


def test_p4_tool_calls_round_trip_in_ollama_format():
    fake = FakeOllama(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "probability", "arguments": {"bonus": 5}}}],
        },
        _json({**ANSWER, "answer": "55% [T1].", "citations": ["T1"]}),
    )
    llm = _llm(fake)
    messages = [{"role": "user", "content": "Odds?"}]
    turn = llm.agent_step(messages, TOOLS)
    [call] = turn.tool_calls
    assert call.name == "probability" and call.arguments == {"bonus": 5} and call.call_id
    first = fake.requests[0]
    assert first["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "probability",
                "description": "Odds of a d20 roll.",
                "parameters": TOOLS[0]["input_schema"],
            },
        }
    ]

    # The graph appends the assistant turn and one user message with the tool results.
    messages += [
        {"role": "assistant", "content": turn.raw_content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.call_id,
                    "content": '{"id": "T1", "result": {"hit": 0.55}}',
                    "is_error": False,
                }
            ],
        },
    ]
    final = llm.agent_step(messages, TOOLS)
    assert final.final.answer == "55% [T1]."
    sent = fake.requests[1]["messages"]
    assert sent[-2]["role"] == "assistant"
    assert sent[-2]["tool_calls"][0]["function"]["name"] == "probability"
    assert sent[-1] == {
        "role": "tool",
        "tool_name": "probability",
        "content": '{"id": "T1", "result": {"hit": 0.55}}',
    }


def test_p4_a_plain_text_final_reply_is_kept_with_its_citations():
    fake = FakeOllama({"role": "assistant", "content": "You hit 55% of the time [T1]."})
    turn = _llm(fake).agent_step([{"role": "user", "content": "Odds?"}], TOOLS)
    assert turn.final.answer == "You hit 55% of the time [T1]."
    assert turn.final.citations == ["T1"]
    assert turn.final.confidence == "low"


# --- P5 errors --------------------------------------------------------------------------------


def test_p5_server_errors_raise_for_the_graph_to_handle():
    fake = FakeOllama(httpx.Response(404, json={"error": "model 'qwen3:4b' not found"}))
    with pytest.raises(OllamaError, match="not found"):
        _llm(fake).route("q")


def test_p5_check_reports_a_missing_model_and_a_stopped_server():
    fake = FakeOllama(tags=("llama3.2:3b",))
    problems = check_ollama(
        "http://ollama.test", ModelConfig.default(provider="ollama"), http=fake.client()
    )
    assert any("ollama pull qwen3:4b" in p for p in problems)

    def refuse(request):
        raise httpx.ConnectError("refused")

    down = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://ollama.test")
    problems = check_ollama("http://ollama.test", ModelConfig.default(provider="ollama"), http=down)
    assert any("ollama serve" in p for p in problems)


def test_p5_check_passes_when_the_models_are_pulled():
    fake = FakeOllama(tags=("qwen3:4b",))
    assert (
        check_ollama(
            "http://ollama.test", ModelConfig.default(provider="ollama"), http=fake.client()
        )
        == []
    )


# --- P6/P7 timeouts, usage, providers ---------------------------------------------------------


def test_p7_usage_is_logged_at_zero_cost():
    fake = FakeOllama(_json(ROUTE))
    llm = _llm(fake)
    llm.route("q")
    [entry] = llm.usage_log
    assert (entry["input_tokens"], entry["output_tokens"], entry["cost"]) == (1200, 90, 0.0)
    assert entry["model"] == DEFAULT_LOCAL_MODEL


def test_p1_p6_make_llm_and_timeouts():
    assert isinstance(make_llm("ollama", ModelConfig.default(provider="ollama")), OllamaLLM)
    assert provider_timeout("ollama") == 300.0 and provider_timeout("claude") == 60.0
    with pytest.raises(ValueError, match="gemini"):
        make_llm("gemini", ModelConfig.default())


def test_p7_estimate_is_free_for_local_models():
    from dungeonmind.evals.live import estimate
    from dungeonmind.evals.suite import Case

    cases = [Case(id="r-1", category="rules", question="q", expect={})]
    assert estimate(cases, ModelConfig.default(provider="ollama")) == 0.0


def test_p5_a_slow_local_model_raises_a_named_timeout():
    from dungeonmind.graph.ollama import OllamaTimeout

    def slow(request):
        raise httpx.ReadTimeout("timed out")

    llm = OllamaLLM(
        http=httpx.Client(transport=httpx.MockTransport(slow), base_url="http://ollama.test")
    )
    with pytest.raises(OllamaTimeout, match="300"):
        llm.route("q")
