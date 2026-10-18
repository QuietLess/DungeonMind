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
    assert roll["label"] == "Mira — Stealth (1d20)"  # dice kept in the label (review F3)
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


# --- M11 review fixes ----------------------------------------------------------------------------


def _setup_fight(dm, cid, chid):
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    dm.post(f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 3}, headers=H)
    dm.post(f"{base}/initiative", json={"only_missing": True}, headers=H)
    return eid, base


def _current_name(client, cid):
    enc = _enc(client, cid)
    return next((c["name"] for c in enc["combatants"] if c["id"] == enc["current_id"]), None)


def test_f1_the_current_turn_stays_put_when_the_order_changes(table):
    hub, dm, mia, bo, cid, chid = table
    eid, base = _setup_fight(dm, cid, chid)
    dm.post(f"{base}/turn", json={"direction": "next"}, headers=H)
    acting = _current_name(dm, cid)
    dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H)
    mira = next(c["id"] for c in _enc(dm, cid)["combatants"] if c["name"] == "Mira")
    dm.patch(f"{base}/combatants/{mira}", json={"initiative": 50}, headers=H)  # sorts first
    assert _current_name(dm, cid) == acting
    first = _enc(dm, cid)["combatants"][0]["id"]  # Mira, before the current one
    dm.delete(f"{base}/combatants/{first}", headers=H)
    assert _current_name(dm, cid) == acting


def test_f1_removing_the_acting_combatant_passes_the_turn_on(table):
    hub, dm, mia, bo, cid, chid = table
    eid, base = _setup_fight(dm, cid, chid)
    order = [c["name"] for c in _enc(dm, cid)["combatants"]]
    current = _enc(dm, cid)["current_id"]
    dm.delete(f"{base}/combatants/{current}", headers=H)
    assert _current_name(dm, cid) == order[1]


def test_f2_rolling_for_newcomers_keeps_the_round(table):
    hub, dm, mia, bo, cid, chid = table
    eid, base = _setup_fight(dm, cid, chid)
    for _ in range(7):  # into round 3
        dm.post(f"{base}/turn", json={"direction": "next"}, headers=H)
    before = _enc(dm, cid)
    dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H)
    dm.post(f"{base}/initiative", json={"only_missing": True}, headers=H)
    after = _enc(dm, cid)
    assert after["round"] == before["round"] == 3
    assert after["current_id"] == before["current_id"]


def test_f3_only_a_d20_initiative_roll_fills_the_slot(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    mira = dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H).json()["id"]
    big = _pool(mia, cid, dice={"100": 20}, character_id=chid, stat="initiative").json()
    assert "100" in big["label"]  # the dice stay visible in the log
    combatant = next(c for c in _enc(dm, cid)["combatants"] if c["id"] == mira)
    assert combatant["initiative"] is None
    good = _pool(mia, cid, dice={"20": 1}, character_id=chid, stat="initiative").json()
    combatant = next(c for c in _enc(dm, cid)["combatants"] if c["id"] == mira)
    assert combatant["initiative"] == good["total"]


def test_f4_concurrent_first_opens_of_an_old_campaign(tmp_path):
    import sqlite3
    import threading

    from dungeonmind.hub.db import Storage

    root = tmp_path / "hub"
    storage = Storage(root)
    cid = "a" * 32
    folder = storage.campaigns_root / cid
    folder.mkdir(mode=0o700)
    with sqlite3.connect(folder / "campaign.db") as db:  # characters as before M11
        db.execute(
            "CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,"
            " name TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
    errors = []

    def open_it():
        try:
            s = Storage(root)
            with s.campaign(cid) as db:
                db.execute("SELECT is_npc FROM characters").fetchall()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=open_it) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_f6_f7_players_learn_nothing_about_hidden_combatants(table):
    hub, dm, mia, bo, cid, chid = table
    eid, base = _setup_fight(dm, cid, chid)
    hidden = _enc(dm, cid)["current_id"]
    dm.patch(f"{base}/combatants/{hidden}", json={"hidden": True}, headers=H)
    seen = _enc(bo, cid)
    assert "turn" not in seen or seen["turn"] is None
    assert seen["current_id"] is None
    assert (
        mia.post(f"{base}/combatants/{hidden}/hp", json={"delta": -1}, headers=H).status_code == 404
    )


def test_f8_npc_rolls_dont_reveal_the_npc_to_players(table):
    hub, dm, mia, bo, cid, chid = table
    npc = dm.post(
        f"/api/campaigns/{cid}/characters", json={"name": "Baron", "npc": True}, headers=H
    ).json()["id"]
    _pool(dm, cid, dice={"20": 1}, character_id=npc)  # rolled publicly on purpose
    seen = bo.get(f"/api/campaigns/{cid}/rolls").json()
    assert seen and all(r["character_id"] is None for r in seen)


def test_f11_duplicate_die_keys_are_merged(table):
    hub, dm, mia, bo, cid, chid = table
    assert _pool(bo, cid, dice={"20": 20, "020": 20}).status_code == 422


def test_f12_monster_names_keep_counting(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    dm.post(f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 2}, headers=H)
    dm.post(f"{base}/combatants", json={"monster": "Goblin Warrior", "count": 1}, headers=H)
    names = sorted(c["name"] for c in _enc(dm, cid)["combatants"])
    assert names == ["Goblin Warrior 1", "Goblin Warrior 2", "Goblin Warrior 3"]


def test_f14_healing_from_zero_clears_death_saves(table):
    hub, dm, mia, bo, cid, chid = table
    eid = _start(dm, cid)
    base = f"/api/campaigns/{cid}/encounters/{eid}"
    mira = dm.post(f"{base}/combatants", json={"character_id": chid}, headers=H).json()["id"]
    path = f"/api/campaigns/{cid}/characters/{chid}/sheet"
    view = mia.get(path).json()
    sheet = {
        **view["sheet"],
        "hp": {"max": 30, "current": 0, "temp": 0},
        "death_saves": {"successes": 1, "failures": 2},
    }
    mia.put(path, json={"sheet": sheet, "version": view["version"]}, headers=H)
    dm.post(f"{base}/combatants/{mira}/hp", json={"delta": 5}, headers=H)
    assert mia.get(path).json()["sheet"]["death_saves"] == {"successes": 0, "failures": 0}
