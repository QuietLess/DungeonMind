"""ClaudeLLM adapter, tested offline with a fake Anthropic client."""

import json
from types import SimpleNamespace

import pytest

from dungeonmind.graph.llm import ClaudeLLM
from dungeonmind.graph.state import AgentTurn, FinalAnswer, Intent, RouteDecision, ToolCall
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.schemas import Chunk, EntityType

BETA = "server-side-fallback-2026-07-01"


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, *responses):
        self.beta = SimpleNamespace(messages=FakeMessages(responses))

    @property
    def calls(self) -> list[dict]:
        return self.beta.messages.calls


def text(t: str):
    return SimpleNamespace(type="text", text=t)


def thinking(t: str = "pondering"):
    return SimpleNamespace(type="thinking", thinking=t, signature="sig-abc")


def tool_use(call_id: str, name: str, args: dict):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(content, stop_reason="end_turn", input_tokens=11, output_tokens=7):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model="claude-opus-5-5",
    )


def answer_json(answer="Yes [E1].", citations=("E1",), confidence="high", insufficient=False):
    return json.dumps(
        {
            "answer": answer,
            "citations": list(citations),
            "confidence": confidence,
            "insufficient_evidence": insufficient,
        }
    )


def route_json(intent="rules_qa", other_edition=False):
    return json.dumps({"intent": intent, "reason": "because", "other_edition": other_edition})


def evidence(n: int) -> list[Evidence]:
    return [
        Evidence(
            chunk=Chunk(
                chunk_id=f"srd521:test/{i}",
                chapter="Rules Glossary",
                heading_path=["Rules Glossary", f"Rule {i}"],
                entity_type=EntityType.GLOSSARY,
                page_start=100 + i,
                page_end=100 + i,
                text=f"Rule text number {i}.",
            ),
            score=1.0,
            rank=i,
        )
        for i in range(1, n + 1)
    ]


TOOLS = [
    {
        "name": "probability",
        "description": "Odds of a d20 roll.",
        "input_schema": {"type": "object", "properties": {}},
    }
]
MESSAGES = [{"role": "user", "content": "Odds to hit AC 15 with +5?"}]


def _all_calls_once():
    """One client that has served route, answer and agent_step; returns its recorded calls."""
    client = FakeClient(
        response([text(route_json())]),
        response([text(answer_json())]),
        response([text(answer_json(citations=[], answer="ok"))]),
    )
    llm = ClaudeLLM(client=client)
    llm.route("How does grappling work?")
    llm.answer("How does grappling work?", evidence(2))
    llm.agent_step(MESSAGES, TOOLS)
    return client.calls


# --- Request shape rules (apply to every call) ---


def test_every_request_uses_server_side_fallback():
    calls = _all_calls_once()
    assert len(calls) == 3
    for call in calls:
        assert call["betas"] == [BETA]
        assert call["fallbacks"] == "default"


def test_no_request_disables_thinking_or_sets_budget():
    for call in _all_calls_once():
        thinking_cfg = call.get("thinking")
        if thinking_cfg is not None:
            assert thinking_cfg.get("type") != "disabled"
            assert "budget_tokens" not in thinking_cfg
        assert "budget_tokens" not in json.dumps(call, default=str)


def test_no_request_forces_tool_choice():
    for call in _all_calls_once():
        choice = call.get("tool_choice")
        if choice is not None:
            assert choice.get("type") not in {"any", "tool"}


def test_every_request_has_effort_and_json_schema_format():
    for call in _all_calls_once():
        cfg = call["output_config"]
        assert cfg["effort"] in {"low", "medium", "high", "xhigh", "max"}
        assert cfg["format"]["type"] == "json_schema"
        assert isinstance(cfg["format"]["schema"], dict)
        assert call["model"] == "claude-opus-5-5"
        assert call["max_tokens"] > 0


def test_model_and_effort_are_configurable():
    client = FakeClient(response([text(answer_json())]))
    ClaudeLLM(client=client, model="claude-other", effort="high").answer("q?", evidence(1))
    assert client.calls[0]["model"] == "claude-other"
    assert client.calls[0]["output_config"]["effort"] == "high"


# --- route ---


def test_route_uses_low_effort_and_sends_question():
    client = FakeClient(response([text(route_json())]))
    ClaudeLLM(client=client, effort="high").route("Can I grapple a ghost?")
    call = client.calls[0]
    assert call["output_config"]["effort"] == "low"
    assert call["messages"] == [{"role": "user", "content": "Can I grapple a ghost?"}]
    assert "tools" not in call


def test_route_schema_enumerates_every_intent():
    client = FakeClient(response([text(route_json())]))
    ClaudeLLM(client=client).route("q")
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert set(schema["properties"]["intent"]["enum"]) == {i.value for i in Intent}


@pytest.mark.parametrize("intent", list(Intent))
def test_route_parses_every_intent(intent):
    client = FakeClient(response([text(route_json(intent.value))]))
    decision = ClaudeLLM(client=client).route("q")
    assert isinstance(decision, RouteDecision)
    assert decision.intent is intent
    assert decision.reason == "because"
    assert decision.other_edition is False


def test_route_parses_other_edition():
    client = FakeClient(response([thinking(), text(route_json("rules_qa", other_edition=True))]))
    decision = ClaudeLLM(client=client).route("How did grappling work in 2014?")
    assert decision.other_edition is True


def test_route_rejects_unknown_intent():
    client = FakeClient(response([text(route_json("weather"))]))
    with pytest.raises(ValueError):
        ClaudeLLM(client=client).route("q")


# --- answer ---


