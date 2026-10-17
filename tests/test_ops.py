"""M9a: health check (O1) and backups (B1–B3)."""

import sqlite3
import stat
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from hub_support import make_hub, party
from typer.testing import CliRunner

from dungeonmind import cli
from dungeonmind.hub.api import create_app
from dungeonmind.hub.backup import BackupError, backup

WIDE = {"COLUMNS": "200", "TERMINAL_WIDTH": "200"}
NOW = datetime(2026, 10, 18, 9, 30, 5, tzinfo=UTC)


# --- O1 health ---------------------------------------------------------------------------------


def test_o1_health_needs_no_session_or_csrf_header(tmp_path):
    client = TestClient(create_app(make_hub(tmp_path)))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_o1_health_is_503_when_the_database_is_broken(tmp_path):
    hub = make_hub(tmp_path)
    client = TestClient(create_app(hub), raise_server_exceptions=False)
    hub.storage.app_db.write_bytes(b"this is not a sqlite database" * 100)
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable"}


# --- B1–B3 backups -----------------------------------------------------------------------------


@pytest.fixture
def filled(tmp_path):
    root = tmp_path / "hub"
    hub = make_hub(root)
    camp, dm, *_ = party(hub)
    hub.create_entry(dm, camp.id, board="lore", title="The Red Oath", body="A secret pact.")
    other = hub.create_campaign(dm, "Second")
    return root, camp.id, other.id


def _count(db, table):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_b1_backup_copies_every_database(filled, tmp_path):
    root, cid, other = filled
    result = backup(root, tmp_path / "backups", now=NOW)
    assert result.folder == tmp_path / "backups" / "dungeonmind-backup-20261018-093005"
    assert result.campaigns == 2
    assert _count(result.folder / "app.db", "users") == _count(root / "app.db", "users")
    copy = result.folder / "campaigns" / cid / "campaign.db"
    assert _count(copy, "entries") == 1
    assert (result.folder / "campaigns" / other / "campaign.db").exists()
    for db in [result.folder / "app.db", copy]:
        with sqlite3.connect(db) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_b1_backup_works_while_a_connection_is_open(filled, tmp_path):
    root, cid, _ = filled
    reader = sqlite3.connect(root / "campaigns" / cid / "campaign.db")
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM entries").fetchone()
    try:
        result = backup(root, tmp_path / "backups", now=NOW)
    finally:
        reader.close()
    assert _count(result.folder / "campaigns" / cid / "campaign.db", "entries") == 1


def test_b2_backup_is_private(filled, tmp_path):
    root, *_ = filled
    result = backup(root, tmp_path / "backups", now=NOW)
    assert stat.S_IMODE(result.folder.stat().st_mode) == 0o700
    for path in result.folder.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected, path


def test_b2_backup_refuses_a_folder_inside_the_root(filled):
    root, *_ = filled
    with pytest.raises(BackupError, match="outside"):
        backup(root, root / "backups", now=NOW)


def test_b2_backup_never_overwrites(filled, tmp_path):
    root, *_ = filled
    backup(root, tmp_path / "backups", now=NOW)
    with pytest.raises(BackupError, match="already exists"):
        backup(root, tmp_path / "backups", now=NOW)


def test_b3_backup_of_a_missing_root_is_a_plain_error(tmp_path):
    result = CliRunner().invoke(
        cli.app,
        ["hub", "backup", "--root", str(tmp_path / "nope"), "--out", str(tmp_path / "b")],
        env=WIDE,
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "app.db" in result.output
    assert not (tmp_path / "nope").exists()  # checking didn't create the root


def test_b3_backup_cli_reports_the_folder_and_campaigns(filled, tmp_path):
    root, *_ = filled
    result = CliRunner().invoke(
        cli.app, ["hub", "backup", "--root", str(root), "--out", str(tmp_path / "b")], env=WIDE
    )
    assert result.exit_code == 0, result.output
    assert "dungeonmind-backup-" in result.output
    assert "2 campaigns" in result.output


# --- M9a review fixes --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["what?", "hash#tag", "per%41cent"])
def test_r1_special_characters_in_the_root_path(tmp_path, name):
    root = tmp_path / name / "hub"
    hub = make_hub(root)
    camp, *_ = party(hub)
    result = backup(root, tmp_path / "out", now=NOW)
    assert _count(result.folder / "app.db", "users") == 5
    assert (result.folder / "campaigns" / camp.id / "campaign.db").exists()
    assert sorted(p.name for p in (tmp_path / name).iterdir()) == ["hub"]  # nothing created


def test_r3_a_failed_backup_leaves_no_folder_and_no_traceback(filled, tmp_path):
    root, cid, _ = filled
    (root / "campaigns" / cid / "campaign.db").write_bytes(b"not a database" * 200)
    for suffix in ("-wal", "-shm"):
        (root / "campaigns" / cid / f"campaign.db{suffix}").unlink(missing_ok=True)
    with pytest.raises(BackupError, match="campaign"):
        backup(root, tmp_path / "backups", now=NOW)
    assert list((tmp_path / "backups").iterdir()) == []
    result = CliRunner().invoke(
        cli.app, ["hub", "backup", "--root", str(root), "--out", str(tmp_path / "b2")], env=WIDE
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_r4_out_inside_the_root_is_refused_whatever_the_spelling(filled, tmp_path):
    root, *_ = filled
    link = tmp_path / "link"
    link.symlink_to(root)
    with pytest.raises(BackupError, match="outside"):
        backup(root, link / "bk", now=NOW)
    upper = root.parent / root.name.upper()
    if upper.exists():  # case-insensitive file system (macOS APFS)
        with pytest.raises(BackupError, match="outside"):
            backup(root, upper / "bk", now=NOW)


def test_r_symlinked_or_odd_campaign_folders_are_skipped(filled, tmp_path):
    root, cid, other = filled
    (root / "campaigns" / "not-a-campaign").mkdir()
    (root / "campaigns" / ("f" * 32)).symlink_to(root / "campaigns" / cid)
    result = backup(root, tmp_path / "backups", now=NOW)
    assert result.campaigns == 2
    assert sorted(p.name for p in (result.folder / "campaigns").iterdir()) == sorted([cid, other])
