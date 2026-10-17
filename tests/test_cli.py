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


# --- 9 and F6: serve -----------------------------------------------------------------------


@pytest.fixture
def serve_env(monkeypatch):
    rec = SimpleNamespace(run=None)
    monkeypatch.setattr("dungeonmind.hub.app.build_hub", lambda *a, **k: object())
    monkeypatch.setattr("dungeonmind.hub.api.create_app", lambda hub, **kw: "app")  # M9d: proxy kwarg
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: setattr(rec, "run", kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-placeholder")
    return rec


def test_9_serve_binds_localhost_without_access_log_by_default(serve_env):
    result = CliRunner().invoke(cli.app, ["serve"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert serve_env.run["host"] == "127.0.0.1"
    assert serve_env.run["access_log"] is False
    assert "Warning" not in result.output


def test_9_serving_on_all_interfaces_warns_about_plain_http(serve_env):
    result = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert "isn't encrypted" in _flat(result.output)


def test_hub_reset_password_cli(tmp_path):
    from hub_support import PASSWORD

    from dungeonmind.hub.service import Hub

    Hub(tmp_path, scrypt_n=2**4).register("mia_r", PASSWORD)
    new = "a new long password"
    result = CliRunner().invoke(
        cli.app,
        ["hub", "reset-password", "mia_r", "--root", str(tmp_path)],
        input=f"{new}\n{new}\n",
        env=WIDE,
    )
    assert result.exit_code == 0, result.output
    assert Hub(tmp_path, scrypt_n=2**4).login("mia_r", new)


# --- M8: eval commands and --models (no API calls) -------------------------------------------

CHUNKS_PRESENT = cli.DEFAULT_CHUNKS.exists()


@pytest.mark.skipif(not CHUNKS_PRESENT, reason="SRD chunks missing (run ingest)")
def test_m8_eval_validate_passes_on_the_shipped_suite():
    result = CliRunner().invoke(cli.app, ["eval", "validate"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert "valid" in result.output


@pytest.mark.skipif(not CHUNKS_PRESENT, reason="SRD chunks missing (run ingest)")
def test_m8_eval_live_estimate_needs_no_key_and_makes_no_calls(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("dungeonmind.graph.llm.ClaudeLLM", lambda *a, **k: pytest.fail("no LLM"))
    result = CliRunner().invoke(cli.app, ["eval", "live", "--estimate", "--limit", "5"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert "5 cases, estimated cost $" in result.output


@pytest.mark.skipif(not CHUNKS_PRESENT, reason="SRD chunks missing (run ingest)")
def test_m8_eval_live_refuses_when_the_estimate_exceeds_the_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-placeholder")
    monkeypatch.setattr("dungeonmind.graph.llm.ClaudeLLM", lambda *a, **k: pytest.fail("no LLM"))
    result = CliRunner().invoke(cli.app, ["eval", "live", "--max-cost", "0.05"], env=WIDE)
    assert result.exit_code != 0
    assert "exceeds --max-cost" in _flat(result.output)


@pytest.mark.skipif(not CHUNKS_PRESENT, reason="SRD chunks missing (run ingest)")
def test_m8_eval_live_without_credentials_stops_before_running(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    result = CliRunner().invoke(cli.app, ["eval", "live", "--limit", "1"], env=WIDE)
    assert result.exit_code != 0
    assert NO_CREDS in _flat(result.output)


def test_m8_eval_compare_prints_both_results(tmp_path):
    import json

    a = {
        "meta": {"label": "opus"},
        "categories": {"rules": {"pass_rate": 0.9, "n": 10}},
        "overall": {"pass_rate": 0.9},
        "cost": {"total": 4.0},
        "latency_ms": {"p50": 3000},
    }
    b = {**a, "meta": {"label": "haiku"}, "overall": {"pass_rate": 0.8}, "cost": {"total": 0.2}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    result = CliRunner().invoke(
        cli.app, ["eval", "compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")], env=WIDE
    )
    assert result.exit_code == 0, result.output
    assert "opus" in result.output and "haiku" in result.output and "-10.0" in result.output


def test_m8_bad_models_option_is_a_clear_error(chat_env):
    result = _invoke("q", "--models", "route=gpt-5")
    assert result.exit_code != 0
    assert "unknown model" in _flat(result.output)


def test_m8_models_option_reaches_the_llm(chat_env, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "dungeonmind.graph.llm.ClaudeLLM", lambda *a, **k: seen.update(k) or object()
    )
    result = _invoke("q", "--models", "route=claude-haiku-5-5")
    assert result.exit_code == 0, result.output
    assert seen["models"].for_step("route").model == "claude-haiku-5-5"


def test_m8_models_env_var_is_the_fallback(chat_env, monkeypatch):
    seen = {}
    monkeypatch.setenv("DUNGEONMIND_MODELS", "all=claude-sonnet-5-5")
    monkeypatch.setattr(
        "dungeonmind.graph.llm.ClaudeLLM", lambda *a, **k: seen.update(k) or object()
    )
    assert _invoke("q").exit_code == 0
    assert seen["models"].for_step("campaign").model == "claude-sonnet-5-5"


# --- M8b: --provider ollama --------------------------------------------------------------------


def test_m8b_chat_with_ollama_needs_no_key_and_checks_the_local_server(chat_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("dungeonmind.graph.ollama.check_ollama", lambda *a, **k: [])
    made = {}
    monkeypatch.setattr(
        "dungeonmind.graph.providers.make_llm",
        lambda provider, models: made.update(provider=provider) or object(),
    )
    result = _invoke("q", "--provider", "ollama")
    assert result.exit_code == 0, result.output
    assert made["provider"] == "ollama"
    assert chat_env.calls[0]["deps"].timeout_s == 300.0


def test_m8b_ollama_problems_are_reported_before_running(chat_env, monkeypatch):
    monkeypatch.setattr(
        "dungeonmind.graph.ollama.check_ollama",
        lambda *a, **k: ["Ollama isn't running: start it with `ollama serve`."],
    )
    result = _invoke("q", "--provider", "ollama")
    assert result.exit_code != 0
    assert "ollama serve" in _flat(result.output)
    assert chat_env.calls == []


def test_m8b_provider_env_var_is_the_fallback(chat_env, monkeypatch):
    monkeypatch.setenv("DUNGEONMIND_PROVIDER", "ollama")
    monkeypatch.setattr("dungeonmind.graph.ollama.check_ollama", lambda *a, **k: [])
    made = {}
    monkeypatch.setattr(
        "dungeonmind.graph.providers.make_llm",
        lambda provider, models: made.update(provider=provider) or object(),
    )
    assert _invoke("q").exit_code == 0
    assert made["provider"] == "ollama"


def test_m8b_unknown_provider_is_a_clear_error(chat_env):
    result = _invoke("q", "--provider", "gemini")
    assert result.exit_code != 0
    assert "claude" in _flat(result.output) and "ollama" in _flat(result.output)


def test_m8d_import_preview_writes_nothing(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Karakterler").mkdir(parents=True)
    (vault / "Karakterler" / "Arden.md").write_text(
        "---\nkind: npc\n---\nArden lonca ustası.\n", encoding="utf-8"
    )
    root = tmp_path / "hub"
    result = CliRunner().invoke(
        cli.app,
        [
            "hub",
            "import-obsidian",
            str(vault),
            "--dm",
            "dm_dana",
            "--campaign-name",
            "Eldoria",
            "--preview",
            "--root",
            str(root),
        ],
        env=WIDE,
    )
    assert result.exit_code == 0, result.output
    assert "Arden" in result.output and "Preview only" in result.output
    assert not root.exists()


def test_m8d_import_preview_with_archive_options(tmp_path):
    vault, archive, sessions = tmp_path / "v", tmp_path / "a", tmp_path / "s"
    for d in (vault, archive, sessions):
        d.mkdir()
    (archive / "Dünya.md").write_text("Dünya özeti.\n", encoding="utf-8")
    (sessions / "01 - Madalyon.md").write_text("Madalyon.\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "hub",
            "import-obsidian",
            str(vault),
            "--dm",
            "d",
            "--campaign-name",
            "c",
            "--preview",
            "--extra-lore",
            str(archive),
            "--sessions",
            str(sessions),
            "--root",
            str(tmp_path / "h"),
        ],
        env=WIDE,
    )
    assert result.exit_code == 0, result.output
    assert "Dünya" in result.output and "01 - Madalyon" in result.output
