"""M10b: server rolls (R1), who may roll (R2), the game log (R3) and limits (R4)."""

import random

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}

SHEET = {
    "classes": [{"name": "Rogue", "level": 5, "hit_die": 8}],
    "abilities": {"str": 10, "dex": 18, "con": 14, "int": 12, "wis": 13, "cha": 8},
    "saves": ["dex"],
    "skills": {"stealth": "expertise"},
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
    "spellcasting": {"ability": "int"},
}


@pytest.fixture
def table(tmp_path):
    hub = make_hub(tmp_path, rng=random.Random(7))
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
    r = mia.put(
        f"/api/campaigns/{cid}/characters/{chid}/sheet",
        json={"sheet": SHEET, "version": 0},
        headers=H,
    )
    assert r.status_code == 200, r.text
    return hub, dm, mia, bo, cid, chid


def _roll(client, cid, **body):
    return client.post(f"/api/campaigns/{cid}/rolls", json={"mode": "normal", **body}, headers=H)


def _kept_sum(roll):
    return sum(sum(t["kept"]) * (1 if t["subtotal"] >= 0 else -1) for t in roll["terms"])


@pytest.mark.parametrize(
    ("kind", "key", "bonus", "label"),
    [
        ("check", "dex", 4, "Dexterity check"),
        ("save", "dex", 7, "Dexterity save"),
        ("skill", "stealth", 10, "Stealth"),
        ("initiative", None, 4, "Initiative"),
        ("attack", "0", 7, "Rapier: attack"),
        ("spell_attack", None, 4, "Spell attack"),
    ],
)
def test_r1_d20_tests_use_the_sheets_bonus(table, kind, key, bonus, label):
    hub, dm, mia, bo, cid, chid = table
    body = {"character_id": chid, "kind": kind}
    if key is not None:
        body["key"] = key
    r = _roll(mia, cid, **body)
    assert r.status_code == 201, r.text
    roll = r.json()
    assert roll["bonus"] == bonus
    assert roll["label"] == f"Mira — {label}"
    assert roll["total"] == _kept_sum(roll) + bonus
    assert roll["terms"][0]["term"] == "1d20"


def test_r1_the_request_cant_choose_the_bonus(table):
    hub, dm, mia, bo, cid, chid = table
    r = _roll(mia, cid, character_id=chid, kind="skill", key="stealth", bonus=99)
    assert r.status_code == 422  # unknown field


def test_r1_advantage_disadvantage_and_naturals(table):
    hub, dm, mia, bo, cid, chid = table
    adv = _roll(mia, cid, character_id=chid, kind="check", key="dex", mode="advantage").json()
    assert adv["terms"][0]["term"] == "2d20kh1"
    assert adv["terms"][0]["kept"] == [max(adv["terms"][0]["rolls"])]
    dis = _roll(mia, cid, character_id=chid, kind="check", key="dex", mode="disadvantage").json()
    assert dis["terms"][0]["kept"] == [min(dis["terms"][0]["rolls"])]
    for roll in (adv, dis):
        natural = roll["terms"][0]["kept"][0]
        assert roll["natural"] == natural


def test_r1_damage_custom_hit_die_and_criticals(table):
    hub, dm, mia, bo, cid, chid = table
    dmg = _roll(mia, cid, character_id=chid, kind="damage", key="0").json()
    assert dmg["terms"][0]["term"] == "1d8" and dmg["bonus"] == 4
    crit = _roll(mia, cid, character_id=chid, kind="damage", key="0", mode="critical").json()
    assert crit["terms"][0]["term"] == "2d8" and crit["bonus"] == 4
    sneak = _roll(mia, cid, character_id=chid, kind="custom", key="0").json()
    assert sneak["label"] == "Mira — Sneak Attack" and sneak["terms"][0]["term"] == "3d6"
    hd = _roll(mia, cid, character_id=chid, kind="hit_die", key="8").json()
    assert hd["terms"][0]["term"] == "1d8" and hd["bonus"] == 2


