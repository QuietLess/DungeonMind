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


def test_i7_session_numbers_from_names_only_when_asked(vault):
    plan = plan_import(vault[0], vault[1], session_numbers="name")
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


def test_i5_dm_note_true_is_a_flag_not_a_secret(tmp_path):
    """Found on the owner's vault: `dm_note: true` marks the whole note as DM-only."""
    v = tmp_path / "v"
    v.mkdir()
    (v / "Velor.md").write_text(
        "---\nkind: concept\ndm_note: true\n---\nVelor gizli bir güç.\n", encoding="utf-8"
    )
    (v / "Arden.md").write_text(
        "---\nkind: npc\ndm_note: false\nsecrets: []\n---\nArden.\n", encoding="utf-8"
    )
    plan = _by_title(plan_import(v))
    assert set(plan) == {"Velor", "Arden"}  # no "— DM notes" entries for flags or empty lists
    assert "**DM only:** yes" in plan["Velor"].body
    assert "DM only" not in plan["Arden"].body


# --- the owner's archive: reconstructed recaps, a plan, and overview documents -----------------

RECAP = "---\ntype: session-recap\nstatus: played\ncertainty: partial\n---\nMadalyon olayı.\n"
PLAN = "---\ntype: dm-preparation\nstatus: planned-not-confirmed-played\n---\nKöy planı.\n"
NUMBERED = "---\nsession: 7\nstatus: played\n---\nYedinci oturum.\n"


def test_i7_by_default_only_a_session_field_gives_a_number(tmp_path):
    s = tmp_path / "s"
    s.mkdir()
    (s / "01 - Madalyon.md").write_text(RECAP, encoding="utf-8")
    (s / "05 - Plan.md").write_text(PLAN, encoding="utf-8")
    (s / "Oturum.md").write_text(NUMBERED, encoding="utf-8")
    v = tmp_path / "v"
    v.mkdir()
    plan = {e.title: e for e in plan_import(v, s).entries}
    assert plan["01 - Madalyon"].session is None  # "01" is reading order, not a session
    assert plan["05 - Plan"].session is None
    assert plan["Oturum"].session == 7


def test_i7_session_entries_keep_type_status_and_certainty(tmp_path):
    s = tmp_path / "s"
    s.mkdir()
    (s / "05 - Plan.md").write_text(PLAN, encoding="utf-8")
    (s / "01 - Madalyon.md").write_text(RECAP, encoding="utf-8")
    v = tmp_path / "v"
    v.mkdir()
    plan = {e.title: e for e in plan_import(v, s).entries}
    assert "**status:** planned-not-confirmed-played" in plan["05 - Plan"].body
    assert "**type:** dm-preparation" in plan["05 - Plan"].body
    assert "**certainty:** partial" in plan["01 - Madalyon"].body


def test_i8_extra_lore_adds_top_level_notes_marked_as_archive(tmp_path, vault):
    a = tmp_path / "archive"
    (a / "Oturum_Notlari").mkdir(parents=True)
    (a / "Arden.md").write_text(
        "---\ncertainty: partial\n---\nArşiv özeti: Arden.\n", encoding="utf-8"
    )
    (a / "Dünya.md").write_text("Dünya ve siyaset özeti.\n", encoding="utf-8")
    (a / "Oturum_Notlari" / "01.md").write_text("alt klasör: alınmaz\n", encoding="utf-8")
    plan = {e.title: e for e in plan_import(vault[0], extra_lore=[a]).entries}
    assert "Arden" in plan and "Arden (arşiv)" in plan  # clash with the vault note
    assert "**source:** archive" in plan["Arden (arşiv)"].body
    assert "**certainty:** partial" in plan["Arden (arşiv)"].body
    assert plan["Dünya"].board == "lore" and plan["Dünya"].visibility == "private"
    assert not any("alt klasör" in e.body for e in plan.values())  # subfolders not included


def test_i9_titles_are_nfc_so_archive_duplicates_are_recognised(tmp_path, vault):
    import unicodedata

    a = tmp_path / "archive"
    a.mkdir()
    nfd = unicodedata.normalize("NFD", "Kızıl Köy")  # how unzipped macOS names arrive
    (a / f"{nfd}.md").write_text("Arşivdeki köy özeti.\n", encoding="utf-8")
    titles = [e.title for e in plan_import(vault[0], extra_lore=[a]).entries]
    assert "Kızıl Köy (arşiv)" in titles  # recognised as the vault note's namesake
    assert all(t == unicodedata.normalize("NFC", t) for t in titles)


