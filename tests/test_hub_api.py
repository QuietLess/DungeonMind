"""Hub HTTP API (M6 contract behaviours 1, 2, 6, 7, 11, 22), via FastAPI's TestClient."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, SESSION_COOKIE, create_app

H = {CSRF_HEADER: "1"}


def _ok_engine(question, search_notes, store, conversation_id, history=()):
    return {
        "answer": f"echo: {question}",
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
        "stop_reason": "completed",
        "trace": [],
    }


@pytest.fixture
def app(tmp_path):
    return create_app(make_hub(tmp_path, engine=_ok_engine))


def _client(app) -> TestClient:
    return TestClient(app)


def _signup(app, username) -> TestClient:
    c = _client(app)
    r = c.post("/api/auth/register", json={"username": username, "password": PASSWORD}, headers=H)
    assert r.status_code == 201, r.text
    return c


@pytest.fixture
def table(app):
    """DM and player in one campaign, plus an outsider; each with their own logged-in client."""
    dm = _signup(app, "dm_dana")
    player = _signup(app, "player_one")
    outsider = _signup(app, "outsider")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
    assert player.post("/api/invites/accept", json={"code": code}, headers=H).status_code == 200
    return dm, player, outsider, cid


# --- 1. every route needs a session -----------------------------------------------------------

PUBLIC = {"/api/auth/register", "/api/auth/login", "/api/health"}  # health: M9a O1
FILL = {"cid": "0" * 32, "eid": "1", "chid": "1", "tid": "1", "uid": "u"}


def test_1_every_route_except_register_and_login_requires_a_session(app):
    # The OpenAPI table lists every route, however FastAPI stores included routers internally.
    client = _client(app)
    paths = app.openapi()["paths"]
    assert sum(len(ops) for ops in paths.values()) >= 20
    for template, operations in paths.items():
        if template in PUBLIC:
            continue
        path = template
        for name, value in FILL.items():
            path = path.replace("{" + name + "}", value)
        assert "{" not in path, template
        for method in operations:
            r = client.request(method.upper(), path, headers=H, json={})
            assert r.status_code == 401, (method, template, r.status_code)


def test_1_a_bogus_cookie_is_not_a_session(app):
    client = _client(app)
    client.cookies.set(SESSION_COOKIE, "forged")
    assert client.get("/api/me").status_code == 401


# --- 6. sessions ------------------------------------------------------------------------------


def test_6_register_logs_in_with_a_hardened_cookie(app):
    client = _client(app)
    r = client.post(
        "/api/auth/register", json={"username": "mia_r", "password": PASSWORD}, headers=H
    )
    assert r.status_code == 201
    cookie = r.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE}=" in cookie
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # plain http on the local network
    assert client.get("/api/me").json()["username"] == "mia_r"
    assert "password" not in r.text


def test_6_login_logout(app):
    _signup(app, "mia_r")
    client = _client(app)
    bad = client.post(
        "/api/auth/login", json={"username": "mia_r", "password": "nope nope"}, headers=H
    )
    assert bad.status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "mia_r", "password": PASSWORD}, headers=H
        ).status_code
        == 200
    )
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout", headers=H).status_code == 204
    assert client.get("/api/me").status_code == 401


def test_8_login_rate_limit_is_429(app):
    _signup(app, "mia_r")
    client = _client(app)
    for _ in range(5):
        client.post(
            "/api/auth/login", json={"username": "mia_r", "password": "wrong pass"}, headers=H
        )
    r = client.post("/api/auth/login", json={"username": "mia_r", "password": PASSWORD}, headers=H)
    assert r.status_code == 429


# --- 7. CSRF header ---------------------------------------------------------------------------


def test_7_unsafe_methods_without_the_header_are_refused(table):
    dm, player, outsider, cid = table
    assert dm.post("/api/campaigns", json={"name": "x"}).status_code == 403
    assert dm.delete(f"/api/campaigns/{cid}").status_code == 403
    assert dm.get(f"/api/campaigns/{cid}").status_code == 200  # safe methods don't need it
    assert (
        _client(dm.app)
        .post("/api/auth/login", json={"username": "dm_dana", "password": PASSWORD})
        .status_code
        == 403
    )


# --- 2. not found vs forbidden ----------------------------------------------------------------


def test_2_outsiders_get_404_and_players_get_403_for_dm_actions(table):
    dm, player, outsider, cid = table
    assert outsider.get(f"/api/campaigns/{cid}").status_code == 404
    assert outsider.get(f"/api/campaigns/{cid}/entries").status_code == 404
    assert player.delete(f"/api/campaigns/{cid}", headers=H).status_code == 403
    assert player.post(f"/api/campaigns/{cid}/invites", headers=H).status_code == 403
    r = player.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "lore", "title": "t", "body": "b"},
        headers=H,
    )
    assert r.status_code == 403


def test_2_hidden_entries_are_404_for_players(table):
    dm, player, outsider, cid = table
    secret = dm.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "lore", "kind": "npc", "title": "Velkor", "body": "A vampire."},
        headers=H,
    )
    assert secret.status_code == 201, secret.text
    eid = secret.json()["id"]
    assert secret.json()["visibility"] == "private"
    assert player.get(f"/api/campaigns/{cid}/entries/{eid}").status_code == 404
    listed = player.get(f"/api/campaigns/{cid}/entries").json()
    assert all(e["id"] != eid for e in listed)
    assert (
        dm.patch(
            f"/api/campaigns/{cid}/entries/{eid}", json={"visibility": "party"}, headers=H
        ).status_code
        == 200
    )
    assert player.get(f"/api/campaigns/{cid}/entries/{eid}").json()["body"] == "A vampire."


def test_campaign_listing_and_members(table):
    dm, player, outsider, cid = table
    assert [c["id"] for c in player.get("/api/campaigns").json()] == [cid]
    assert player.get("/api/campaigns").json()[0]["role"] == "player"
    assert outsider.get("/api/campaigns").json() == []
    names = {m["username"]: m["role"] for m in dm.get(f"/api/campaigns/{cid}/members").json()}
    assert names == {"dm_dana": "dm", "player_one": "player"}


def test_characters_and_character_board_over_http(table):
    dm, player, outsider, cid = table
    r = player.post(f"/api/campaigns/{cid}/characters", json={"name": "Thorn"}, headers=H)
    assert r.status_code == 201
    chid = r.json()["id"]
    e = player.post(
        f"/api/campaigns/{cid}/entries",
        json={
            "board": "character",
            "character_id": chid,
            "kind": "backstory",
            "title": "Wolves",
            "body": "Raised by wolves.",
        },
        headers=H,
    )
    assert e.status_code == 201 and e.json()["visibility"] == "dm"
    assert dm.get(f"/api/campaigns/{cid}/entries/{e.json()['id']}").status_code == 200
    assert player.delete(f"/api/campaigns/{cid}/characters/{chid}", headers=H).status_code == 409


def test_chat_over_http(table):
    dm, player, outsider, cid = table
    t = player.post(f"/api/campaigns/{cid}/chats", json={"title": "Q"}, headers=H)
    assert t.status_code == 201
    tid = t.json()["id"]
    r = player.post(f"/api/campaigns/{cid}/chats/{tid}/messages", json={"text": "hello"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "echo: hello"
    history = player.get(f"/api/campaigns/{cid}/chats/{tid}").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert dm.get(f"/api/campaigns/{cid}/chats/{tid}").status_code == 404


def test_search_over_http(table):
    dm, player, outsider, cid = table
    dm.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "journal", "title": "Rumour", "body": "A dragon sleeps in the well"},
        headers=H,
    )
    hits = player.get(f"/api/campaigns/{cid}/search", params={"q": "dragon well"}).json()
    assert hits and hits[0]["source"].startswith("Journal")


# --- 11, 22. limits and error format ----------------------------------------------------------


def test_11_oversized_bodies_are_413(table):
    dm, player, outsider, cid = table
    r = dm.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "journal", "title": "t", "body": "x" * 70_000},
        headers=H,
    )
    assert r.status_code == 413


def test_22_validation_errors_are_plain_messages(table):
    dm, player, outsider, cid = table
    r = dm.post(
        f"/api/campaigns/{cid}/entries",
        json={"board": "journal", "title": "x" * 121, "body": "b"},
        headers=H,
    )
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str) and "120" in r.json()["detail"]
    r = dm.post(f"/api/campaigns/{cid}/entries", json={"board": "journal"}, headers=H)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = dm.post(
        f"/api/campaigns/{cid}/entries",
        content=b"not json",
        headers={**H, "content-type": "application/json"},
    )
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_22_unexpected_errors_hide_their_text(tmp_path):
    def exploding(question, search_notes, store, conversation_id, history=()):
        raise RuntimeError("boom /secret/path")

    hub = make_hub(tmp_path, engine=exploding)
    app = create_app(hub)

    def broken(*a, **k):
        raise RuntimeError("database at /secret/path is on fire")

    hub.campaigns = broken
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/api/auth/register", json={"username": "mia_r", "password": PASSWORD}, headers=H)
    r = client.get("/api/campaigns")
    assert r.status_code == 500
    assert r.json() == {"detail": "Something went wrong. Please try again."}


# --- F6: no secrets in URLs -------------------------------------------------------------------


def test_f6_no_route_takes_an_invite_code_in_its_path(app):
    assert not any("{code}" in path for path in app.openapi()["paths"])


def test_f6_invites_are_revoked_by_body(table):
    dm, player, outsider, cid = table
    code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
    r = dm.post(f"/api/campaigns/{cid}/invites/revoke", json={"code": code}, headers=H)
    assert r.status_code == 204
    assert outsider.post("/api/invites/accept", json={"code": code}, headers=H).status_code == 404


def test_f4_login_uses_the_client_address(table, monkeypatch):
    dm, player, outsider, cid = table
    hub = dm.app.state.hub
    seen = []
    real = hub.login
    monkeypatch.setattr(
        hub, "login", lambda u, p, client="local": seen.append(client) or real(u, p, client=client)
    )
    _client(dm.app).post(
        "/api/auth/login", json={"username": "dm_dana", "password": PASSWORD}, headers=H
    )
    assert seen == ["testclient"]
