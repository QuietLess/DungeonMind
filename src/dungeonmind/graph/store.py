"""Conversation stores: the last encounter request per conversation (M5 "make it harder").

`InMemoryStore` is the default and forgets everything when the process ends. `JsonFileStore`
keeps one JSON object {conversation_id: request} on disk for the CLI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dungeonmind.tools.encounter_builder import EncounterRequest


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, EncounterRequest] = {}

    def get(self, conversation_id: str) -> EncounterRequest | None:
        request = self._data.get(conversation_id)
        return request.model_copy(deep=True) if request else None

    def put(self, conversation_id: str, request: EncounterRequest) -> None:
        self._data[conversation_id] = request.model_copy(deep=True)


class JsonFileStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def get(self, conversation_id: str) -> EncounterRequest | None:
        raw = self._load().get(conversation_id)
        return EncounterRequest.model_validate(raw) if raw is not None else None

    def put(self, conversation_id: str, request: EncounterRequest) -> None:
        data = self._load()
        data[conversation_id] = request.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write a sibling file and swap it in, so a crash never leaves half a file behind.
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} is not a JSON object of conversations")
        return data
