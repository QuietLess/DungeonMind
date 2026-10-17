"""M10d C1–C2: the generated sheet entry the chatbot reads."""

import pytest
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.errors import Forbidden, Invalid

SHEET = {
    "classes": [{"name": "Rogue", "level": 5, "hit_die": 8}],
    "abilities": {"str": 10, "dex": 18, "con": 14, "int": 12, "wis": 13, "cha": 8},
    "skills": {"stealth": "expertise"},
    "armor_class": 15,
    "hp": {"max": 38, "current": 30, "temp": 0},
    "attacks": [
        {
            "name": "Rapier",
            "ability": "dex",
            "proficient": True,
            "damage": "1d8",
            "add_ability_to_damage": True,
        }
    ],
    "custom_dice": [{"name": "Sneak Attack", "expression": "3d6"}],
}


@pytest.fixture
def table(tmp_path):
    hub = make_hub(tmp_path)
    dm = hub.register("dm_dana", PASSWORD)
    mia = hub.register("mia_r", PASSWORD)
    bo = hub.register("bo_k", PASSWORD)
    camp = hub.create_campaign(dm, "Ashvale")
    for p in (mia, bo):
        hub.accept_invite(p, hub.create_invite(dm, camp.id).code)
    char = hub.create_character(mia, camp.id, "Mira")
    return hub, dm, mia, bo, camp, char


def _sheet_entries(hub, user, cid, chid):
    return [
        e for e in hub.entries(user, cid, board="character", character_id=chid) if e.kind == "sheet"
    ]


def test_c1_saving_a_sheet_keeps_one_generated_entry(table):
    hub, dm, mia, bo, camp, char = table
    hub.save_sheet(mia, camp.id, char.id, SHEET, 0)
    hub.save_sheet(mia, camp.id, char.id, {**SHEET, "armor_class": 16}, 1)
    entries = _sheet_entries(hub, bo, camp.id, char.id)  # another player can see it
    assert len(entries) == 1
    e = entries[0]
    assert e.title == "Mira — character sheet" and e.visibility == "party"
    assert e.author_id == mia.id
    assert "AC 16" in e.body and "Stealth +10" in e.body and "Rapier" in e.body
    assert "Sneak Attack" in e.body


def test_c1_the_chatbot_finds_it_through_search(table):
    hub, dm, mia, bo, camp, char = table
    hub.save_sheet(mia, camp.id, char.id, SHEET, 0)
    hits = hub.search(bo, camp.id, "Mira armor class stealth")
    assert any(
        h.source.endswith("Mira — character sheet") or "character sheet" in h.source for h in hits
    )


def test_c2_nobody_writes_sheet_entries_directly(table):
    hub, dm, mia, bo, camp, char = table
    with pytest.raises(Invalid):
        hub.create_entry(
            mia,
            camp.id,
            board="character",
            character_id=char.id,
            kind="sheet",
            title="Fake",
            body="AC 30",
        )
    hub.save_sheet(mia, camp.id, char.id, SHEET, 0)
    entry = _sheet_entries(hub, mia, camp.id, char.id)[0]
    with pytest.raises(Forbidden):
        hub.update_entry(mia, camp.id, entry.id, body="AC 30")
    with pytest.raises(Forbidden):
        hub.delete_entry(mia, camp.id, entry.id)


def test_c2_deleting_the_character_removes_the_sheet_and_its_entry(table):
    hub, dm, mia, bo, camp, char = table
    hub.save_sheet(mia, camp.id, char.id, SHEET, 0)
    hub.delete_character(mia, camp.id, char.id)
    with hub.storage.campaign(camp.id) as db:
        assert db.execute("SELECT count(*) FROM entries WHERE kind = 'sheet'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sheets").fetchone()[0] == 0
