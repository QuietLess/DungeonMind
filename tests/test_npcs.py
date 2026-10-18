"""M11a: NPCs (N1–N4)."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}

GOBLIN = {
    "key": "srd-2024_goblin-warrior",
    "name": "Goblin Warrior",
    "challenge_rating": 0.25,
    "armor_class": 15,
    "hit_points": 10,
    "speed": {"walk": 30, "unit": "feet"},
    "ability_scores": {
        "strength": 8,
        "dexterity": 15,
        "constitution": 10,
        "intelligence": 10,
        "wisdom": 8,
        "charisma": 8,
    },
    "initiative_bonus": 2,
    "saving_throws_all": {
        "strength": -1,
        "dexterity": 2,
        "constitution": 0,
        "intelligence": 0,
        "wisdom": -1,
        "charisma": -1,
    },
    "skill_bonuses": {"stealth": 6},
    "skill_bonuses_all": {"stealth": 6, "perception": -1},
    "actions": [
        {
            "name": "Scimitar",
            "desc": "Melee Attack Roll: +4, reach 5 ft. 5 (1d6 + 2) Slashing.",
            "attacks": [
                {
                    "name": "Scimitar attack",
                    "to_hit_mod": 4,
                    "damage_die_count": 1,
                    "damage_die_type": "D6",
                    "damage_bonus": 2,
                    "damage_type": {"name": "Slashing"},
                }
            ],
        },
    ],
    "traits": [{"name": "Nimble Escape", "desc": "Disengage or Hide as a bonus action."}],
}


def monsters(name: str):
    return GOBLIN if name.casefold() in {"goblin warrior", GOBLIN["key"]} else None


@pytest.fixture
def table(tmp_path):
    hub = make_hub(tmp_path, monsters=monsters, monster_names=lambda q: ["Goblin Warrior"])
    app = create_app(hub)

    def signup(name):
        c = TestClient(app)
        r = c.post("/api/auth/register", json={"username": name, "password": PASSWORD}, headers=H)
        assert r.status_code == 201
        return c

    dm, mia = signup("dm_dana"), signup("mia_r")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
    mia.post("/api/invites/accept", json={"code": code}, headers=H)
    return hub, dm, mia, cid


def test_n1_only_the_dm_creates_and_sees_npcs(table):
    hub, dm, mia, cid = table
    r = dm.post(
        f"/api/campaigns/{cid}/characters", json={"name": "Baron Vex", "npc": True}, headers=H
    )
    assert r.status_code == 201 and r.json()["is_npc"] is True
    npc = r.json()["id"]
    assert (
        mia.post(
            f"/api/campaigns/{cid}/characters", json={"name": "Fake", "npc": True}, headers=H
        ).status_code
        == 403
    )
    assert [c["name"] for c in dm.get(f"/api/campaigns/{cid}/characters").json()] == ["Baron Vex"]
    assert mia.get(f"/api/campaigns/{cid}/characters").json() == []
    assert mia.get(f"/api/campaigns/{cid}/characters/{npc}/sheet").status_code == 404
    r = mia.post(
        f"/api/campaigns/{cid}/rolls",
        json={"kind": "initiative", "character_id": npc, "mode": "normal"},
        headers=H,
    )
    assert r.status_code == 404


def test_n2_npc_sheet_entry_is_private_and_pb_can_be_set(table):
    hub, dm, mia, cid = table
    npc = dm.post(
        f"/api/campaigns/{cid}/characters", json={"name": "Baron Vex", "npc": True}, headers=H
    ).json()["id"]
    r = dm.put(
        f"/api/campaigns/{cid}/characters/{npc}/sheet",
        json={"sheet": {"proficiency_bonus": 4, "saves": ["wis"]}, "version": 0},
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["derived"]["proficiency_bonus"] == 4
    assert r.json()["derived"]["saves"]["wis"] == 4
    entries = dm.get(f"/api/campaigns/{cid}/entries?board=character&character_id={npc}").json()
    assert [e["visibility"] for e in entries if e["kind"] == "sheet"] == ["private"]
    assert mia.get(f"/api/campaigns/{cid}/search?q=Baron").json() == []


def test_n3_npcs_from_an_srd_monster(table):
    hub, dm, mia, cid = table
    r = dm.post(
        f"/api/campaigns/{cid}/npcs/from-monster",
        json={"monster": "Goblin Warrior", "count": 2},
        headers=H,
    )
    assert r.status_code == 201, r.text
    made = r.json()
    assert [c["name"] for c in made] == ["Goblin Warrior 1", "Goblin Warrior 2"]
    view = dm.get(f"/api/campaigns/{cid}/characters/{made[0]['id']}/sheet").json()
    s, d = view["sheet"], view["derived"]
    assert s["armor_class"] == 15 and s["hp"] == {"max": 10, "current": 10, "temp": 0}
    assert d["proficiency_bonus"] == 2
    assert d["initiative"] == 2
    assert d["skills"]["stealth"] == 6 and d["saves"]["dex"] == 2
    assert d["attacks"][0]["to_hit"] == 4 and d["attacks"][0]["damage"] == "1d6+2"
    assert any(f["name"] == "Nimble Escape" for f in s["features"])
    assert (
        mia.post(
            f"/api/campaigns/{cid}/npcs/from-monster",
            json={"monster": "Goblin Warrior", "count": 1},
            headers=H,
        ).status_code
        == 403
    )
    assert (
        dm.post(
            f"/api/campaigns/{cid}/npcs/from-monster",
            json={"monster": "Tarrasque Lite", "count": 1},
            headers=H,
        ).status_code
        == 404
    )


def test_n3_monster_search(table):
    hub, dm, mia, cid = table
    assert dm.get(f"/api/campaigns/{cid}/monsters?q=gob").json() == ["Goblin Warrior"]
    assert mia.get(f"/api/campaigns/{cid}/monsters?q=gob").status_code == 403


def test_n4_hp_changes_dont_re_embed(table):
    hub, dm, mia, cid = table
    chid = mia.post(f"/api/campaigns/{cid}/characters", json={"name": "Mira"}, headers=H).json()[
        "id"
    ]
    path = f"/api/campaigns/{cid}/characters/{chid}/sheet"
    mia.put(
        path, json={"sheet": {"hp": {"max": 20, "current": 20, "temp": 0}}, "version": 0}, headers=H
    )
    before = len(hub.embedder.documents)
    mia.put(
        path, json={"sheet": {"hp": {"max": 20, "current": 12, "temp": 0}}, "version": 1}, headers=H
    )
    assert len(hub.embedder.documents) == before