SECTIONED = """---
kind: item
---
Eski bir madalyon.

## Bilinenler
Sahibi Erius.

## DM notu
Madalyon arzuyu duyar.

### Alt not
Bu da gizli.

## Sonra
Herkesin bildiği kısım.

## Gerçek
Aslında bir tanrının parçası.
"""


def test_i10_dm_sections_move_to_the_dm_notes_entry(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    (v / "Madalyon.md").write_text(SECTIONED, encoding="utf-8")
    plan = {e.title: e for e in plan_import(v).entries}
    main, notes = plan["Madalyon"].body, plan["Madalyon — DM notes"].body
    assert "arzuyu duyar" not in main and "Bu da gizli" not in main
    assert "Sahibi Erius" in main and "Herkesin bildiği" in main
    assert "## DM notu" in notes and "Bu da gizli" in notes  # subsections go along
    assert "Aslında bir tanrının" in main  # not a DM heading by default
    assert plan["Madalyon — DM notes"].visibility == "private"


def test_i10_more_secret_headings_on_request(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    (v / "Madalyon.md").write_text(SECTIONED, encoding="utf-8")
    plan = {e.title: e for e in plan_import(v, secret_headings=("DM", "Gerçek")).entries}
    assert "Aslında bir tanrının" not in plan["Madalyon"].body
    assert "Aslında bir tanrının" in plan["Madalyon — DM notes"].body


def test_i10_cli_passes_secret_headings(tmp_path):
    from typer.testing import CliRunner

    from dungeonmind import cli

    v = tmp_path / "v"
    v.mkdir()
    (v / "Madalyon.md").write_text(SECTIONED, encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "hub",
            "import-obsidian",
            str(v),
            "--dm",
            "x",
            "--campaign-name",
            "y",
            "--preview",
            "--secret-heading",
            "DM",
            "--secret-heading",
            "Gerçek",
            "--root",
            str(tmp_path / "h"),
        ],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.output
    assert "Madalyon — DM notes" in result.output


def _split(text, **kw):
    import tempfile
    from pathlib import Path

    v = Path(tempfile.mkdtemp())
    (v / "Not.md").write_text(text, encoding="utf-8")
    return {e.title: e.body for e in plan_import(v, **kw).entries}


def test_r4_headings_inside_code_fences_dont_end_a_dm_section():
    plan = _split("Açık.\n\n## DM notu\nsır A\n```\n# yorum\n```\nsır B\n\n## Sonra\nherkes\n")
    assert "sır B" not in plan["Not"] and "sır B" in plan["Not — DM notes"]
    assert "herkes" in plan["Not"]


def test_r5_indented_headings_word_boundary_and_turkish_case():
    plan = _split("Açık.\n\n  ## DM notu\nsır\n\n## Dmitri the smith\nherkes\n")
    assert "sır" in plan["Not — DM notes"] and "Dmitri" in plan["Not"]
    plan = _split("Açık.\n\n## GİZLİ bilgi\nsır\n", secret_headings=("Gizli",))
    assert "sır" in plan["Not — DM notes"]


def test_r9_duplicates_with_different_dm_sections_keep_both():
    import tempfile
    from pathlib import Path

    v = Path(tempfile.mkdtemp())
    for folder, secret in (("a", "sır bir"), ("b", "sır iki")):
        (v / folder).mkdir()
        (v / folder / "Han.md").write_text(
            f"Aynı metin.\n\n## DM notu\n{secret}\n", encoding="utf-8"
        )
    bodies = " ".join(e.body for e in plan_import(v).entries)
    assert "sır bir" in bodies and "sır iki" in bodies


def test_r10_a_long_title_keeps_the_dm_notes_label():
    plan = _split("Açık.\n\n## DM notu\nsır\n")
    import tempfile
    from pathlib import Path

    v = Path(tempfile.mkdtemp())
    (v / f"{'Uzun' * 30}.md").write_text("Açık.\n\n## DM notu\nsır\n", encoding="utf-8")
    titles = [e.title for e in plan_import(v).entries]
    assert len(titles) == 2 and len(set(titles)) == 2
    assert any(t.endswith("— DM notes") and len(t) <= 120 for t in titles)
    assert plan
