"""JsonFileStore robustness (security re-review of S1/S2)."""

import json

import pytest

from dungeonmind.graph import store as store_module
from dungeonmind.graph.store import JsonFileStore
from dungeonmind.tools.encounter_builder import EncounterRequest

REQUEST = EncounterRequest(party_levels=[5, 5, 5, 5])


def test_deeply_nested_json_is_treated_as_damaged(tmp_path):
    # 100k "[" makes json.loads raise RecursionError, which used to escape the S2 handling.
    path = tmp_path / "conversations.json"
    path.write_text("[" * 100_000, encoding="utf-8")
    store = JsonFileStore(path)
    assert store.get("a") is None
    assert store.last_problem and "conversations.json" in store.last_problem
    store.put("a", REQUEST)  # must not raise
    assert JsonFileStore(path).get("a") == REQUEST


ORIGINALS = {
    "malformed": "{not json",
    # Valid JSON but over the 1 MB limit, so it holds conversations get() won't read.
    "too_large": json.dumps({"keep": {"party_levels": [3]}, "pad": "x" * (1024 * 1024)}),
}


# Ids, not the contents: pytest puts the parameter id in an environment variable.
@pytest.mark.parametrize("kind", list(ORIGINALS))
def test_put_backs_up_a_file_it_could_not_read_before_replacing_it(tmp_path, kind):
    original = ORIGINALS[kind]
    path = tmp_path / "conversations.json"
    path.write_text(original, encoding="utf-8")
    JsonFileStore(path).put("a", REQUEST)
    backup = tmp_path / "conversations.json.bak"
    assert backup.read_text(encoding="utf-8") == original
    assert JsonFileStore(path).get("a") == REQUEST


def test_put_on_a_healthy_file_makes_no_backup(tmp_path):
    path = tmp_path / "conversations.json"
    store = JsonFileStore(path)
    store.put("a", REQUEST)
    store.put("b", REQUEST)
    assert not (tmp_path / "conversations.json.bak").exists()


def test_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store_module.json, "dump", broken_dump)
    with pytest.raises(OSError, match="disk full"):
        JsonFileStore(tmp_path / "conversations.json").put("a", REQUEST)
    assert list(tmp_path.glob("*.tmp")) == []
