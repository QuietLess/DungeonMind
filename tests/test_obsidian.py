"""M8d Obsidian importer (I1–I7) on an invented Turkish vault. No real campaign content."""

import pytest
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.obsidian import apply_import, plan_import

NPC = """---
aliases: [Kızıl Lord]
kind: npc
status: alive
role: lonca ustası
related_people: ["[[Mira]]", "[[Arden]]"]
dm_note: Aslında kralın kayıp kardeşi.
secrets:
  - Gece yarısı ayini düzenliyor.
tags: []
---
# Arden

Arden, [[Kan Taşı Loncası|loncanın]] başında. [[Kızıl Köy#Tarihçe]] doğumlu.
![[arden.png]]
<!-- yazar notu -->
"""
PLACE = "---\nkind: place\n---\nKızıl Köy dağların eteğinde.\n"
CONCEPT = "---\nkind: concept\n---\nAyon enerjisi her şeyin kaynağı.\n"
PC = "---\nkind: pc\n---\nTaren, oyuncu karakteri.\n"
NOKIND_CHAR = "Mira köyün şifacısı.\n"
NOKIND_TOP = "Açık sorular: kral nerede?\n"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Karakterler").mkdir(parents=True)
    (v / "Excalidraw").mkdir()
    (v / ".obsidian").mkdir()
    (v / "Karakterler" / "Arden.md").write_text(NPC, encoding="utf-8")
    (v / "Karakterler" / "Mira.md").write_text(NOKIND_CHAR, encoding="utf-8")
    (v / "Mira.md").write_text(NOKIND_CHAR, encoding="utf-8")  # same text: imported once
    (v / "Arden.md").write_text("Farklı bir Arden notu.\n", encoding="utf-8")  # different text
    (v / "Kızıl Köy.md").write_text(PLACE, encoding="utf-8")
    (v / "Ayon Enerjisi.md").write_text(CONCEPT, encoding="utf-8")
    (v / "Taren.md").write_text(PC, encoding="utf-8")
    (v / "Açık Sorular.md").write_text(NOKIND_TOP, encoding="utf-8")
    (v / "Boş.md").write_text("---\nkind: npc\n---\n\n", encoding="utf-8")
    (v / "Uzun.md").write_text(
        "---\nkind: event\n---\n" + "\n\n".join(["Savaş anlatısı. " * 60] * 15), encoding="utf-8"
    )
    (v / "Excalidraw" / "Çizim.excalidraw.md").write_text("excalidraw data", encoding="utf-8")
    (v / "Untitled.base").write_text("base", encoding="utf-8")
    (v / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    s = tmp_path / "oturumlar"
    s.mkdir()
    (s / "Oturum 1.md").write_text("İlk oturum: köye vardık.\n", encoding="utf-8")
    (s / "oturum-04.txt").write_text("Dördüncü oturum: saray baskını.\n", encoding="utf-8")
    return v, s


def _snapshot(*roots):
    return {str(p): p.stat().st_mtime_ns for r in roots for p in r.rglob("*")}


def _by_title(plan):
    return {e.title: e for e in plan.entries}


def test_i3_kinds_follow_frontmatter_and_folders(vault):
    plan = _by_title(plan_import(vault[0]))
    assert (plan["Arden"].board, plan["Arden"].kind) == ("lore", "npc")
    assert plan["Kızıl Köy"].kind == "location"
    assert plan["Ayon Enerjisi"].kind == "other"
    assert plan["Taren (PC)"].kind == "other"
    assert plan["Mira"].kind == "npc"  # no kind, in Karakterler
    assert plan["Açık Sorular"].kind == "other"
    assert {e.visibility for e in plan.values()} == {"private"}


def test_i4_frontmatter_becomes_a_header_and_links_become_text(vault):
    body = _by_title(plan_import(vault[0]))["Arden"].body
    assert "kind:" not in body and "tags:" not in body and "---" not in body
    assert "**aliases:** Kızıl Lord" in body and "**status:** alive" in body
    assert "**related_people:** Mira, Arden" in body
    assert "loncanın başında" in body and "Kızıl Köy doğumlu" in body
    assert "[[" not in body and "arden.png" not in body and "yazar notu" not in body


def test_i5_dm_notes_and_secrets_are_a_separate_private_entry(vault):
    plan = _by_title(plan_import(vault[0]))
    assert "kayıp kardeşi" not in plan["Arden"].body and "ayini" not in plan["Arden"].body
    secret = plan["Arden — DM notes"]
    assert secret.visibility == "private" and secret.kind == "npc"
    assert "kayıp kardeşi" in secret.body and "Gece yarısı ayini" in secret.body


def test_i6_long_notes_split_duplicates_and_skips(vault):
    plan = plan_import(vault[0])
    titles = [e.title for e in plan.entries]
    parts = [t for t in titles if t.startswith("Uzun (")]
    assert len(parts) >= 2 and all(len(e.body) <= 10_000 for e in plan.entries)
    assert titles.count("Mira") == 1
    assert "Arden (vault)" in titles  # different text, same title: folder suffix
    skipped = dict(plan.skipped)
    assert "Excalidraw/Çizim.excalidraw.md" in skipped and "Untitled.base" in skipped
    assert "Boş.md" in skipped
    assert all(".obsidian" not in e.source for e in plan.entries)


def test_i7_session_files_become_private_journal_entries_with_numbers(vault):
    plan = plan_import(vault[0], vault[1])
    sessions = sorted((e.session, e.title) for e in plan.entries if e.board == "journal")
    assert sessions == [(1, "Oturum 1"), (4, "oturum-04")]
    assert all(e.visibility == "private" for e in plan.entries if e.board == "journal")


def test_i1_i2_preview_and_import_never_touch_the_vault(vault, tmp_path):
    before = _snapshot(*vault)
    plan = plan_import(*vault)
    hub = make_hub(tmp_path / "hub")
    hub.register("dm_dana", PASSWORD)
    camp, count = apply_import(hub, plan, "dm_dana", "Eldoria")
    assert count == len(plan.entries)
    assert _snapshot(*vault) == before


def test_i2_import_creates_the_campaign_and_refuses_a_non_empty_one(vault, tmp_path):
    hub = make_hub(tmp_path / "hub")
    dm = hub.register("dm_dana", PASSWORD)
    plan = plan_import(*vault)
    camp, count = apply_import(hub, plan, "dm_dana", "Eldoria")
    assert camp.role == "dm"
    titles = {e.title for e in hub.entries(dm, camp.id)}
    assert {"Arden", "Arden — DM notes", "Oturum 1"} <= titles
    with pytest.raises(ValueError, match="already has entries"):
        apply_import(hub, plan, "dm_dana", "Eldoria")


def test_i2_unknown_dm_is_a_clear_error(vault, tmp_path):
    with pytest.raises(ValueError, match="nobody"):
        apply_import(make_hub(tmp_path / "hub"), plan_import(vault[0]), "nobody", "X")