def test_answer_numbers_evidence_in_list_order_in_user_message():
    client = FakeClient(response([text(answer_json())]))
    ev = evidence(3)
    ClaudeLLM(client=client).answer("How does grappling work?", list(reversed(ev)))
    messages = client.calls[0]["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    content = messages[0]["content"]
    # Reversed list: E1 is rule 3, E3 is rule 1.
    pos = [content.index(f'id="E{i}"') for i in (1, 2, 3)]
    assert pos == sorted(pos)
    for eid, rule in (("E1", 3), ("E2", 2), ("E3", 1)):
        block = content.split(f'id="{eid}"', 1)[1].split("</evidence>", 1)[0]
        assert f"Rule text number {rule}." in block
    assert 'id="E4"' not in content
    assert "How does grappling work?" in content
    assert "tools" not in client.calls[0]


def test_answer_returns_final_answer():
    client = FakeClient(
        response([thinking(), text(answer_json("Yes [E2].", ["E2"], "medium", False))])
    )
    result = ClaudeLLM(client=client).answer("q?", evidence(2))
    assert result == FinalAnswer(
        answer="Yes [E2].", citations=["E2"], confidence="medium", insufficient_evidence=False
    )


def test_answer_does_not_validate_citations_itself():
    # Citation checking belongs to the graph's verify node, not the adapter.
    client = FakeClient(response([text(answer_json("Yes [E9].", ["E9"]))]))
    result = ClaudeLLM(client=client).answer("q?", evidence(1))
    assert result.citations == ["E9"]


# --- agent_step ---


def test_agent_step_passes_tools_and_messages():
    client = FakeClient(response([text(answer_json(citations=[]))]))
    ClaudeLLM(client=client).agent_step(MESSAGES, TOOLS)
    call = client.calls[0]
    assert call["tools"] == TOOLS
    assert call["messages"] == MESSAGES
    assert call["output_config"]["effort"] == "medium"


def test_agent_step_tool_use_returns_calls_and_raw_content_unchanged():
    content = [
        thinking("which tool?"),
        text("Let me check."),
        tool_use("toolu_1", "probability", {"modifier": 5, "dc": 15}),
        tool_use("toolu_2", "dice_roll", {"expression": "2d6"}),
    ]
    client = FakeClient(response(content, stop_reason="tool_use", input_tokens=40))
    turn = ClaudeLLM(client=client).agent_step(MESSAGES, TOOLS)
    assert isinstance(turn, AgentTurn)
    assert turn.final is None
    assert turn.tool_calls == [
        ToolCall(call_id="toolu_1", name="probability", arguments={"modifier": 5, "dc": 15}),
        ToolCall(call_id="toolu_2", name="dice_roll", arguments={"expression": "2d6"}),
    ]
    assert turn.raw_content == content
    assert [b.type for b in turn.raw_content] == ["thinking", "text", "tool_use", "tool_use"]
    assert turn.raw_content[0].signature == "sig-abc"
    assert turn.usage == {"input_tokens": 40, "output_tokens": 7}


def test_agent_step_end_turn_returns_final_from_first_text_block():
    content = [
        thinking(),
        text(answer_json("75% [T1].", ["T1"], "high")),
        text(answer_json("ignored", [], "low", True)),
    ]
    client = FakeClient(response(content, input_tokens=100, output_tokens=50))
    turn = ClaudeLLM(client=client).agent_step(MESSAGES, TOOLS)
    assert turn.tool_calls == []
    assert turn.final == FinalAnswer(
        answer="75% [T1].", citations=["T1"], confidence="high", insufficient_evidence=False
    )
    assert turn.raw_content == content
    assert turn.usage == {"input_tokens": 100, "output_tokens": 50}


# --- unusable responses raise ---


def _call(method: str, llm: ClaudeLLM):
    if method == "route":
        return llm.route("q")
    if method == "answer":
        return llm.answer("q", evidence(1))
    return llm.agent_step(MESSAGES, TOOLS)


@pytest.mark.parametrize("method", ["route", "answer", "agent_step"])
@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_and_truncation_raise(method, stop_reason):
    body = route_json() if method == "route" else answer_json()
    client = FakeClient(response([text(body)], stop_reason=stop_reason))
    with pytest.raises((RuntimeError, ValueError)):
        _call(method, ClaudeLLM(client=client))


@pytest.mark.parametrize("method", ["route", "answer", "agent_step"])
def test_malformed_json_raises(method):
    client = FakeClient(response([text('{"answer": "unterminated')]))
    with pytest.raises((RuntimeError, ValueError)):
        _call(method, ClaudeLLM(client=client))


@pytest.mark.parametrize("method", ["route", "answer", "agent_step"])
def test_missing_text_block_raises(method):
    client = FakeClient(response([thinking()]))
    with pytest.raises((RuntimeError, ValueError)):
        _call(method, ClaudeLLM(client=client))


@pytest.mark.parametrize("method", ["answer", "agent_step"])
def test_json_violating_answer_schema_raises(method):
    bad = json.dumps({"answer": "x", "citations": [], "confidence": "certain"})
    client = FakeClient(response([text(bad)]))
    with pytest.raises((RuntimeError, ValueError)):
        _call(method, ClaudeLLM(client=client))


# --- prompt-injection guardrail in system prompts ---


def test_answer_system_prompt_treats_evidence_as_non_instructions():
    calls = _all_calls_once()
    system = calls[1]["system"].lower()
    assert "evidence" in system and "not instructions" in system


def test_agent_system_prompt_treats_tool_output_as_data():
    calls = _all_calls_once()
    system = calls[2]["system"].lower()
    assert "tool" in system and "data" in system and "not instructions" in system
