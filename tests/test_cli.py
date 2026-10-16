"""`dungeonmind chat` CLI: credentials message, max-steps cap, terminal sanitising (M4 hardening).

Fully offline: the retriever, catalog, LLM adapter, Deps and run_chat are monkeypatched, so no
index, network or API key is needed.
"""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import dungeonmind.cli as cli
from dungeonmind.graph.state import Intent

NO_CREDS = "No valid Anthropic credentials"
WIDE = {"COLUMNS": "200", "TERMINAL_WIDTH": "200"}


def _state(**overrides) -> dict:
    state = {
        "question": "q",
        "intent": Intent.RULES_QA,
        "route_reason": "because",
        "answer": "You can grapple [E1].",
        "citations": [{"id": "E1", "kind": "srd", "label": "SRD 5.2.1, Grappling, p. 1"}],
        "confidence": "high",
        "insufficient_evidence": False,
        "violations": [],
        "stop_reason": "completed",
        "error_type": None,
        "tool_calls": [],
        "trace": [{"node": "route", "detail": "rules_qa", "ms": 1.0}],
    }
    state.update(overrides)
    return state


@pytest.fixture
def chat_env(monkeypatch):
    """Patch everything `chat` builds; return a recorder of run_chat calls and the next state."""
    rec = SimpleNamespace(calls=[], state=_state())

    def fake_run_chat(question, deps, *args, **kwargs):
        rec.calls.append({"question": question, "deps": deps})
        return rec.state

    retriever = SimpleNamespace(by_id={}, retrieve=lambda *a, **k: [])
    monkeypatch.setattr(cli, "load_retriever", lambda *a, **k: retriever)
    monkeypatch.setattr("dungeonmind.graph.build.run_chat", fake_run_chat)
    monkeypatch.setattr("dungeonmind.graph.build.Deps", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr("dungeonmind.graph.llm.ClaudeLLM", lambda *a, **k: object())
    monkeypatch.setattr(
        "dungeonmind.tools.catalog.Catalog.load", classmethod(lambda c, *a, **k: object())
    )
    monkeypatch.setattr("dungeonmind.tools.registry.ToolRegistry", lambda *a, **k: object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-placeholder")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return rec


def _invoke(*args: str):
    return CliRunner().invoke(cli.app, ["chat", *args], env=WIDE)


def _flat(output: str) -> str:
    return " ".join(output.split())


def test_happy_path_prints_answer(chat_env):
    result = _invoke("Can I grapple?")
    assert result.exit_code == 0, result.output
    assert "You can grapple [E1]." in result.output
    assert NO_CREDS not in _flat(result.output)


# --- E1 credentials message -----------------------------------------------------------------


def test_e1_missing_credentials_are_reported_before_running(chat_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    result = _invoke("Can I grapple?")
    assert NO_CREDS in _flat(result.output)
    assert chat_env.calls == []
    assert result.exit_code != 0


def test_e1_auth_token_alone_counts_as_credentials(chat_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-placeholder")
    result = _invoke("Can I grapple?")
    assert result.exit_code == 0, result.output
    assert len(chat_env.calls) == 1


def test_e1_authentication_error_type_gives_credentials_message(chat_env):
    chat_env.state = _state(
        answer="Sorry, something went wrong.",
        citations=[],
        stop_reason="error",
        error_type="AuthenticationError",
        trace=[{"node": "route", "detail": "error", "ms": 1.0}],
    )
    result = _invoke("Can I grapple?")
    assert NO_CREDS in _flat(result.output)


def test_e1_other_errors_do_not_search_trace_text(chat_env):
    chat_env.state = _state(
        answer="Sorry, something went wrong.",
        citations=[],
        stop_reason="error",
        error_type="APIConnectionError",
        trace=[{"node": "route", "detail": "error: AuthenticationError mentioned", "ms": 1.0}],
    )
    result = _invoke("Can I grapple?")
    assert NO_CREDS not in _flat(result.output)
    assert "Sorry, something went wrong." in result.output


# --- H3 max-steps cap -----------------------------------------------------------------------


def test_h3_cli_max_steps_is_capped_at_10(chat_env):
    result = _invoke("Can I grapple?", "--max-steps", "11")
    ran_with = [c["deps"].max_steps for c in chat_env.calls]
    assert result.exit_code != 0 or all(n <= 10 for n in ran_with), (result.output, ran_with)


def test_h3_cli_max_steps_10_is_allowed(chat_env):
    result = _invoke("Can I grapple?", "--max-steps", "10")
    assert result.exit_code == 0, result.output
    assert chat_env.calls[0]["deps"].max_steps == 10


# --- S2 terminal output ---------------------------------------------------------------------

CONTROL = ["\x1b]0;pwned\x07", "\x00", "\x7f", "\x9b31m", "\x07"]


def test_s2_clean_for_terminal_strips_control_characters():
    from dungeonmind.cli import clean_for_terminal

    dirty = "a\x1bb\x07c\x00d\x7fe\x9bf\x01g\x1fh\x85i"
    assert clean_for_terminal(dirty) == "abcdefghi"


def test_s2_clean_for_terminal_keeps_newlines_tabs_and_unicode():
    from dungeonmind.cli import clean_for_terminal

    text = "Rules Glossary › Grappled\n\tpp. 182–183 — “quoted” é ü 龍"
    assert clean_for_terminal(text) == text


def test_s2_chat_output_has_no_control_characters(chat_env):
    chat_env.state = _state(
        answer="Grapple " + "".join(CONTROL) + " done [E1].",
        citations=[{"id": "E1", "kind": "srd", "label": "SRD\x1b]8;;evil\x07 label"}],
        violations=["E9\x9b"],
        tool_calls=[
            {
                "id": "T1",
                "name": "dice_roll",
                "arguments": {"expression": "1d4\x07"},
                "output": {"error": "bad\x1b]0;pwned\x07"},
                "is_error": True,
            }
        ],
        trace=[{"node": "agent", "detail": "error: Boom: \x1b]0;pwned\x07\x00", "ms": 1.0}],
    )
    result = _invoke("Can I grapple?", "--trace")
    assert result.exit_code == 0, result.output
    for ch in ("\x07", "\x00", "\x7f", "\x9b", "\x1b]"):
        assert ch not in result.output, repr(ch)
    assert "Grapple" in result.output and "done [E1]." in result.output


# --- M6-b: stored text printed by `search` and `ask --show-evidence` is cleaned --------------


def test_m6b_search_output_has_no_control_characters(monkeypatch):
    from fake_llm import evidence

    ev = evidence("evil", text="Rule\x1b]0;pwned\x07 text")
    ev.chunk.heading_path[-1] = "Head\x1b[31m"
    retriever = SimpleNamespace(by_id={}, retrieve=lambda *a, **k: [ev])
    monkeypatch.setattr(cli, "load_retriever", lambda *a, **k: retriever)
    result = CliRunner().invoke(cli.app, ["search", "x", "--full"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\x07" not in result.output
    assert "Rule]0;pwned text" in result.output


def test_m6b_show_evidence_output_has_no_control_characters(monkeypatch):
    from fake_llm import evidence

    ev = evidence("evil")
    ev.chunk.heading_path[-1] = "Head\x1b]0;pwned\x07"
    retriever = SimpleNamespace(by_id={}, retrieve=lambda *a, **k: [ev])
    monkeypatch.setattr(cli, "load_retriever", lambda *a, **k: retriever)

    class Boom:
        def answer(self, *a, **k):
            raise RuntimeError("stop after printing evidence")

    monkeypatch.setattr("dungeonmind.rag.answer.AnswerGenerator", lambda *a, **k: Boom())
    result = CliRunner().invoke(cli.app, ["ask", "x", "--show-evidence"], env=WIDE)
    assert "\x1b" not in result.output and "\x07" not in result.output
    assert "Head]0;pwned" in result.output
