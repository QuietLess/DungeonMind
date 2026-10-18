"""M10.1 P1–P2."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}
HUGE = "99999999999999999999"


@pytest.fixture
def table(tmp_path):
    hub = make_hub(tmp_path)
    app = create_app(hub)
    c = TestClient(app)
    c.post("/api/auth/register", json={"username": "dm_dana", "password": PASSWORD}, headers=H)
    cid = c.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    chid = c.post(f"/api/campaigns/{cid}/characters", json={"name": "Mira"}, headers=H).json()["id"]
    return hub, c, cid, chid


@pytest.mark.parametrize(
    "path",
    [
        "/entries/{n}",
        "/characters/{n}/sheet",
        "/chats/{n}",
    ],
)
@pytest.mark.parametrize("n", [HUGE, "0", "-5"])
def test_p1_out_of_range_ids_are_404(table, path, n):
    hub, c, cid, chid = table
    r = c.get(f"/api/campaigns/{cid}" + path.format(n=n))
    assert r.status_code == 404, (path, n, r.status_code)


def test_p2_a_save_racing_a_rename_uses_the_current_name(table, monkeypatch):
    hub, c, cid, chid = table
    real = hub._sheet_entry_parts
    state = {"done": False}

    def parts(cid_, character, sheet, derived):
        result = real(cid_, character, sheet, derived)
        if not state["done"]:
            state["done"] = True
            with hub.storage.campaign(cid_) as db:  # the rename commits meanwhile
                db.execute("UPDATE characters SET name = 'Mirabel' WHERE id = ?", (chid,))
        return result

    monkeypatch.setattr(hub, "_sheet_entry_parts", parts)
    r = c.put(
        f"/api/campaigns/{cid}/characters/{chid}/sheet",
        json={"sheet": {"armor_class": 14}, "version": 0},
        headers=H,
    )
    assert r.status_code == 200, r.text
    entries = c.get(f"/api/campaigns/{cid}/entries?board=character&character_id={chid}").json()
    assert [e["title"] for e in entries if e["kind"] == "sheet"] == ["Mirabel — character sheet"]


@pytest.mark.parametrize("n", [HUGE, "-" + HUGE])
def test_p1_huge_character_id_filters_are_not_500(table, n):
    hub, c, cid, chid = table
    r = c.get(f"/api/campaigns/{cid}/entries?character_id={n}")
    assert r.status_code in (404, 422), r.status_code
