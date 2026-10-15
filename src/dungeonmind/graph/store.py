"""Conversation stores: the last encounter request per conversation (M5 "make it harder").

`InMemoryStore` is the default and forgets everything when the process ends. `JsonFileStore`
keeps one JSON object {conversation_id: request} on disk for the CLI.

Both expose `last_problem`: None, or a short description of why the last `get` found no usable
request in a damaged store (S2), so the encounter node can trace it instead of failing.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dungeonmind.tools.encounter_builder import EncounterRequest

MAX_FILE_BYTES = 1024 * 1024
REPLACE_ATTEMPTS = 5
REPLACE_DELAY_S = 0.05


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, EncounterRequest] = {}
        self.last_problem: str | None = None

    def get(self, conversation_id: str) -> EncounterRequest | None:
        request = self._data.get(conversation_id)
        return request.model_copy(deep=True) if request else None

    def put(self, conversation_id: str, request: EncounterRequest) -> None:
        self._data[conversation_id] = request.model_copy(deep=True)


class JsonFileStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.last_problem: str | None = None

    def get(self, conversation_id: str) -> EncounterRequest | None:
        """The stored request, or None when there is none or the file is damaged (S2)."""
        self.last_problem = None
        data, problem = self._load()
        raw = data.get(conversation_id)
        if problem is None and raw is not None:
            try:
                return EncounterRequest.model_validate(raw)
            except ValidationError:
                problem = "its entry for this conversation isn't a valid encounter request"
        if problem is not None:
            self.last_problem = f"{self.path.name} is damaged ({problem}); ignored it"
        return None

    def put(self, conversation_id: str, request: EncounterRequest) -> None:
        data, problem = self._load()
        if problem is not None:
            # A file we couldn't read is replaced, but its bytes are kept: it may be a valid
            # store that was just too large, or a read that failed during another write.
            shutil.copyfile(self.path, self.backup_path)
        data[conversation_id] = request.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write a unique sibling file and swap it in (S1): a crash never leaves half a file, and
        # two writers never share a temporary file.
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=self.path.parent,
            prefix=self.path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp = Path(f.name)
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._replace(tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def _replace(self, tmp: Path) -> None:
        # On Windows the replace fails while another process has the target open; retry briefly.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_DELAY_S)

    def _load(self) -> tuple[dict[str, Any], str | None]:
        """(conversations, problem); a damaged file loads as empty with a problem description."""
        try:
            if not self.path.exists():
                return {}, None
            if self.path.stat().st_size > MAX_FILE_BYTES:
                return {}, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
            data = json.loads(self.path.read_text(encoding="utf-8"))
        # RecursionError: deeply nested JSON exhausts the parser's stack.
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
            return {}, f"unreadable: {type(e).__name__}"
        if not isinstance(data, dict):
            return {}, "not a JSON object of conversations"
        return data, None
