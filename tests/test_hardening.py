"""M9e: per-account sign-in limit (H1) and the explicit hub owner (H2)."""

import pytest
from hub_support import PASSWORD, make_hub

from dungeonmind.hub.errors import AuthFailed, Conflict, RateLimited


def _fail(hub, username, clients):
    for client in clients:
        with pytest.raises(AuthFailed):
            hub.login(username, "wrong password", client=client)


def test_h1_many_addresses_hit_the_per_account_limit(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("mia_r", PASSWORD)
    for i in range(10):  # 5 per client is the per-client cap; 10 clients x 5 = 50
        _fail(hub, "mia_r", [f"10.0.0.{i}"] * 5)
    with pytest.raises(RateLimited):
        hub.login("mia_r", PASSWORD, client="10.0.1.1")  # a fresh address is refused too


def test_h1_other_accounts_are_unaffected(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("mia_r", PASSWORD)
    hub.register("bo_k", PASSWORD)
    for i in range(10):
        _fail(hub, "mia_r", [f"10.0.0.{i}"] * 5)
    assert hub.login("bo_k", PASSWORD, client="10.0.1.1")


def test_h1_unknown_usernames_get_no_account_counter(tmp_path):
    hub = make_hub(tmp_path)
    for i in range(100):
        with pytest.raises(AuthFailed):
            hub.login(f"ghost_{i}", "wrong password", client=f"10.0.{i}.1")
    assert len(hub._accounts._failures) == 0


def test_h1_password_checks_on_the_account_page_count_too(tmp_path):
    hub = make_hub(tmp_path)
    user = hub.register("mia_r", PASSWORD)
    for i in range(10):
        for _ in range(5):
            with pytest.raises(Exception):  # noqa: B017 - Forbidden, then the limit
                hub.change_password(user, "t", "not mine", "a new password 1", client=f"10.0.0.{i}")
    with pytest.raises(RateLimited):
        hub.login("mia_r", PASSWORD, client="10.0.9.9")


def test_h2_the_first_account_is_recorded_as_owner(tmp_path):
    hub = make_hub(tmp_path)
    owner = hub.register("dm_dana", PASSWORD)
    hub.register("player_one", PASSWORD)
    assert hub.owner_id() == owner.id


def test_h2_an_older_hub_records_its_oldest_account(tmp_path):
    hub = make_hub(tmp_path)
    owner = hub.register("dm_dana", PASSWORD)
    hub.register("player_one", PASSWORD)
    with hub.storage.app() as db:
        db.execute("DELETE FROM settings")  # as before M9e
    assert make_hub(tmp_path).owner_id() == owner.id


def test_h2_the_owner_cannot_delete_their_account(tmp_path):
    hub = make_hub(tmp_path)
    owner = hub.register("dm_dana", PASSWORD)
    with pytest.raises(Conflict, match="owner"):
        hub.delete_account(owner, PASSWORD, "dm_dana")
    assert hub.login("dm_dana", PASSWORD)


def test_h2_ownership_doesnt_move_when_others_come_and_go(tmp_path):
    hub = make_hub(tmp_path, signup="invite")
    owner = hub.register("dm_dana", PASSWORD)
    camp = hub.create_campaign(owner, "Ashvale")
    p1 = hub.register("p_one", PASSWORD, invite=hub.create_invite(owner, camp.id).code)
    hub.delete_account(p1, PASSWORD, "p_one")
    assert hub.owner_id() == owner.id
