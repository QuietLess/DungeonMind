"""M6 review fixes F1–F10 (docs/ARCHITECTURE.md › M6 review fixes): regression tests."""

import contextlib
import threading
import time

import pytest
from fake_embedder import HashEmbedder
from hub_support import PASSWORD, Clock, make_hub, party

from dungeonmind.hub import auth, models
from dungeonmind.hub.errors import AuthFailed, Conflict, Invalid, RateLimited
from dungeonmind.hub.models import User


class ConstantEmbedder(HashEmbedder):
    """Every text gets the same vector, so ranking can only come from BM25."""

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


# --- F1: concurrent edits ---------------------------------------------------------------------


def test_f1_concurrent_edit_conflicts_and_chunks_stay_consistent(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    camp, dm, *_ = party(hub)
    e = hub.create_entry(dm, camp.id, board="journal", title="Old title", body="Old body")
    original = hub._embed
    interleaved = []

    def embed_then_race(title, body):
        chunks = original(title, body)
        if not interleaved:  # while the first edit is embedding, a second edit lands
            interleaved.append(True)
            hub.update_entry(dm, camp.id, e.id, title="Other tab", body="The duke hides here.")
        return chunks

    monkeypatch.setattr(hub, "_embed", embed_then_race)
    with pytest.raises(Conflict):
        hub.update_entry(dm, camp.id, e.id, body="Elara lives in the tower.")
    stored = hub.entry(dm, camp.id, e.id)
    assert (stored.title, stored.body) == ("Other tab", "The duke hides here.")
    hits = hub.search(dm, camp.id, "Elara tower duke hides", k=5)
    assert [h.text for h in hits] == ["The duke hides here."]


def test_f1_sequential_edits_still_work(tmp_path):
    hub = make_hub(tmp_path)
    camp, dm, *_ = party(hub)
    e = hub.create_entry(dm, camp.id, board="journal", title="t", body="b")
    hub.update_entry(dm, camp.id, e.id, visibility="private")
    assert hub.update_entry(dm, camp.id, e.id, body="new body").body == "new body"


# --- F2: keyword stats come from visible chunks only ------------------------------------------


def _ranked(hub, viewer, cid, query):
    return [(h.entry_id, round(h.score, 12)) for h in hub.search(viewer, cid, query, k=10)]


def test_f2_hidden_entries_do_not_change_a_players_ranking(tmp_path):
    hub = make_hub(tmp_path, embedder=ConstantEmbedder())
    camp, dm, p1, *_ = party(hub)
    hub.create_entry(p1, camp.id, board="journal", title="A", body="vampire vampire crypt")
    hub.create_entry(p1, camp.id, board="journal", title="B", body="crypt lantern")
    hub.create_entry(p1, camp.id, board="journal", title="C", body="lantern rope")
    before = _ranked(hub, p1, camp.id, "vampire lantern crypt")
    for i in range(5):  # hidden DM lore full of the same words
        hub.create_entry(dm, camp.id, board="lore", title=f"S{i}", body="vampire crypt " * 5)
    assert _ranked(hub, p1, camp.id, "vampire lantern crypt") == before


def test_f2_keywords_rank_even_in_tiny_campaigns(tmp_path):
    hub = make_hub(tmp_path, embedder=ConstantEmbedder())
    camp, dm, *_ = party(hub)
    hub.create_entry(dm, camp.id, board="journal", title="Weather", body="Rain all week.")
    zebra = hub.create_entry(dm, camp.id, board="journal", title="Pet", body="A striped zebra.")
    assert hub.search(dm, camp.id, "zebra", k=2)[0].entry_id == zebra.id


def test_f2_search_at_scale_is_well_under_a_second(tmp_path):
    hub = make_hub(tmp_path)
    camp, dm, *_ = party(hub)
    words = "harvest baron vampire crypt lantern elara duke tower river guild".split()
    body = " ".join(words * 30)  # ~1,800 chars → 2 chunks
    for i in range(1_500):
        hub.create_entry(
            dm,
            camp.id,
            board="journal",
            title=f"Note {i} {words[i % 10]}",
            body=f"{body} unique{i}",
        )
    timings = []
    for _ in range(3):  # best of three: robust to a busy machine, still catches a regression
        started = time.perf_counter()
        hits = hub.search(dm, camp.id, "where is the vampire baron's crypt unique77", k=8)
        timings.append(time.perf_counter() - started)
    assert hits[0].text.endswith("unique77")
    assert min(timings) < 1.0, timings


# --- F3: search rate limit --------------------------------------------------------------------


def test_f3_searches_are_rate_limited_per_user(tmp_path):
    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    camp, dm, p1, *_ = party(hub)
    for _ in range(20):
        hub.search(p1, camp.id, "anything")
    with pytest.raises(RateLimited):
        hub.search(p1, camp.id, "anything")
    hub.search(dm, camp.id, "anything")  # other users unaffected
    clock.advance(seconds=61)
    hub.search(p1, camp.id, "anything")


# --- F4: login limits -------------------------------------------------------------------------


def test_f4_parallel_wrong_passwords_cannot_exceed_the_limit(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    hub.register("mia_r", PASSWORD)
    checks = []
    real = auth.verify_password

    def counting(password, stored):
        checks.append(1)
        time.sleep(0.01)  # widen the race window
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", counting)

    def attempt():
        with contextlib.suppress(AuthFailed, RateLimited):
            hub.login("mia_r", "wrong password!", client="10.0.0.9")

    threads = [threading.Thread(target=attempt) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(checks) <= 5


def test_f4_another_client_cannot_lock_a_user_out(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("dm_dana", PASSWORD)
    for _ in range(5):
        with pytest.raises(AuthFailed):
            hub.login("dm_dana", "wrong password!", client="10.6.6.6")
    with pytest.raises(RateLimited):
        hub.login("dm_dana", PASSWORD, client="10.6.6.6")
    assert hub.login("dm_dana", PASSWORD, client="10.0.0.2")


def test_f4_one_client_spreading_guesses_over_usernames_is_limited(tmp_path):
    hub = make_hub(tmp_path)
    for i in range(30):
        with pytest.raises(AuthFailed):
            hub.login(f"user_{i:03d}", "wrong password!", client="10.6.6.6")
    with pytest.raises(RateLimited):
        hub.login("user_999", "wrong password!", client="10.6.6.6")


def test_f4_malformed_usernames_fail_without_being_tracked(tmp_path):
    hub = make_hub(tmp_path)
    for name in ("x" * 5000, "Bad Name", ""):
        with pytest.raises(AuthFailed):
            hub.login(name, "whatever pass", client="10.0.0.3")
    assert all("x" * 5000 not in key for key in hub._limiter._failures)


def test_f4_limiter_table_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED_KEYS", 50)
    limiter = auth.Limiter()
    now = Clock()()
    for i in range(120):
        limiter.reserve([(f"key{i}", 5)], now)
    assert len(limiter._failures) <= 50


# --- F5: hashing cost and account limits ------------------------------------------------------


def test_f5_registrations_are_limited_per_client(tmp_path):
    hub = make_hub(tmp_path)
    for i in range(10):
        hub.register(f"user_{i:03d}", PASSWORD, client="10.0.0.4")
    with pytest.raises(RateLimited):
        hub.register("user_011", PASSWORD, client="10.0.0.4")
    hub.register("user_012", PASSWORD, client="10.0.0.5")


def test_f5_new_session_needs_no_second_password_check(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    user = hub.register("mia_r", PASSWORD)
    monkeypatch.setattr(auth, "verify_password", lambda *a: pytest.fail("verified again"))
    assert hub.user_for_token(hub.new_session(user)) == user


def test_f5_campaigns_per_user_are_capped(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    dm = hub.register("dm_dana", PASSWORD)
    monkeypatch.setattr(models, "MAX_CAMPAIGNS_PER_USER", 2)
    hub.create_campaign(dm, "One")
    hub.create_campaign(dm, "Two")
    with pytest.raises(Invalid):
        hub.create_campaign(dm, "Three")


# --- F7: sessions -----------------------------------------------------------------------------


def test_f7_expired_sessions_are_purged_on_login(tmp_path):
    import sqlite3

    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    hub.register("mia_r", PASSWORD)
    hub.login("mia_r", PASSWORD)
    clock.advance(days=15)
    hub.login("mia_r", PASSWORD)
    db = sqlite3.connect(tmp_path / "app.db")
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


# --- F8: failed runs refund the quota ---------------------------------------------------------


def test_f8_runs_that_fail_before_the_model_are_refunded_up_to_three_a_day(tmp_path):
    def failing(question, search_notes, store, conversation_id):
        raise RuntimeError("engine broke before calling the model")

    hub = make_hub(tmp_path, engine=failing, daily_message_limit=1)
    camp, dm, *_ = party(hub)
    thread = hub.create_thread(dm, camp.id)
    for _ in range(4):  # 1 allowed + 3 refunds
        assert hub.ask(dm, camp.id, thread.id, "hello").stop_reason == "error"
    with pytest.raises(RateLimited):
        hub.ask(dm, camp.id, thread.id, "hello")


def test_f8_model_errors_are_billed_so_they_are_not_refunded(tmp_path):
    """A run that reached the model (stop_reason error from the graph) costs money: it counts."""

    def model_failed(question, search_notes, store, conversation_id):
        return {"answer": "x", "citations": [], "confidence": "low",
                "insufficient_evidence": True, "stop_reason": "error", "trace": []}

    hub = make_hub(tmp_path, engine=model_failed, daily_message_limit=1)
    camp, dm, *_ = party(hub)
    thread = hub.create_thread(dm, camp.id)
    hub.ask(dm, camp.id, thread.id, "make the answer so long it gets truncated")
    with pytest.raises(RateLimited):
        hub.ask(dm, camp.id, thread.id, "again")


# --- F9: unicode filter -----------------------------------------------------------------------


def test_f9_list_filter_is_unicode_case_insensitive(tmp_path):
    hub = make_hub(tmp_path)
    camp, dm, *_ = party(hub)
    e = hub.create_entry(dm, camp.id, board="journal", title="ÉLARA", body="Straße")
    assert [x.id for x in hub.entries(dm, camp.id, q="élara")] == [e.id]
    assert [x.id for x in hub.entries(dm, camp.id, q="STRASSE")] == [e.id]


# --- F10: campaign creation is all-or-nothing -------------------------------------------------


def test_f10_failed_campaign_insert_leaves_no_folder(tmp_path):
    hub = make_hub(tmp_path)
    ghost = User(id="0" * 32, username="ghost")  # not in users: the insert breaks its foreign key
    with pytest.raises(Exception):  # noqa: B017 - any storage error; the folder is the point
        hub.create_campaign(ghost, "Ashvale")
    assert list((tmp_path / "campaigns").iterdir()) == []


# --- F12: sessions per user are capped ---------------------------------------------------------


def test_f12_a_user_holds_at_most_20_sessions_oldest_go_first(tmp_path):
    clock = Clock()
    hub = make_hub(tmp_path, clock=clock)
    user = hub.register("mia_r", PASSWORD)
    tokens = []
    for _ in range(22):
        tokens.append(hub.login("mia_r", PASSWORD))
        clock.advance(seconds=1)
    assert hub.user_for_token(tokens[0]) is None
    assert hub.user_for_token(tokens[1]) is None
    assert all(hub.user_for_token(t) == user for t in tokens[2:])