def test_r1_exhaustion_applies_to_d20_tests_only(table):
    hub, dm, mia, bo, cid, chid = table
    sheet = {**SHEET, "exhaustion": 2}
    mia.put(
        f"/api/campaigns/{cid}/characters/{chid}/sheet",
        json={"sheet": sheet, "version": 1},
        headers=H,
    )
    assert _roll(mia, cid, character_id=chid, kind="skill", key="stealth").json()["bonus"] == 6
    assert _roll(mia, cid, character_id=chid, kind="damage", key="0").json()["bonus"] == 4


def test_r1_death_save_and_free_expressions(table):
    hub, dm, mia, bo, cid, chid = table
    ds = _roll(mia, cid, character_id=chid, kind="death_save").json()
    assert ds["outcome"] in {"success", "failure", "critical success", "critical failure"}
    free = _roll(bo, cid, kind="expression", expression="2d6+3").json()
    assert free["label"] == "bo_k — 2d6+3" and free["total"] == _kept_sum(free) + 3
    assert _roll(bo, cid, kind="expression", expression="1d20+drop").status_code == 422


def test_r1_bad_keys_are_422(table):
    hub, dm, mia, bo, cid, chid = table
    assert _roll(mia, cid, character_id=chid, kind="skill", key="flying").status_code == 422
    assert _roll(mia, cid, character_id=chid, kind="attack", key="5").status_code == 422
    assert _roll(mia, cid, character_id=chid, kind="hit_die", key="12").status_code == 422


# --- R2 who may roll -----------------------------------------------------------------------------


def test_r2_only_the_player_or_dm_rolls_for_a_character(table):
    hub, dm, mia, bo, cid, chid = table
    assert _roll(bo, cid, character_id=chid, kind="initiative").status_code == 403
    assert _roll(dm, cid, character_id=chid, kind="initiative").status_code == 201


# --- R3 game log ---------------------------------------------------------------------------------


def test_r3_log_shows_public_rolls_and_hides_dm_only_ones(table):
    hub, dm, mia, bo, cid, chid = table
    first = _roll(mia, cid, character_id=chid, kind="initiative").json()
    secret = _roll(mia, cid, character_id=chid, kind="skill", key="stealth", to_dm=True).json()
    log = lambda c, after=0: c.get(f"/api/campaigns/{cid}/rolls?after={after}").json()  # noqa: E731
    assert [r["id"] for r in log(bo)] == [first["id"]]
    assert [r["id"] for r in log(mia)] == [first["id"], secret["id"]]
    assert [r["id"] for r in log(dm)] == [first["id"], secret["id"]]
    assert log(bo, after=first["id"]) == []
    assert log(dm)[1]["to_dm"] is True


def test_r3_a_campaign_keeps_its_last_500_rolls(table):
    hub, dm, mia, bo, cid, chid = table
    with hub.storage.campaign(cid) as db:
        for i in range(520):
            db.execute(
                "INSERT INTO rolls (user_id, author_name, character_id, label, expression, "
                "detail, total, to_dm, created_at) VALUES ('x', 'x', NULL, 'l', 'e', '{}', 1, 0, "
                "'2026-10-18T00:00:00+00:00')"
            )
    _roll(bo, cid, kind="expression", expression="1d4")
    with hub.storage.campaign(cid) as db:
        assert db.execute("SELECT count(*) FROM rolls").fetchone()[0] == 500


def test_r3_outsiders_see_nothing(table, tmp_path):
    hub, dm, mia, bo, cid, chid = table
    app = create_app(hub)
    out = TestClient(app)
    out.post("/api/auth/register", json={"username": "outsider", "password": PASSWORD}, headers=H)
    assert out.get(f"/api/campaigns/{cid}/rolls").status_code == 404
    assert _roll(out, cid, kind="expression", expression="1d4").status_code == 404


# --- R4 limits -----------------------------------------------------------------------------------


def test_r4_thirty_rolls_a_minute(table):
    hub, dm, mia, bo, cid, chid = table
    for _ in range(30):
        assert _roll(bo, cid, kind="expression", expression="1d4").status_code == 201
    assert _roll(bo, cid, kind="expression", expression="1d4").status_code == 429
    hub.clock.advance(minutes=1, seconds=1)
    assert _roll(bo, cid, kind="expression", expression="1d4").status_code == 201
