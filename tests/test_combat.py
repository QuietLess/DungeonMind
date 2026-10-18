"""M11b dice pools (D1–D2) and M11c combat tracker (C1–C5)."""

import random

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub
from test_npcs import monsters

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}
SHEET = {
    "classes": [{"name": "Rogue", "level": 5, "hit_die": 8}],
    "abilities": {"str": 10, "dex": 18, "con": 14, "int": 12, "wis": 13, "cha": 8},
    "skills": {"stealth": "expertise"},
    "hp": {"max": 30, "current": 30, "temp": 5},
    "attacks": [
        {
            "name": "Rapier",
            "ability": "dex",
            "proficient": True,
            "damage": "1d8",
            "add_ability_to_damage": True,
        }
    ],
}


@pytest.fixture
def table(tmp_path):
    hub = make_hub(
        tmp_path,
        rng=random.Random(3),
        monsters=monsters,
        monster_names=lambda q: ["Goblin Warrior"],
    )
    app = create_app(hub)

    def signup(name):
        c = TestClient(app)
        r = c.post("/api/auth/register", json={"username": name, "password": PASSWORD}, headers=H)
        assert r.status_code == 201
        return c

    dm, mia, bo = signup("dm_dana"), signup("mia_r"), signup("bo_k")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    for p in (mia, bo):
        code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
        p.post("/api/invites/accept", json={"code": code}, headers=H)
    chid = mia.post(f"/api/campaigns/{cid}/characters", json={"name": "Mira"}, headers=H).json()[
        "id"
    ]
    mia.put(
        f"/api/campaigns/{cid}/characters/{chid}/sheet",
        json={"sheet": SHEET, "version": 0},
        headers=H,
    )
    return hub, dm, mia, bo, cid, chid


def _pool(client, cid, **body):
    return client.post(
        f"/api/campaigns/{cid}/rolls", json={"kind": "pool", "mode": "normal", **body}, headers=H
    )


# --- D1/D2 pools ---------------------------------------------------------------------------------


def test_d1_clicked_dice_are_rolled(table):
    hub, dm, mia, bo, cid, chid = table
    r = _pool(bo, cid, dice={"6": 2, "20": 1})
    assert r.status_code == 201, r.text
    roll = r.json()
    assert [t["term"] for t in roll["terms"]] == ["2d6", "1d20"]
    assert roll["total"] == sum(t["subtotal"] for t in roll["terms"])
    assert roll["label"] == "bo_k — 2d6+1d20"


def test_d1_limits_and_modes(table):
    hub, dm, mia, bo, cid, chid = table
    assert _pool(bo, cid, dice={}).status_code == 422
    assert _pool(bo, cid, dice={"7": 1}).status_code == 422
    assert _pool(bo, cid, dice={"6": 21}).status_code == 422
    assert _pool(bo, cid, dice={"6": 20, "8": 20, "10": 11}).status_code == 422
    assert _pool(bo, cid, dice={"6": 2}, mode="advantage").status_code == 422
    adv = _pool(bo, cid, dice={"20": 1}, mode="advantage").json()
    assert adv["terms"][0]["term"] == "2d20kh1"


def test_d2_a_stat_adds_the_characters_bonus(table):
    hub, dm, mia, bo, cid, chid = table
    roll = _pool(
        mia, cid, dice={"20": 1}, character_id=chid, stat="skill:stealth", modifier=1
    ).json()
    assert roll["bonus"] == 11
    assert roll["total"] == roll["terms"][0]["subtotal"] + 11
    assert roll["label"] == "Mira — Stealth"
    dmg = _pool(mia, cid, dice={"8": 1}, character_id=chid, stat="check:dex").json()
    assert dmg["bonus"] == 4
    assert (
        _pool(bo, cid, dice={"20": 1}, character_id=chid, stat="skill:stealth").status_code == 403
    )
    assert (
        _pool(mia, cid, dice={"20": 1}, character_id=chid, stat="skill:flying").status_code == 422
    )
    assert _pool(mia, cid, dice={"20": 1}, stat="skill:stealth").status_code == 422
    assert _pool(mia, cid, dice={"20": 1}, modifier=99).status_code == 422


# --- C1–C5 combat --------------------------------------------------------------------------------


def _enc(client, cid):
    return client.get(f"/api/campaigns/{cid}/encounter").json()


def _start(dm, cid):
    r = dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "Ambush"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_c1_one_active_encounter_dm_only(table):
    hub, dm, mia, bo, cid, chid = table
    assert _enc(bo, cid) is None
    assert (
        mia.post(f"/api/campaigns/{cid}/encounters", json={"name": "x"}, headers=H).status_code
        == 403
    )
    eid = _start(dm, cid)
    assert (
        dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "Again"}, headers=H).status_code
        == 409
    )
    assert _enc(bo, cid)["id"] == eid


