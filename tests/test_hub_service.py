"""Hub service (docs/ARCHITECTURE.md › M6 contract): accounts, campaigns, boards, visibility.

Behaviour numbers refer to the M6 contract. Everything runs on a temp folder with a fake
embedder; no model, network or API key.
"""

import sqlite3

import pytest
from fake_embedder import FailingEmbedder
from hub_support import PASSWORD, Clock, make_hub, party

from dungeonmind.hub import models
from dungeonmind.hub.errors import (
    AuthFailed,
    Conflict,
    Forbidden,
    HubError,
    Invalid,
    NotFound,
    RateLimited,
)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


# --- accounts (6, 8) --------------------------------------------------------------------------


def test_register_login_and_resolve_session(hub):
    user = hub.register("mia_r", PASSWORD)
    token = hub.login("mia_r", PASSWORD)
    assert hub.user_for_token(token) == user
    assert hub.user_for_token("not-a-token") is None


@pytest.mark.parametrize("name", ["ab", "Mia", "mia r", "m" * 33, "../x", "mia\x00"])
def test_register_rejects_bad_usernames(hub, name):
    with pytest.raises(Invalid):
        hub.register(name, PASSWORD)


@pytest.mark.parametrize("password", ["short", "x" * 201])
def test_register_rejects_bad_passwords(hub, password):
    with pytest.raises(Invalid):
        hub.register("mia_r", password)


def test_register_duplicate_username_conflicts(hub):
    hub.register("mia_r", PASSWORD)
    with pytest.raises(Conflict):
        hub.register("mia_r", PASSWORD)


def test_8_unknown_user_and_wrong_password_fail_the_same_way(hub):
    hub.register("mia_r", PASSWORD)
    with pytest.raises(AuthFailed) as a:
        hub.login("mia_r", "wrong password!")
    with pytest.raises(AuthFailed) as b:
        hub.login("nobody_here", PASSWORD)
    assert str(a.value) == str(b.value)


def test_8_login_is_rate_limited_after_five_failures_per_username(tmp_path):
    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    hub.register("mia_r", PASSWORD)
    for _ in range(5):
        with pytest.raises(AuthFailed):
            hub.login("mia_r", "wrong password!")
    with pytest.raises(RateLimited):
        hub.login("mia_r", PASSWORD)  # even the right password, until the window passes
    clock.advance(minutes=16)
    assert hub.login("mia_r", PASSWORD)


def test_6_sessions_expire_after_14_days_and_logout_ends_them(tmp_path):
    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    user = hub.register("mia_r", PASSWORD)
    old = hub.login("mia_r", PASSWORD)
    clock.advance(days=14, seconds=1)
    assert hub.user_for_token(old) is None
    fresh = hub.login("mia_r", PASSWORD)
    assert hub.user_for_token(fresh) == user
    hub.logout(fresh)
    assert hub.user_for_token(fresh) is None


