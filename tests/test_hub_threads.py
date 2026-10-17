"""M9c CT1–CT2: renaming and deleting chat threads."""

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.api import CSRF_HEADER, create_app

H = {CSRF_HEADER: "1"}


def _answer(text="ok"):
    return {
        "answer": text,
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
        "stop_reason": "completed",
        "trace": [],
    }


class Engine:
    """Answers, optionally running `during` first (as if something happened mid-answer)."""

    def __init__(self) -> None:
        self.during = None

    def __call__(self, question, search_notes, store, conversation_id, history=()):
        if self.during:
            self.during()
        return _answer()


@pytest.fixture
def engine():
    return Engine()


@pytest.fixture
def hub(tmp_path, engine):
    return make_hub(tmp_path, engine=engine)


@pytest.fixture
def party(hub):
    app = create_app(hub)

    def signup(name):
        c = TestClient(app)
        r = c.post("/api/auth/register", json={"username": name, "password": PASSWORD}, headers=H)
        assert r.status_code == 201
        return c

    dm, player = signup("dm_dana"), signup("player_one")
    cid = dm.post("/api/campaigns", json={"name": "Ashvale"}, headers=H).json()["id"]
    code = dm.post(f"/api/campaigns/{cid}/invites", headers=H).json()["code"]
    player.post("/api/invites/accept", json={"code": code}, headers=H)
    return dm, player, cid


def _thread(client, cid, title="Rules"):
    return client.post(f"/api/campaigns/{cid}/chats", json={"title": title}, headers=H).json()["id"]


def test_ct1_owner_renames_a_thread(party):
    dm, _, cid = party
    tid = _thread(dm, cid)
    r = dm.patch(f"/api/campaigns/{cid}/chats/{tid}", json={"title": "Grappling"}, headers=H)
    assert r.status_code == 200
    assert r.json()["title"] == "Grappling"
    assert [t["title"] for t in dm.get(f"/api/campaigns/{cid}/chats").json()] == ["Grappling"]


def test_ct1_rename_validates_the_title(party):
    dm, _, cid = party
    tid = _thread(dm, cid)
    for bad in ("", "x" * 121):
        r = dm.patch(f"/api/campaigns/{cid}/chats/{tid}", json={"title": bad}, headers=H)
        assert r.status_code == 422


def test_ct1_owner_deletes_a_thread_with_its_messages(party, hub):
    dm, _, cid = party
    tid = _thread(dm, cid)
    dm.post(f"/api/campaigns/{cid}/chats/{tid}/messages", json={"text": "hi"}, headers=H)
    assert dm.delete(f"/api/campaigns/{cid}/chats/{tid}", headers=H).status_code == 204
    assert dm.get(f"/api/campaigns/{cid}/chats/{tid}").status_code == 404
    with hub.storage.campaign(cid) as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_ct1_someone_elses_thread_is_404(party):
    dm, player, cid = party
    tid = _thread(dm, cid)
    path = f"/api/campaigns/{cid}/chats/{tid}"
    assert player.patch(path, json={"title": "mine now"}, headers=H).status_code == 404
    assert player.delete(path, headers=H).status_code == 404
    assert dm.get(path).json()["thread"]["title"] == "Rules"


def test_ct2_a_thread_deleted_mid_answer_is_404_not_500(party, engine, hub):
    dm, _, cid = party
    tid = _thread(dm, cid)
    engine.during = lambda: dm.delete(f"/api/campaigns/{cid}/chats/{tid}", headers=H)
    r = dm.post(f"/api/campaigns/{cid}/chats/{tid}/messages", json={"text": "hi"}, headers=H)
    assert r.status_code == 404
    assert r.json()["detail"] == "Chat not found."
    assert dm.get("/api/account").json()["questions_today"] == 1
    with hub.storage.campaign(cid) as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
