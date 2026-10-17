"""M9d: behind Cloudflare (P1–P3), invite-only sign-up (S1–S3) and `dungeonmind share` (SH1–SH3)."""

import os
import stat
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from hub_support import PASSWORD, make_hub
from typer.testing import CliRunner

from dungeonmind import cli
from dungeonmind.hub.api import CSRF_HEADER, SESSION_COOKIE, create_app

H = {CSRF_HEADER: "1"}
WIDE = {"COLUMNS": "200", "TERMINAL_WIDTH": "200"}
LOOPBACK = ("127.0.0.1", 50000)


def _fail_logins(client, n=5, headers=None):
    for _ in range(n):
        client.post(
            "/api/auth/login",
            json={"username": "mia_r", "password": "wrong password"},
            headers={**H, **(headers or {})},
        )


def _login(client, headers=None):
    return client.post(
        "/api/auth/login",
        json={"username": "mia_r", "password": PASSWORD},
        headers={**H, **(headers or {})},
    )


@pytest.fixture
def hub(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("mia_r", PASSWORD)
    return hub


# --- P1 real client address --------------------------------------------------------------------


def test_p1_cloudflare_header_separates_clients_behind_the_tunnel(hub):
    client = TestClient(create_app(hub, proxy="cloudflare"), client=LOOPBACK)
    _fail_logins(client, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert _login(client, {"CF-Connecting-IP": "203.0.113.5"}).status_code == 429
    assert _login(client, {"CF-Connecting-IP": "203.0.113.6"}).status_code == 200


def test_p1_without_the_flag_the_header_is_ignored(hub):
    client = TestClient(create_app(hub), client=LOOPBACK)
    _fail_logins(client, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert _login(client, {"CF-Connecting-IP": "203.0.113.6"}).status_code == 429


def test_p1_the_header_counts_only_from_loopback(hub):
    client = TestClient(create_app(hub, proxy="cloudflare"), client=("198.51.100.9", 4000))
    _fail_logins(client, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert _login(client, {"CF-Connecting-IP": "203.0.113.6"}).status_code == 429


def test_p1_an_invalid_header_falls_back_to_the_connection(hub):
    client = TestClient(create_app(hub, proxy="cloudflare"), client=LOOPBACK)
    _fail_logins(client, headers={"CF-Connecting-IP": "not-an-ip"})
    assert _login(client, {"CF-Connecting-IP": "also bad"}).status_code == 429
    assert _login(client, {"CF-Connecting-IP": "203.0.113.6"}).status_code == 200


def test_p1_ipv6_clients_are_grouped_by_64(hub):
    client = TestClient(create_app(hub, proxy="cloudflare"), client=LOOPBACK)
    _fail_logins(client, headers={"CF-Connecting-IP": "2001:db8:1:2::5"})
    assert _login(client, {"CF-Connecting-IP": "2001:db8:1:2:ffff::9"}).status_code == 429
    assert _login(client, {"CF-Connecting-IP": "2001:db8:1:3::5"}).status_code == 200


# --- P2 secure cookie --------------------------------------------------------------------------


def _cookie_header(response) -> str:
    return next(v for k, v in response.headers.multi_items() if k == "set-cookie")


def test_p2_forwarded_https_marks_the_cookie_secure(hub):
    client = TestClient(create_app(hub, proxy="cloudflare"), client=LOOPBACK)
    r = _login(client, {"X-Forwarded-Proto": "https", "CF-Connecting-IP": "203.0.113.5"})
    assert r.status_code == 200
    assert SESSION_COOKIE in _cookie_header(r)
    assert "secure" in _cookie_header(r).lower()


def test_p2_the_forwarded_scheme_is_ignored_without_the_flag(hub):
    client = TestClient(create_app(hub), client=LOOPBACK)
    r = _login(client, {"X-Forwarded-Proto": "https"})
    assert "secure" not in _cookie_header(r).lower()


# --- S1–S3 invite-only sign-up -----------------------------------------------------------------


@pytest.fixture
def invite_hub(tmp_path):
    hub = make_hub(tmp_path, signup="invite")
    dm = hub.register("dm_dana", PASSWORD)  # the owner's account exists already (CLI/API)
    camp = hub.create_campaign(dm, "Ashvale")
    return hub, dm, camp


def _register(client, username, invite=None):
    body = {"username": username, "password": PASSWORD}
    if invite is not None:
        body["invite"] = invite
    return client.post("/api/auth/register", json=body, headers=H)


def test_s1_sign_up_needs_a_valid_invite(invite_hub):
    hub, dm, camp = invite_hub
    client = TestClient(create_app(hub))
    for invite in (None, "nope-nope-nope"):
        r = _register(client, "player_one", invite)
        assert r.status_code == 403
        assert r.json()["detail"] == "Ask your DM for an invite code."
    with hub.storage.app() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_s1_a_valid_invite_creates_the_account_and_joins(invite_hub):
    hub, dm, camp = invite_hub
    code = hub.create_invite(dm, camp.id).code
    client = TestClient(create_app(hub))
    r = _register(client, "player_one", code)
    assert r.status_code == 201, r.text
    assert [c["id"] for c in client.get("/api/campaigns").json()] == [camp.id]
    assert _register(TestClient(create_app(hub)), "player_two", code).status_code == 403  # used


def test_s1_failed_codes_count_toward_the_registration_limit(invite_hub):
    hub, dm, camp = invite_hub
    client = TestClient(create_app(hub))
    for i in range(10):
        _register(client, f"guess_{i}", "wrong-code-123")
    code = hub.create_invite(dm, camp.id).code
    assert _register(client, "player_one", code).status_code == 429


def test_s2_config_says_which_sign_up_applies(invite_hub, tmp_path):
    hub, *_ = invite_hub
    config = TestClient(create_app(hub)).get("/api/auth/config").json()
    assert config == {"signup": "invite", "first": False}
    open_hub = make_hub(tmp_path / "open")
    config = TestClient(create_app(open_hub)).get("/api/auth/config").json()
    assert config["signup"] == "open"


def test_s3_open_sign_up_still_ignores_invites(tmp_path):
    client = TestClient(create_app(make_hub(tmp_path)))
    assert _register(client, "player_one").status_code == 201


# --- P3 and serve flags --------------------------------------------------------------------------


@pytest.fixture
def serve_env(monkeypatch):
    rec = SimpleNamespace(run=None, app_kw=None, hub_kw=None)

    def build_hub(*a, **k):
        rec.hub_kw = k
        return object()

    def create(hub, **kw):
        rec.app_kw = kw
        return "app"

    monkeypatch.setattr("dungeonmind.hub.app.build_hub", build_hub)
    monkeypatch.setattr("dungeonmind.hub.api.create_app", create)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: setattr(rec, "run", kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-placeholder")
    return rec


def test_p3_uvicorn_never_reads_forwarded_headers(serve_env):
    result = CliRunner().invoke(cli.app, ["serve", "--proxy", "cloudflare"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert serve_env.run["proxy_headers"] is False
    assert serve_env.app_kw["proxy"] == "cloudflare"


def test_p1_proxy_mode_needs_a_loopback_bind(serve_env):
    result = CliRunner().invoke(
        cli.app, ["serve", "--proxy", "cloudflare", "--host", "0.0.0.0"], env=WIDE
    )
    assert result.exit_code != 0
    assert serve_env.run is None


def test_s3_serve_passes_the_sign_up_mode(serve_env):
    result = CliRunner().invoke(cli.app, ["serve", "--signup", "invite"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert serve_env.hub_kw["signup"] == "invite"


# --- SH1–SH3 share -------------------------------------------------------------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _fake_cloudflared(tmp_path, body: str):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "cloudflared"
    exe.write_text(f"#!{sys.executable}\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return bindir


TUNNEL = """
import sys, time, pathlib
pathlib.Path(sys.argv[0]).with_name("args.txt").write_text(" ".join(sys.argv[1:]))
print("INF Requesting new quick Tunnel on trycloudflare.com...", file=sys.stderr, flush=True)
print("INF |  https://brave-otter-lantern.trycloudflare.com  |", file=sys.stderr, flush=True)
time.sleep(30)
"""


def test_sh1_share_prints_the_address_and_runs_the_hub_behind_the_proxy(
    serve_env, tmp_path, monkeypatch
):
    bindir = _fake_cloudflared(tmp_path, TUNNEL)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    result = CliRunner().invoke(cli.app, ["share", "--port", "8123"], env=WIDE)
    assert result.exit_code == 0, result.output
    assert "https://brave-otter-lantern.trycloudflare.com" in result.output
    assert serve_env.run["host"] == "127.0.0.1"
    assert serve_env.run["port"] == 8123
    assert serve_env.run["proxy_headers"] is False
    assert serve_env.app_kw["proxy"] == "cloudflare"
    assert serve_env.hub_kw["signup"] == "invite"
    args = (bindir / "args.txt").read_text()
    assert "--url http://127.0.0.1:8123" in args
    assert "--no-autoupdate" in args


def test_sh2_share_without_cloudflared_explains_how_to_install(serve_env, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    result = CliRunner().invoke(cli.app, ["share"], env={**WIDE, "PATH": str(empty)})
    assert result.exit_code == 1
    assert "cloudflared" in result.output
    assert "Traceback" not in result.output
    assert serve_env.run is None


def test_sh2_share_gives_up_when_no_address_appears(serve_env, tmp_path, monkeypatch):
    bindir = _fake_cloudflared(tmp_path, "import time\ntime.sleep(30)")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    result = CliRunner().invoke(
        cli.app, ["share", "--port", str(_free_port()), "--tunnel-timeout", "1"], env=WIDE
    )
    assert result.exit_code == 1
    assert "address" in result.output.lower()
    assert serve_env.run is None


def test_sh3_open_sign_up_while_sharing_warns_about_cost(serve_env, tmp_path, monkeypatch):
    bindir = _fake_cloudflared(tmp_path, TUNNEL)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    result = CliRunner().invoke(
        cli.app, ["share", "--port", str(_free_port()), "--signup", "open"], env=WIDE
    )
    assert result.exit_code == 0, result.output
    assert "anyone" in result.output.lower()
    assert serve_env.hub_kw["signup"] == "open"


# --- M9d review fixes ---------------------------------------------------------------------------


def test_r1_only_the_owners_campaigns_can_invite_new_accounts(invite_hub):
    hub, dm, camp = invite_hub  # dm_dana is the first account: the hub's owner
    client = TestClient(create_app(hub))
    assert _register(client, "player_one", hub.create_invite(dm, camp.id).code).status_code == 201
    player = hub.login("player_one", PASSWORD)
    player_user = hub.user_for_token(player)
    own = hub.create_campaign(player_user, "Player side game")
    code = hub.create_invite(player_user, own.id).code
    r = _register(TestClient(create_app(hub)), "sock_puppet", code)
    assert r.status_code == 403
    # Existing accounts can still join the player's campaign with it.
    other = TestClient(create_app(hub))
    assert _register(other, "player_two", hub.create_invite(dm, camp.id).code).status_code == 201
    assert other.post("/api/invites/accept", json={"code": code}, headers=H).status_code == 200


def test_r4_config_tells_an_empty_hub_that_the_first_sign_up_needs_no_code(tmp_path):
    hub = make_hub(tmp_path, signup="invite")
    client = TestClient(create_app(hub))
    assert client.get("/api/auth/config").json() == {"signup": "invite", "first": True}
    assert _register(client, "dm_dana").status_code == 201  # the owner, no code
    assert client.get("/api/auth/config").json() == {"signup": "invite", "first": False}


def test_r5_hsts_through_the_tunnel_only(hub):
    proxied = TestClient(create_app(hub, proxy="cloudflare"), client=LOOPBACK)
    r = proxied.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in r.headers.get("strict-transport-security", "")
    assert "strict-transport-security" not in TestClient(create_app(hub)).get("/api/health").headers


def test_r2_an_error_line_naming_the_api_host_is_not_the_address(serve_env, tmp_path, monkeypatch):
    body = """
import sys, time
print('ERR failed to request quick Tunnel: '
      'Post "https://api.trycloudflare.com/tunnel": EOF', file=sys.stderr, flush=True)
sys.exit(1)
"""
    bindir = _fake_cloudflared(tmp_path, body)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    result = CliRunner().invoke(
        cli.app, ["share", "--port", str(_free_port()), "--tunnel-timeout", "5"], env=WIDE
    )
    assert result.exit_code == 1
    assert "api.trycloudflare.com" not in result.output
    assert serve_env.run is None


def test_r6_share_refuses_a_busy_port_before_opening_the_tunnel(serve_env, tmp_path, monkeypatch):
    import socket

    bindir = _fake_cloudflared(tmp_path, TUNNEL)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        result = CliRunner().invoke(cli.app, ["share", "--port", str(port)], env=WIDE)
    assert result.exit_code == 1
    assert "in use" in result.output
    assert not (bindir / "args.txt").exists()  # cloudflared never started