def test_6_session_tokens_and_passwords_are_not_stored_in_plain_text(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("mia_r", PASSWORD)
    token = hub.login("mia_r", PASSWORD)
    raw = (tmp_path / "app.db").read_bytes()
    assert token.encode() not in raw
    assert PASSWORD.encode() not in raw


def test_reset_password_replaces_it_and_ends_sessions(hub):
    user = hub.register("mia_r", PASSWORD)
    token = hub.login("mia_r", PASSWORD)
    hub.reset_password("mia_r", "a brand new password")
    assert hub.user_for_token(token) is None
    with pytest.raises(AuthFailed):
        hub.login("mia_r", PASSWORD)
    assert hub.user_for_token(hub.login("mia_r", "a brand new password")) == user


# --- campaigns, invites, members --------------------------------------------------------------


def test_creator_is_dm_and_campaign_has_its_own_folder(hub, tmp_path):
    dm = hub.register("dm_dana", PASSWORD)
    camp = hub.create_campaign(dm, "The Ashvale Saga")
    assert camp.role == "dm" and camp.dm_id == dm.id
    assert len(camp.id) == 32 and all(c in "0123456789abcdef" for c in camp.id)
    assert (tmp_path / "campaigns" / camp.id / "campaign.db").is_file()
    assert [c.id for c in hub.campaigns(dm)] == [camp.id]


def test_campaign_name_is_validated(hub):
    dm = hub.register("dm_dana", PASSWORD)
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(Invalid):
            hub.create_campaign(dm, bad)


def test_invites_join_players_once_and_expire(tmp_path):
    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    dm, p1, p2 = (hub.register(n, PASSWORD) for n in ("dm_dana", "player_one", "player_two"))
    camp = hub.create_campaign(dm, "Ashvale")
    invite = hub.create_invite(dm, camp.id)
    assert len(invite.code) == 12
    joined = hub.accept_invite(p1, invite.code)
    assert joined.id == camp.id and joined.role == "player"
    with pytest.raises(NotFound):  # single use
        hub.accept_invite(p2, invite.code)
    late = hub.create_invite(dm, camp.id)
    clock.advance(days=7, seconds=1)
    with pytest.raises(NotFound):
        hub.accept_invite(p2, late.code)


def test_revoked_invite_cannot_be_used(hub):
    dm, p1 = hub.register("dm_dana", PASSWORD), hub.register("player_one", PASSWORD)
    camp = hub.create_campaign(dm, "Ashvale")
    invite = hub.create_invite(dm, camp.id)
    hub.revoke_invite(dm, camp.id, invite.code)
    with pytest.raises(NotFound):
        hub.accept_invite(p1, invite.code)


def test_existing_member_accepting_an_invite_conflicts(hub):
    camp, dm, p1, *_ = party(hub)
    with pytest.raises(Conflict):
        hub.accept_invite(p1, hub.create_invite(dm, camp.id).code)


def test_only_the_dm_invites_removes_or_deletes(hub):
    camp, dm, p1, p2, *_ = party(hub)
    with pytest.raises(Forbidden):
        hub.create_invite(p1, camp.id)
    with pytest.raises(Forbidden):
        hub.remove_member(p1, camp.id, p2.id)
    with pytest.raises(Forbidden):
        hub.delete_campaign(p1, camp.id)


def test_players_can_leave_but_the_dm_cannot(hub):
    camp, dm, p1, *_ = party(hub)
    hub.remove_member(p1, camp.id, p1.id)
    with pytest.raises(NotFound):
        hub.campaign(p1, camp.id)
    with pytest.raises(Forbidden):
        hub.remove_member(dm, camp.id, dm.id)


def test_members_lists_roles(hub):
    camp, dm, p1, p2, p3, _ = party(hub)
    members = {m.username: m.role for m in hub.members(p1, camp.id)}
    assert members == {
        "dm_dana": "dm",
        "player_one": "player",
        "player_two": "player",
        "player_gone": "player",
    }


def test_2_non_members_get_not_found_for_everything(hub):
    camp, dm, p1, p2, p3, outsider = party(hub)
    eid = hub.create_entry(dm, camp.id, board="journal", title="t", body="b").id
    calls = [
        lambda: hub.campaign(outsider, camp.id),
        lambda: hub.members(outsider, camp.id),
        lambda: hub.entries(outsider, camp.id),
        lambda: hub.entry(outsider, camp.id, eid),
        lambda: hub.search(outsider, camp.id, "anything"),
        lambda: hub.characters(outsider, camp.id),
        lambda: hub.threads(outsider, camp.id),
        lambda: hub.create_thread(outsider, camp.id),
        lambda: hub.create_entry(outsider, camp.id, board="journal", title="t", body="b"),
        lambda: hub.delete_campaign(outsider, camp.id),
        lambda: hub.create_invite(outsider, camp.id),
    ]
    for call in calls:
        with pytest.raises(NotFound):
            call()
    assert hub.campaigns(outsider) == []


@pytest.mark.parametrize(
    "cid", ["../x", "a/b", "/etc", "", "A" * 32, "g" * 32, "0" * 31, "0" * 33, "0" * 31 + "\x00"]
)
def test_5_malformed_campaign_ids_are_not_found_without_touching_disk(hub, tmp_path, cid):
    user = hub.register("mia_r", PASSWORD)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    with pytest.raises(NotFound):
        hub.campaign(user, cid)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_5_symlinked_campaign_folder_is_refused(hub, tmp_path):
    camp, dm, *_ = party(hub)
    folder = tmp_path / "campaigns" / camp.id
    elsewhere = tmp_path / "elsewhere"
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(HubError):
        hub.create_entry(dm, camp.id, board="journal", title="t", body="b")


def test_delete_campaign_removes_its_folder_and_memberships(hub, tmp_path):
    camp, dm, p1, *_ = party(hub)
    hub.delete_campaign(dm, camp.id)
    assert not (tmp_path / "campaigns" / camp.id).exists()
    assert hub.campaigns(p1) == []
    with pytest.raises(NotFound):
        hub.campaign(dm, camp.id)


# --- boards (B1–B3) ---------------------------------------------------------------------------


def test_b1_only_the_dm_writes_lore(hub):
    camp, dm, p1, *_ = party(hub)
    lore = hub.create_entry(dm, camp.id, board="lore", title="Elara", body="Spymaster.", kind="npc")
    assert lore.board == "lore" and lore.kind == "npc"
    with pytest.raises(Forbidden):
        hub.create_entry(p1, camp.id, board="lore", title="Fake canon", body="x")


def test_b1_kind_must_belong_to_board(hub):
    camp, dm, *_ = party(hub)
    with pytest.raises(Invalid):
        hub.create_entry(dm, camp.id, board="lore", title="t", body="b", kind="backstory")
    with pytest.raises(Invalid):
        hub.create_entry(dm, camp.id, board="journal", title="t", body="b", kind="npc")
    with pytest.raises(Invalid):
        hub.create_entry(dm, camp.id, board="nonsense", title="t", body="b")


def test_b1_character_entries_need_own_character(hub):
    camp, dm, p1, p2, *_ = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn", "A half-orc ranger")
    entry = hub.create_entry(
        p1,
        camp.id,
        board="character",
        character_id=thorn.id,
        title="Raised by wolves",
        body="...",
        kind="backstory",
    )
    assert entry.character_id == thorn.id
    with pytest.raises(Forbidden):  # someone else's character board
        hub.create_entry(p2, camp.id, board="character", character_id=thorn.id, title="t", body="b")
    with pytest.raises(Invalid):  # character board needs a character
        hub.create_entry(p1, camp.id, board="character", title="t", body="b")
    with pytest.raises(Invalid):  # other boards must not have one
        hub.create_entry(p1, camp.id, board="journal", character_id=thorn.id, title="t", body="b")
    with pytest.raises(NotFound):
        hub.create_entry(p1, camp.id, board="character", character_id=999, title="t", body="b")


def test_b2_defaults_and_lore_visibility_rules(hub):
    camp, dm, p1, *_ = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn")
    lore = hub.create_entry(dm, camp.id, board="lore", title="t", body="b")
    char = hub.create_entry(
        p1, camp.id, board="character", character_id=thorn.id, title="t", body="b"
    )
    journal = hub.create_entry(p1, camp.id, board="journal", title="t", body="b")
    assert (lore.visibility, char.visibility, journal.visibility) == ("private", "dm", "party")
    as_dm = hub.create_entry(dm, camp.id, board="lore", title="t", body="b", visibility="dm")
    assert as_dm.visibility == "private"
    dm_journal = hub.create_entry(
        dm, camp.id, board="journal", title="t", body="b", visibility="dm"
    )
    assert dm_journal.visibility == "private"
    with pytest.raises(Invalid):
        hub.create_entry(p1, camp.id, board="journal", title="t", body="b", visibility="public")


def test_b3_character_with_entries_cannot_be_deleted(hub):
    camp, dm, p1, *_ = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn")
    e = hub.create_entry(p1, camp.id, board="character", character_id=thorn.id, title="t", body="b")
    with pytest.raises(Conflict, match="1"):
        hub.delete_character(p1, camp.id, thorn.id)
    hub.delete_entry(p1, camp.id, e.id)
    hub.delete_character(p1, camp.id, thorn.id)
    assert hub.characters(p1, camp.id) == []


def test_characters_are_visible_to_all_members_but_edited_by_owner(hub):
    camp, dm, p1, p2, *_ = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn", "Ranger")
    assert [c.name for c in hub.characters(p2, camp.id)] == ["Thorn"]
    with pytest.raises(Forbidden):
        hub.update_character(p2, camp.id, thorn.id, name="Mine now")
    with pytest.raises(Forbidden):
        hub.update_character(dm, camp.id, thorn.id, name="DM edit")
    assert hub.update_character(p1, camp.id, thorn.id, summary="Wolf-raised").summary == (
        "Wolf-raised"
    )
    with pytest.raises(Invalid):
        hub.create_character(p1, camp.id, "x" * 61)


# --- 3. visibility matrix ---------------------------------------------------------------------


def _readable(viewer, viewer_is_dm, entry) -> bool:
    if entry.visibility == "party":
        return True
    if entry.author_id == viewer.id:
        return True
    return entry.visibility == "dm" and viewer_is_dm


@pytest.fixture
def world(hub):
    camp, dm, p1, p2, p3, outsider = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn")
    made = []

    def add(author, board, visibility, **kw):
        token = f"tok{len(made)}"
        e = hub.create_entry(
            author,
            camp.id,
            board=board,
            visibility=visibility,
            title=f"Entry {token}",
            body=f"shared words {token}",
            **kw,
        )
        made.append(e)

    add(dm, "lore", "private", kind="npc")
    add(dm, "lore", "party", kind="location")
    add(dm, "journal", "private")
    add(dm, "journal", "dm")  # stored as private: the DM already reads their own entries
    add(dm, "journal", "party")
    for author in (p1, p2, p3):
        for vis in ("private", "dm", "party"):
            add(author, "journal", vis)
    for vis in ("private", "dm", "party"):
        add(p1, "character", vis, character_id=thorn.id)
    hub.remove_member(dm, camp.id, p3.id)  # p3's entries stay, p3 loses access
    return hub, camp, {"dm": dm, "p1": p1, "p2": p2, "p3": p3, "outsider": outsider}, made


@pytest.mark.parametrize("viewer_name", ["dm", "p1", "p2"])
def test_3_members_list_get_and_search_exactly_what_they_may_read(world, viewer_name):
    hub, camp, users, made = world
    viewer = users[viewer_name]
    expected = {e.id for e in made if _readable(viewer, viewer_name == "dm", e)}

    assert {e.id for e in hub.entries(viewer, camp.id)} == expected
    for e in made:
        if e.id in expected:
            assert hub.entry(viewer, camp.id, e.id).id == e.id
        else:
            with pytest.raises(NotFound):
                hub.entry(viewer, camp.id, e.id)
    hits = hub.search(viewer, camp.id, "shared words", k=50)
    assert {h.entry_id for h in hits} == expected


@pytest.mark.parametrize("viewer_name", ["dm", "p1", "p2"])
def test_3_chat_engine_receives_exactly_the_readable_entries(world, viewer_name):
    hub, camp, users, made = world
    viewer = users[viewer_name]
    expected = {e.id for e in made if _readable(viewer, viewer_name == "dm", e)}
    got = []

    def spy(question, search_notes, store, conversation_id):
        got.extend(h.entry_id for h in search_notes("shared words", 50))
        return {
            "answer": "ok",
            "citations": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "stop_reason": "completed",
            "trace": [],
        }

    hub.engine = spy
    hub.ask(viewer, camp.id, hub.create_thread(viewer, camp.id).id, "anything")
    assert set(got) == expected


def test_3_dm_cannot_read_players_private_entries(world):
    hub, camp, users, made = world
    private_by_players = [
        e for e in made if e.visibility == "private" and e.author_id != users["dm"].id
    ]
    assert private_by_players
    visible = {e.id for e in hub.entries(users["dm"], camp.id)}
    assert not visible & {e.id for e in private_by_players}


@pytest.mark.parametrize("viewer_name", ["p3", "outsider"])
def test_3_removed_members_and_outsiders_see_nothing(world, viewer_name):
    hub, camp, users, made = world
    viewer = users[viewer_name]
    with pytest.raises(NotFound):
        hub.entries(viewer, camp.id)
    with pytest.raises(NotFound):
        hub.search(viewer, camp.id, "shared words")
    with pytest.raises(NotFound):
        hub.entry(viewer, camp.id, made[3].id)  # even a party entry


def test_3_filters_by_board_character_and_session(world):
    hub, camp, users, made = world
    dm = users["dm"]
    assert {e.board for e in hub.entries(dm, camp.id, board="lore")} == {"lore"}
    chars = hub.entries(users["p1"], camp.id, board="character")
    assert chars and all(e.character_id is not None for e in chars)
    s3 = hub.create_entry(dm, camp.id, board="journal", title="S3", body="b", session=3)
    assert [e.id for e in hub.entries(dm, camp.id, session=3)] == [s3.id]


def test_3_text_query_filters_listed_entries(world):
    hub, camp, users, made = world
    found = hub.entries(users["dm"], camp.id, q="TOK0")  # case-insensitive, title or body
    assert [e.id for e in found] == [made[0].id]
    assert hub.entries(users["p1"], camp.id, q="tok0") == []  # still visibility-filtered


def test_visibility_change_applies_on_the_next_request(world):
    hub, camp, users, made = world
    secret = made[0]  # DM-private lore
    assert secret.id not in {e.id for e in hub.entries(users["p1"], camp.id)}
    hub.update_entry(users["dm"], camp.id, secret.id, visibility="party")
    assert secret.id in {e.id for e in hub.entries(users["p1"], camp.id)}
    assert secret.id in {h.entry_id for h in hub.search(users["p1"], camp.id, "tok0")}


def test_only_authors_edit_or_delete_and_cannot_see_means_not_found(world):
    hub, camp, users, made = world
    p1_party = next(
        e
        for e in made
        if e.author_id == users["p1"].id and e.visibility == "party" and e.board == "journal"
    )
    p1_private = next(
        e for e in made if e.author_id == users["p1"].id and e.visibility == "private"
    )
    with pytest.raises(Forbidden):
        hub.update_entry(users["p2"], camp.id, p1_party.id, title="hijack")
    with pytest.raises(Forbidden):
        hub.delete_entry(users["dm"], camp.id, p1_party.id)
    with pytest.raises(NotFound):
        hub.update_entry(users["p2"], camp.id, p1_private.id, title="hijack")
    updated = hub.update_entry(users["p1"], camp.id, p1_party.id, title="Renamed", session=4)
    assert updated.title == "Renamed" and updated.session == 4
    hub.delete_entry(users["p1"], camp.id, p1_party.id)
    with pytest.raises(NotFound):
        hub.entry(users["p1"], camp.id, p1_party.id)


def test_update_rejects_unknown_or_immutable_fields(world):
    hub, camp, users, made = world
    e = made[3]  # DM party journal
    for change in ({"author_id": users["p1"].id}, {"board": "lore"}, {"id": 99}, {"nope": 1}):
        with pytest.raises(Invalid):
            hub.update_entry(users["dm"], camp.id, e.id, **change)


# --- 5. cross-campaign isolation --------------------------------------------------------------


def test_5_campaigns_never_see_each_others_entries(hub, tmp_path):
    dm = hub.register("dm_dana", PASSWORD)
    a = hub.create_campaign(dm, "Ashvale")
    b = hub.create_campaign(dm, "Greywater")
    hub.create_entry(
        dm,
        a.id,
        board="lore",
        title="Elara",
        body="Elara is loyal to the duke.",
        visibility="party",
    )
    hub.create_entry(
        dm, b.id, board="lore", title="Elara", body="Elara betrayed the duke.", visibility="party"
    )
    for _ in range(2):
        hits_a = hub.search(dm, a.id, "Is Elara loyal to the duke?", k=50)
        hits_b = hub.search(dm, b.id, "Is Elara loyal to the duke?", k=50)
        # Each campaign answers only from its own notes, never the other's.
        assert hits_a and all("betrayed" not in h.text and "again" not in h.text for h in hits_a)
        assert hits_b and all("loyal" not in h.text for h in hits_b)
        hub.create_entry(dm, b.id, board="journal", title="More", body="Elara again, duke.")
    loyal = hub.entries(dm, a.id, q="loyal")[0]
    hub.update_entry(dm, a.id, loyal.id, body="Elara is loyal, and the duke trusts her.")
    for e in hub.entries(dm, b.id):
        hub.delete_entry(dm, b.id, e.id)
    assert hub.search(dm, b.id, "Elara duke") == []
    assert all("trusts" in h.text for h in hub.search(dm, a.id, "Elara duke", k=50))
    raw_a = (tmp_path / "campaigns" / a.id / "campaign.db").read_bytes()
    assert b"betrayed" not in raw_a


# --- 10–12. entries: storage, limits, search --------------------------------------------------


def test_10_failed_embedding_saves_nothing(tmp_path):
    hub = make_hub(tmp_path, embedder=FailingEmbedder())
    camp, dm, *_ = party(hub)
    with pytest.raises(HubError) as err:
        hub.create_entry(dm, camp.id, board="journal", title="t", body="b")
    assert "/secret/path" not in str(err.value)
    assert make_hub(tmp_path).entries(dm, camp.id) == []


def test_10_edit_rechunks_so_old_text_is_no_longer_found(hub):
    camp, dm, *_ = party(hub)
    e = hub.create_entry(dm, camp.id, board="journal", title="Rumour", body="dragon in the well")
    hub.update_entry(dm, camp.id, e.id, body="goblins in the mill")
    hits = hub.search(dm, camp.id, "dragon well", k=50)
    assert all("dragon" not in h.text for h in hits)
    assert any("goblins" in h.text for h in hub.search(dm, camp.id, "goblins mill"))


def test_10_deleted_entries_are_never_returned(hub):
    camp, dm, *_ = party(hub)
    e = hub.create_entry(dm, camp.id, board="journal", title="Gone", body="unique zebra")
    hub.delete_entry(dm, camp.id, e.id)
    assert hub.search(dm, camp.id, "unique zebra", k=50) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"title": ""},
        {"title": "x" * 121},
        {"body": ""},
        {"body": "x" * 10_001},
        {"session": 0},
        {"session": 1000},
    ],
)
def test_11_entry_limits(hub, fields):
    camp, dm, *_ = party(hub)
    args = {"board": "journal", "title": "t", "body": "b", **fields}
    with pytest.raises(Invalid):
        hub.create_entry(dm, camp.id, **args)


