"""M9f E1–E4: `dungeonmind eval campaign` on a hub folder."""

import json

from hub_support import PASSWORD, make_hub
from typer.testing import CliRunner

from dungeonmind import cli

WIDE = {"COLUMNS": "200", "TERMINAL_WIDTH": "200"}


def _hub(tmp_path, monkeypatch):
    root = tmp_path / "hub"
    hub = make_hub(root)
    dm = hub.register("dm_dana", PASSWORD)
    player = hub.register("player_one", PASSWORD)
    camp = hub.create_campaign(dm, "Ashvale")
    hub.accept_invite(player, hub.create_invite(dm, camp.id).code)
    hub.create_entry(dm, camp.id, board="lore", title="Baron Vex", kind="npc",
                     body="The baron rules the river town and hides a red oath.",
                     visibility="party")
    hub.create_entry(dm, camp.id, board="lore", title="The Red Oath", kind="plot",
                     body="Secret: the baron swore the red oath to the drowned god.",
                     visibility="private")
    hub.create_entry(dm, camp.id, board="lore", title="Old Mill", kind="location",
                     body="A ruined mill by the river, haunted by millers.", visibility="party")
    # The CLI builds its own hub on the folder; give it the test embedder.
    monkeypatch.setattr("dungeonmind.evals.campaign.make_embedder", lambda: hub.embedder)
    return root, camp.id


def _cases(tmp_path, rows):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def _run(root, cid, cases):
    return CliRunner().invoke(
        cli.app,
        ["eval", "campaign", str(cases), "--campaign", cid, "--root", str(root)],
        env=WIDE,
    )


def test_e2_reports_recall_and_misses_per_viewer(tmp_path, monkeypatch):
    root, cid = _hub(tmp_path, monkeypatch)
    cases = _cases(tmp_path, [
        {"id": "c1", "question": "who rules the river town", "viewer": "dm_dana",
         "expect": ["Baron Vex"]},
        {"id": "c2", "question": "what did the baron swear", "viewer": "player_one",
         "expect": ["The Red Oath"]},  # private to the DM: the player can't find it
    ])
    result = _run(root, cid, cases)
    assert result.exit_code == 0, result.output
    assert "R@5 0.50" in result.output
    assert "c2" in result.output and "missed" in result.output.lower()


def test_e4_a_leak_fails_the_run(tmp_path, monkeypatch):
    root, cid = _hub(tmp_path, monkeypatch)
    cases = _cases(tmp_path, [
        {"id": "leak1", "question": "baron oath", "viewer": "player_one", "expect": [],
         "no_leak": ["drowned god"]},
    ])
    assert _run(root, cid, cases).exit_code == 0  # the player can't see the private entry
    cases = _cases(tmp_path, [
        {"id": "leak2", "question": "baron oath", "viewer": "dm_dana", "expect": [],
         "no_leak": ["drowned god"]},  # the DM can: reported as a leak for this viewer
    ])
    result = _run(root, cid, cases)
    assert result.exit_code == 1
    assert "leak2" in result.output


def test_e1_invalid_lines_are_reported(tmp_path, monkeypatch):
    root, cid = _hub(tmp_path, monkeypatch)
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "a", "question": "q", "viewer": "dm_dana", "expect": []}\nnot json\n'
                    '{"id": "b", "question": "q", "viewer": "nobody", "expect": []}\n')
    result = _run(root, cid, path)
    assert result.exit_code == 1
    assert "line 2" in result.output
    assert "line 3" in result.output and "nobody" in result.output


def test_e3_it_writes_nothing(tmp_path, monkeypatch):
    root, cid = _hub(tmp_path, monkeypatch)
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*.db")}
    cases = _cases(tmp_path, [{"id": "c1", "question": "mill", "viewer": "dm_dana",
                               "expect": ["Old Mill"]}])
    assert _run(root, cid, cases).exit_code == 0
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*.db")} == before


def test_e2_titles_match_whatever_their_unicode_form(tmp_path, monkeypatch):
    import unicodedata

    root, cid = _hub(tmp_path, monkeypatch)
    with __import__("sqlite3").connect(root / "campaigns" / cid / "campaign.db") as db:
        db.execute("UPDATE entries SET title = ? WHERE title = 'Old Mill'",
                   (unicodedata.normalize("NFD", "Old Mïll"),))  # as a pre-fix import left it
    cases = _cases(tmp_path, [{"id": "c1", "question": "ruined mill by the river",
                               "viewer": "dm_dana", "expect": ["Old Mïll"]}])
    result = _run(root, cid, cases)
    assert "R@5 1.00" in result.output, result.output
