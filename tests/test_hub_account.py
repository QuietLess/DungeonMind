"""M9b: account management over HTTP (A1–A4)."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}
NEW = "a brand new passphrase"


def _engine(question, search_notes, store, conversation_id, history=()):
    return {
        "answer": "ok",
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
        "stop_reason": "completed",
        "trace": [],
    }


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path, engine=_engine, daily_message_limit=7)


@pytest.fixture
def app(hub):
    return create_app(hub)


def _signup(app, username) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"username": username, "password": PASSWORD}, headers=H)
    assert r.status_code == 201, r.text
    return c


def _login(app, username, password=PASSWORD) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return c


def _campaign_with_player(app):
    dm = _signup(app, "dm_dana")
    player = _signup(app, "player_one")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
    assert player.post("/api/invites/accept", json={"code": code}, headers=H).status_code == 200
    return dm, player, cid


# --- A1 ----------------------------------------------------------------------------------------


def test_a1_account_shows_usage_and_sessions(app):
    dm, _, cid = _campaign_with_player(app)
    _login(app, "dm_dana")  # a second device
    tid = dm.post(f"/api/campaigns/{cid}/chats", json={"title": "q"}, headers=H).json()["id"]
    dm.post(f"/api/campaigns/{cid}/chats/{tid}/messages", json={"text": "hi"}, headers=H)
    r = dm.get("/api/account")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "dm_dana"
    assert body["created_at"]
    assert body["sessions"] == 2
    assert body["questions_today"] == 1
    assert body["daily_limit"] == 7


def test_a_account_routes_need_a_session_and_the_csrf_header(app):
    anon = TestClient(app)
    assert anon.get("/api/account").status_code == 401
    me = _signup(app, "mia_r")
    for path in ("/api/account/password", "/api/account/sign-out-others", "/api/account/delete"):
        assert anon.post(path, json={}, headers=H).status_code == 401
        assert me.post(path, json={}).status_code == 403  # no CSRF header


# --- A2 ----------------------------------------------------------------------------------------


def test_a2_change_password_keeps_this_session_and_ends_the_others(app):
    me = _signup(app, "mia_r")
    other = _login(app, "mia_r")
    r = me.post(
        "/api/account/password",
        json={"current_password": PASSWORD, "new_password": NEW},
        headers=H,
    )
    assert r.status_code == 204, r.text
    assert me.get("/api/account").status_code == 200
    assert other.get("/api/account").status_code == 401
    _login(app, "mia_r", NEW)
    old = TestClient(app).post(
        "/api/auth/login", json={"username": "mia_r", "password": PASSWORD}, headers=H
    )
    assert old.status_code == 401


def test_a2_wrong_current_password_is_403_and_rate_limited(app):
    me = _signup(app, "mia_r")
    for _ in range(5):
        r = me.post(
            "/api/account/password",
            json={"current_password": "not my password", "new_password": NEW},
            headers=H,
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "Current password is wrong."
    r = me.post(
        "/api/account/password",
        json={"current_password": PASSWORD, "new_password": NEW},
        headers=H,
    )
    assert r.status_code == 429
    _login(app, "mia_r")  # nothing changed


def test_a2_new_password_follows_the_rules(app):
    me = _signup(app, "mia_r")
    r = me.post(
        "/api/account/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=H,
    )
    assert r.status_code == 422


# --- A3 ----------------------------------------------------------------------------------------


def test_a3_sign_out_other_devices(app):
    me = _signup(app, "mia_r")
    a, b = _login(app, "mia_r"), _login(app, "mia_r")
    r = me.post("/api/account/sign-out-others", headers=H)
    assert r.status_code == 200
    assert r.json() == {"signed_out": 2}
    assert a.get("/api/account").status_code == 401
    assert b.get("/api/account").status_code == 401
    assert me.get("/api/account").json()["sessions"] == 1


# --- A4 ----------------------------------------------------------------------------------------


def _delete(client, password=PASSWORD, confirm="player_one"):
    return client.post(
        "/api/account/delete",
        json={"password": password, "confirm_username": confirm},
        headers=H,
    )


def test_a4_delete_needs_the_password_and_the_username(app):
    _, player, _ = _campaign_with_player(app)
    assert _delete(player, password="not my password").status_code == 403
    assert _delete(player, confirm="someone_else").status_code == 422
    assert player.get("/api/account").status_code == 200


def test_a4_a_dm_must_delete_their_campaigns_first(app):
    dm, _, cid = _campaign_with_player(app)
    r = _delete(dm, confirm="dm_dana")
    assert r.status_code == 409
    assert "DM of 1 campaign" in r.json()["detail"]
    assert dm.delete(f"/api/campaigns/{cid}", headers=H).status_code == 204
    assert _delete(dm, confirm="dm_dana").status_code == 204


def test_a4_delete_removes_private_text_and_keeps_shared_entries(app, hub):
    dm, player, cid = _campaign_with_player(app)
    chid = player.post(
        f"/api/campaigns/{cid}/characters", json={"name": "Brin"}, headers=H
    ).json()["id"]

    def entry(visibility, title):
        r = player.post(
            f"/api/campaigns/{cid}/entries",
            json={
                "board": "character",
                "character_id": chid,
                "title": title,
                "body": f"{title} text",
                "visibility": visibility,
            },
            headers=H,
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    secret = entry("private", "Only mine")
    shared = entry("dm", "For the DM")
    party = entry("party", "For everyone")
    tid = player.post(f"/api/campaigns/{cid}/chats", json={"title": "q"}, headers=H).json()["id"]
    player.post(f"/api/campaigns/{cid}/chats/{tid}/messages", json={"text": "hi"}, headers=H)

    r = _delete(player)
    assert r.status_code == 204
    assert player.get("/api/account").status_code == 401  # cookie cleared, session gone

    with hub.storage.campaign(cid) as db:
        ids = {row["id"] for row in db.execute("SELECT id FROM entries")}
        assert secret not in ids
        assert {shared, party} <= ids
        assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM chunks WHERE entry_id = ?", (secret,)).fetchone()[
            0
        ] == 0
        assert db.execute("SELECT count(*) FROM characters").fetchone()[0] == 1
    members = dm.get(f"/api/campaigns/{cid}/members").json()
    assert [m["username"] for m in members] == ["dm_dana"]
    assert dm.get(f"/api/campaigns/{cid}/entries/{shared}").status_code == 200

    gone = TestClient(app).post(
        "/api/auth/login", json={"username": "player_one", "password": PASSWORD}, headers=H
    )
    assert gone.status_code == 401
    again = _signup(app, "player_one")  # the name is free again ...
    assert again.get(f"/api/campaigns/{cid}").status_code == 404  # ... but not the old access


def test_a4_delete_also_cleans_campaigns_the_user_already_left(app, hub):
    dm, player, cid = _campaign_with_player(app)
    secret = player.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "journal", "title": "Mine", "body": "private", "visibility": "private"},
        headers=H,
    ).json()["id"]
    me = player.get("/api/account").json()
    uid = next(
        m["user_id"]
        for m in dm.get(f"/api/campaigns/{cid}/members").json()
        if m["username"] == me["username"]
    )
    assert player.delete(f"/api/campaigns/{cid}/members/{uid}", headers=H).status_code == 204
    assert _delete(player).status_code == 204
    with hub.storage.campaign(cid) as db:
        assert db.execute("SELECT 1 FROM entries WHERE id = ?", (secret,)).fetchone() is None