def test_11_entries_per_campaign_are_capped(hub, monkeypatch):
    camp, dm, *_ = party(hub)
    monkeypatch.setattr(models, "MAX_ENTRIES", 2)
    hub.create_entry(dm, camp.id, board="journal", title="1", body="b")
    hub.create_entry(dm, camp.id, board="journal", title="2", body="b")
    with pytest.raises(Invalid, match="2"):
        hub.create_entry(dm, camp.id, board="journal", title="3", body="b")


def test_11_members_per_campaign_are_capped(hub, monkeypatch):
    camp, dm, *_ = party(hub)  # dm + 3 players
    monkeypatch.setattr(models, "MAX_MEMBERS", 4)
    late = hub.register("late_comer", PASSWORD)
    with pytest.raises(Invalid):
        hub.accept_invite(late, hub.create_invite(dm, camp.id).code)


def test_12_long_entries_are_chunked_and_best_chunk_is_returned(hub):
    camp, dm, *_ = party(hub)
    paragraphs = [f"Paragraph {i} about weather and crops. " * 20 for i in range(6)]
    paragraphs[4] = "The lighthouse keeper hides the silver key under the third stair."
    e = hub.create_entry(dm, camp.id, board="journal", title="Long", body="\n\n".join(paragraphs))
    hits = hub.search(dm, camp.id, "where is the silver key hidden", k=3)
    assert hits[0].entry_id == e.id
    assert "silver key" in hits[0].text
    assert len(hits[0].text) <= 1500
    assert len({h.entry_id for h in hits}) == len(hits)  # one hit per entry