def test_c1_c2_c3_add_roll_and_take_turns(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    assert dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H).status_code == 201
    r = dm.post(f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 2}, headers=H)
    assert r.status_code == 201
    enc = _enc(dm, cid)
    assert [c["name"] for c in enc["combatants"]] == [
        "Mira",
        "Goblin Warrior 1",
        "Goblin Warrior 2",
    ]
    r = dm.post(f"{base}/initiative", json={"only_missing": True}, headers=H)
    assert r.status_code == 200
    enc = _enc(dm, cid)
    inits = [c["initiative"] for c in enc["combatants"]]
    assert all(isinstance(i, int) for i in inits)
    assert inits == sorted(inits, reverse=True)
    assert enc["round"] == 1 and enc["turn"] == 0
    logged = dm.get(f"/api/campaigns/{cid}/rolls").json()
    goblin_rolls = [x for x in logged if x["label"].startswith("Goblin")]
    assert goblin_rolls and all(x["to_dm"] for x in goblin_rolls)
    for _ in range(3):
        dm.post(f"{base}/turn", json={"direction": "next"}, headers=H)
    enc = _enc(dm, cid)
    assert enc["round"] == 2 and enc["turn"] == 0
    dm.post(f"{base}/turn", json={"direction": "prev"}, headers=H)
    enc = _enc(dm, cid)
    assert enc["round"] == 1 and enc["turn"] == 2
    assert mia.post(f"{base}/turn", json={"direction": "next"}, headers=H).status_code == 403


def test_c2_dm_sets_initiative_and_players_fill_their_own(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    mira = dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H).json()["id"]
    roll = mia.post(
        f"/api/campaigns/{cid}/rolls",
        json={"kind": "initiative", "character_id": chid, "mode": "normal"},
        headers=H,
    ).json()
    combatant = next(c for c in _enc(dm, cid)["combatants"] if c["id"] == mira)
    assert combatant["initiative"] == roll["total"]
    dm.patch(f"{base}/combatants/{mira}", json={"initiative": 25}, headers=H)
    assert next(c for c in _enc(dm, cid)["combatants"] if c["id"] == mira)["initiative"] == 25


def test_c4_hp_changes_the_sheet_temp_first(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    mira = dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H).json()["id"]
    r = dm.post(f"{base}/combatants/{mira}/hp", json={"delta": -8}, headers=H)
    assert r.status_code == 200
    sheet = mia.get(f"/api/campaigns/{cid}/characters/{chid}/sheet").json()["sheet"]
    assert sheet["hp"] == {"max": 30, "current": 27, "temp": 0}  # 5 temp first, then 3
    mia.post(f"{base}/combatants/{mira}/hp", json={"delta": 100}, headers=H)
    assert (
        mia.get(f"/api/campaigns/{cid}/characters/{chid}/sheet").json()["sheet"]["hp"]["current"]
        == 30
    )
    dm.post(f"{base}/combatants/{mira}/hp", json={"delta": -100}, headers=H)
    assert (
        mia.get(f"/api/campaigns/{cid}/characters/{chid}/sheet").json()["sheet"]["hp"]["current"]
        == 0
    )
    assert bo.post(f"{base}/combatants/{mira}/hp", json={"delta": -1}, headers=H).status_code == 403
    r = mia.post(f"{base}/combatants/{mira}/conditions", json={"conditions": ["prone"]}, headers=H)
    assert r.status_code == 200
    assert mia.get(f"/api/campaigns/{cid}/characters/{chid}/sheet").json()["sheet"][
        "conditions"
    ] == ["prone"]


def test_c5_players_see_health_states_not_numbers_and_no_hidden(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H)
    gobs = dm.post(
        f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 2}, headers=H
    ).json()
    g1, g2 = gobs[0]["id"], gobs[1]["id"]
    dm.post(f"{base}/combatants/{g1}/hp", json={"delta": -6}, headers=H)  # 4/10 → bloodied
    dm.patch(f"{base}/combatants/{g2}", json={"hidden": True}, headers=H)
    seen = {c["name"]: c for c in _enc(bo, cid)["combatants"]}
    assert set(seen) == {"Mira", "Goblin Warrior 1"}
    goblin = seen["Goblin Warrior 1"]
    assert goblin["health"] == "bloodied"
    assert goblin.get("hp") is None and goblin.get("ac") is None
    assert seen["Mira"]["hp"]["current"] == 30
    dm_view = {c["name"]: c for c in _enc(dm, cid)["combatants"]}
    assert (
        dm_view["Goblin Warrior 1"]["hp"]["current"] == 4 and dm_view["Goblin Warrior 2"]["hidden"]
    )


def test_c1_end_removes_added_monsters_when_asked(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    dm.post(f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 2}, headers=H)
    keep = dm.post(
        f"/api/campaigns/{cid}/characters", json={"name": "Baron", "npc": True}, headers=H
    ).json()["id"]
    dm.post(f"{base}/combatants", json={"character_id": keep}, headers=H)
    r = dm.post(f"{base}/end", json={"remove_monsters": True}, headers=H)
    assert r.status_code == 200
    assert _enc(dm, cid) is None
    names = [c["name"] for c in dm.get(f"/api/campaigns/{cid}/characters").json()]
    assert names == ["Baron", "Mira"]
