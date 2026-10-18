"""M10a: character sheets — model (S1), 2024 derived values (S2), access and versions (S3)."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub
from pydantic import ValidationError

from dungeonmind.hub.api import CSRF_HEADER, create_app
from dungeonmind.hub.sheets import Sheet, derive

H = {CSRF_HEADER: "1"}


def _rogue(**over) -> Sheet:
    data = {
        "classes": [{"name": "Rogue", "level": 5, "hit_die": 8}],
        "abilities": {"str": 10, "dex": 18, "con": 14, "int": 12, "wis": 13, "cha": 8},
        "saves": ["dex", "int"],
        "skills": {"stealth": "expertise", "perception": "proficient"},
        "armor_class": 15,
        "speed": 30,
        "hp": {"max": 38, "current": 30, "temp": 5},
        "attacks": [
            {
                "name": "Rapier",
                "ability": "dex",
                "proficient": True,
                "damage": "1d8",
                "add_ability_to_damage": True,
                "damage_type": "piercing",
            }
        ],
        "inventory": [{"name": "Rope", "quantity": 1, "weight": 5}],
        "currency": {"gp": 100},
        "custom_dice": [{"name": "Sneak Attack", "expression": "3d6"}],
    }
    data.update(over)
    return Sheet.model_validate(data)


# --- S2 derived values ---------------------------------------------------------------------------


def test_s2_core_numbers_follow_the_2024_rules():
    d = derive(_rogue())
    assert d["level"] == 5 and d["proficiency_bonus"] == 3
    assert d["modifiers"]["dex"] == 4 and d["modifiers"]["cha"] == -1
    assert d["saves"]["dex"] == 7 and d["saves"]["str"] == 0
    assert d["skills"]["stealth"] == 10  # expertise: 4 + 2×3
    assert d["skills"]["perception"] == 4  # proficient: 1 + 3
    assert d["skills"]["arcana"] == 1  # untrained: INT mod only
    assert d["passive_perception"] == 14
    assert d["initiative"] == 4
    assert d["attacks"][0]["to_hit"] == 7
    assert d["attacks"][0]["damage"] == "1d8+4"
    assert d["hit_dice"] == {"8": 5}


def test_s2_proficiency_bonus_by_level():
    for level, pb in [(1, 2), (4, 2), (5, 3), (8, 3), (9, 4), (13, 5), (17, 6), (20, 6)]:
        d = derive(_rogue(classes=[{"name": "Fighter", "level": level, "hit_die": 10}]))
        assert d["proficiency_bonus"] == pb, level


def test_s2_multiclass_jack_of_all_trades_and_spellcasting():
    sheet = _rogue(
        classes=[
            {"name": "Bard", "level": 3, "hit_die": 8},
            {"name": "Fighter", "level": 2, "hit_die": 10},
        ],
        jack_of_all_trades=True,
        spellcasting={"ability": "cha", "slots": [{"max": 4, "used": 1}, {"max": 2, "used": 0}]},
    )
    d = derive(sheet)
    assert d["level"] == 5 and d["proficiency_bonus"] == 3
    assert d["skills"]["arcana"] == 1 + 1  # INT +1, half PB rounded down
    assert d["initiative"] == 4  # 2024: no Jack of All Trades on initiative (M10 F8)
    assert d["spell_save_dc"] == 8 + 3 - 1 and d["spell_attack"] == 3 - 1
    assert d["hit_dice"] == {"8": 3, "10": 2}


def test_s2_weight_coins_and_capacity():
    d = derive(_rogue(currency={"gp": 100, "sp": 50}))
    assert d["carried_weight"] == 5 + 3  # rope + 150 coins / 50
    assert d["carrying_capacity"] == 150  # STR 10 × 15


def test_s2_exhaustion_penalties():
    d = derive(_rogue(exhaustion=2))
    assert d["d20_penalty"] == -4
    assert d["speed"] == 20


def test_s2_an_empty_sheet_is_level_one():
    d = derive(Sheet())
    assert d["level"] == 1 and d["proficiency_bonus"] == 2 and d["modifiers"]["str"] == 0


# --- S1 validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        {"abilities": {"str": 31}},
        {
            "classes": [
                {"name": "Wizard", "level": 15, "hit_die": 6},
                {"name": "Cleric", "level": 6, "hit_die": 8},
            ]
        },  # total 21
        {"classes": [{"name": "Wizard", "level": 3, "hit_die": 7}]},
        {"skills": {"stealth": "master"}},
        {"skills": {"flying": "proficient"}},
        {"saves": ["luck"]},
        {"exhaustion": 7},
        {"conditions": ["sleepy"]},
        {"custom_dice": [{"name": "Bad", "expression": "1d20+rm -rf"}]},
        {"attacks": [{"name": "Axe", "damage": "101d6"}]},  # over the dice limit
        {"spellcasting": {"slots": [{"max": 2, "used": 3}]}},
        {"unknown_field": 1},
    ],
)
def test_s1_invalid_sheets_are_refused(over):
    with pytest.raises(ValidationError):
        _rogue(**over)


# --- S3 access over HTTP -------------------------------------------------------------------------


@pytest.fixture
def table(tmp_path):
    hub = make_hub(tmp_path)
    app = create_app(hub)

    def signup(name):
        c = TestClient(app)
        r = c.post("/api/auth/register", json={"username": name, "password": PASSWORD}, headers=H)
        assert r.status_code == 201
        return c

    dm, mia, bo = signup("dm_dana"), signup("mia_r"), signup("bo_k")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    for player in (mia, bo):
        code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
        player.post("/api/invites/accept", json={"code": code}, headers=H)
    chid = mia.post(f"/api/campaigns/{cid}/characters", json={"name": "Mira"}, headers=H).json()[
        "id"
    ]
    return dm, mia, bo, cid, chid


def _path(cid, chid):
    return f"/api/campaigns/{cid}/characters/{chid}/sheet"


def test_s3_an_unsaved_sheet_is_empty_with_version_zero(table):
    dm, mia, bo, cid, chid = table
    r = bo.get(_path(cid, chid))
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 0 and body["can_edit"] is False
    assert body["derived"]["level"] == 1


def test_s3_owner_and_dm_edit_everyone_views(table):
    dm, mia, bo, cid, chid = table
    sheet = _rogue().model_dump()
    r = mia.put(_path(cid, chid), json={"sheet": sheet, "version": 0}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    assert mia.get(_path(cid, chid)).json()["can_edit"] is True
    assert dm.get(_path(cid, chid)).json()["can_edit"] is True
    viewed = bo.get(_path(cid, chid)).json()
    assert viewed["sheet"]["armor_class"] == 15 and viewed["derived"]["skills"]["stealth"] == 10
    r = bo.put(_path(cid, chid), json={"sheet": sheet, "version": 1}, headers=H)
    assert r.status_code == 403
    r = dm.put(
        _path(cid, chid), json={"sheet": {**sheet, "armor_class": 16}, "version": 1}, headers=H
    )
    assert r.status_code == 200 and r.json()["sheet"]["armor_class"] == 16


def test_s3_a_stale_version_is_409_and_saves_nothing(table):
    dm, mia, bo, cid, chid = table
    sheet = _rogue().model_dump()
    assert mia.put(_path(cid, chid), json={"sheet": sheet, "version": 0}, headers=H).is_success
    r = dm.put(
        _path(cid, chid), json={"sheet": {**sheet, "armor_class": 20}, "version": 0}, headers=H
    )
    assert r.status_code == 409
    assert mia.get(_path(cid, chid)).json()["sheet"]["armor_class"] == 15


def test_s3_outsiders_get_404_and_invalid_sheets_422(table, tmp_path):
    dm, mia, bo, cid, chid = table
    r = mia.put(
        _path(cid, chid), json={"sheet": {"abilities": {"str": 99}}, "version": 0}, headers=H
    )
    assert r.status_code == 422
    assert mia.get(_path(cid, 999)).status_code == 404


def test_s3_a_large_sheet_fits(table):
    dm, mia, bo, cid, chid = table
    sheet = _rogue(
        spells=[{"name": f"Spell {i}", "level": i % 10, "notes": "x" * 150} for i in range(150)],
        inventory=[
            {"name": f"Item {i}", "quantity": 1, "weight": 1, "notes": "y" * 150}
            for i in range(150)
        ],
    ).model_dump()
    r = mia.put(_path(cid, chid), json={"sheet": sheet, "version": 0}, headers=H)
    assert r.status_code == 200, r.text[:200]


# --- M10 review fixes ----------------------------------------------------------------------------


def test_f8_jack_of_all_trades_does_not_touch_initiative_in_2024():
    d = derive(_rogue(jack_of_all_trades=True))
    assert d["initiative"] == 4  # plain DEX check: no half proficiency (2024)
    assert d["skills"]["arcana"] == 1 + 1  # skill checks still get it


def test_f1_a_failed_index_saves_nothing(table, monkeypatch):
    dm, mia, bo, cid, chid = table
    from dungeonmind.hub import service

    def boom(*a, **k):
        raise service.StorageError("Couldn't index this entry right now; it wasn't saved.")

    monkeypatch.setattr(service.Hub, "_embed", boom)
    r = mia.put(_path(cid, chid), json={"sheet": _rogue().model_dump(), "version": 0}, headers=H)
    assert r.status_code == 500
    assert mia.get(_path(cid, chid)).json()["version"] == 0  # the sheet wasn't saved either


def test_f6_renaming_the_character_renames_its_sheet_entry(table):
    dm, mia, bo, cid, chid = table
    mia.put(_path(cid, chid), json={"sheet": _rogue().model_dump(), "version": 0}, headers=H)
    mia.patch(f"/api/campaigns/{cid}/characters/{chid}", json={"name": "Mirabel"}, headers=H)
    entries = mia.get(f"/api/campaigns/{cid}/entries?board=character&character_id={chid}").json()
    sheet_entry = next(e for e in entries if e["kind"] == "sheet")
    assert sheet_entry["title"] == "Mirabel — character sheet"
    assert sheet_entry["body"].startswith("Mirabel:")


def test_f10_a_full_sheet_fits_the_body_limit(table):
    dm, mia, bo, cid, chid = table
    sheet = _rogue(
        features=[{"name": "Ö" * 80, "source": "ş" * 60, "text": "ğ" * 2000} for _ in range(60)],
    ).model_dump()
    r = mia.put(_path(cid, chid), json={"sheet": sheet, "version": 0}, headers=H)
    assert r.status_code == 200, r.text[:200]


def test_n1_rename_works_even_when_search_is_down(table, monkeypatch):
    dm, mia, bo, cid, chid = table
    mia.put(_path(cid, chid), json={"sheet": _rogue().model_dump(), "version": 0}, headers=H)
    from dungeonmind.hub import service

    def boom(*a, **k):
        raise service.StorageError("Search isn't available, so the entry wasn't saved.")

    monkeypatch.setattr(service.Hub, "_embed", boom)
    r = mia.patch(f"/api/campaigns/{cid}/characters/{chid}", json={"name": "Mirabel"}, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "Mirabel"
    monkeypatch.undo()
    # The next sheet save brings the generated entry up to date.
    mia.put(_path(cid, chid), json={"sheet": _rogue().model_dump(), "version": 1}, headers=H)
    entries = mia.get(f"/api/campaigns/{cid}/entries?board=character&character_id={chid}").json()
    assert next(e for e in entries if e["kind"] == "sheet")["title"] == "Mirabel — character sheet"