def test_12_hits_carry_source_labels_and_canon_flag(hub):
    camp, dm, p1, *_ = party(hub)
    thorn = hub.create_character(p1, camp.id, "Thorn")
    hub.create_entry(
        dm, camp.id, board="lore", kind="npc", title="Elara", body="Elara owl", visibility="party"
    )
    hub.create_entry(
        p1,
        camp.id,
        board="character",
        character_id=thorn.id,
        kind="backstory",
        title="Wolves",
        body="Elara owl",
        visibility="party",
    )
    hub.create_entry(
        p1, camp.id, board="journal", title="Session recap", body="Elara owl", session=3
    )
    hits = {h.source: h for h in hub.search(p1, camp.id, "Elara owl", k=10)}
    assert hits["Lore › NPC › Elara"].canon is True
    assert hits["Thorn's board › Backstory › Wolves"].canon is False
    assert "Journal › Session recap (player_one, session 3)" in hits


def test_12_empty_campaign_search_returns_nothing(hub):
    camp, dm, *_ = party(hub)
    assert hub.search(dm, camp.id, "anything") == []


def test_12_k_is_bounded(hub):
    camp, dm, *_ = party(hub)
    for bad in (0, 51):
        with pytest.raises(Invalid):
            hub.search(dm, camp.id, "x", k=bad)


def test_campaign_db_uses_wal(hub, tmp_path):
    camp, *_ = party(hub)
    db = sqlite3.connect(tmp_path / "campaigns" / camp.id / "campaign.db")
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# --- chunking ---------------------------------------------------------------------------------


def test_chunking_packs_paragraphs_and_splits_long_ones_at_sentences_then_hard_wraps():
    from dungeonmind.hub.entries import CHUNK_CHARS, chunk_body

    short = chunk_body("One.\n\nTwo.\n\n\n  Three.  ")
    assert short == ["One.\n\nTwo.\n\nThree."]

    sentences = " ".join(f"Sentence number {i} is here." for i in range(200))
    chunks = chunk_body(sentences)
    assert len(chunks) > 1 and all(len(c) <= CHUNK_CHARS for c in chunks)
    assert all(c.endswith(".") for c in chunks)  # split at sentence ends
    assert " ".join(chunks) == sentences

    unbroken = "x" * (CHUNK_CHARS * 2 + 10)
    wrapped = chunk_body(f"Intro. {unbroken} Outro.")
    assert all(len(c) <= CHUNK_CHARS for c in wrapped)
    assert "".join(wrapped).replace(" ", "") == f"Intro.{unbroken}Outro."
