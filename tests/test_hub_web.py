"""M7 serving (docs/ARCHITECTURE.md › M7 contract, UI1–UI2): the built UI on the API's origin."""

import pytest
from fastapi.testclient import TestClient
from hub_support import make_hub

from dungeonmind.hub.api import create_app

INDEX = "<!doctype html><html><body><div id=root></div></body></html>"


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "app-123.js").write_text("console.log('hi')")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside dist")
    return d


@pytest.fixture
def client(tmp_path, dist):
    return TestClient(create_app(make_hub(tmp_path / "hub"), web_dist=dist))


@pytest.mark.parametrize("path", ["/", "/campaigns", "/campaigns/abc/lore", "/login?next=/x"])
def test_ui1_app_paths_serve_the_index(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["content-type"].startswith("text/html")


def test_ui1_assets_and_root_files_are_served(client):
    assert client.get("/assets/app-123.js").text == "console.log('hi')"
    assert client.get("/favicon.svg").text == "<svg/>"


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"]
)
def test_ui1_paths_cannot_escape_the_dist_folder(client, path):
    r = client.get(path)
    assert "outside dist" not in r.text


def test_ui1_unknown_api_paths_stay_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_ui1_missing_build_explains_how_to_build(tmp_path):
    c = TestClient(create_app(make_hub(tmp_path / "hub"), web_dist=tmp_path / "missing"))
    r = c.get("/")
    assert r.status_code == 200
    assert "npm --prefix web run build" in r.text


def test_ui1_spa_routes_are_not_in_the_api_schema(client):
    assert all(p.startswith("/api/") for p in client.app.openapi()["paths"])


@pytest.mark.parametrize("path", ["/", "/assets/app-123.js", "/api/me", "/api/nope"])
def test_ui2_every_response_has_security_headers(client, path):
    h = client.get(path).headers
    csp = h["content-security-policy"]
    for directive in (
        "default-src 'self'",
        "img-src 'self' data:",
        "script-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ):
        assert directive in csp
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "DENY"


# --- M7 security review fixes (W1–W4) ---------------------------------------------------------


def test_w1_unexpected_errors_still_carry_security_headers(tmp_path, dist):
    hub = make_hub(tmp_path / "hub")
    app = create_app(hub, web_dist=dist)

    def broken(*a, **k):
        raise RuntimeError("boom")

    hub.campaigns = broken
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/api/auth/register", json={"username": "mia_r", "password": "a long password"},
           headers={"X-DungeonMind": "1"})
    r = c.get("/api/campaigns")
    assert r.status_code == 500
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_w1_null_bytes_in_paths_are_not_errors(client):
    r = client.get("/assets/%00x")
    assert r.status_code in (200, 404)
    assert "outside dist" not in r.text


@pytest.mark.parametrize("name", [".env", ".git/config", "assets/app-123.js.map"])
def test_w2_hidden_files_and_source_maps_are_not_served(client, dist, name):
    target = dist / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SECRET=1")
    r = client.get("/" + name)
    assert "SECRET=1" not in r.text


def test_w4_api_responses_are_not_cached_and_static_files_answer_head(client):
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert client.head("/assets/app-123.js").status_code == 200
    h = client.get("/").headers
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in h["permissions-policy"]


def test_w3_campaign_prompt_says_the_question_is_not_evidence():
    from dungeonmind.graph.llm import CAMPAIGN_PROMPT

    assert "question itself is not evidence" in CAMPAIGN_PROMPT
