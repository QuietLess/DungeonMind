"""Hub: the single place that checks membership, roles and authorship (M6 contract).

Every public method that touches a campaign first resolves the caller's membership
(`_member`); a non-member, a malformed id or an entry/thread the caller can't read is
`NotFound`, so responses never reveal what exists. `Forbidden` is only for things the caller
can see but their role doesn't allow them to change.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dungeonmind.graph.state import NoteHit
from dungeonmind.hub import auth, models
from dungeonmind.hub import entries as store
from dungeonmind.hub.chats import (
    ChatEngine,
    ThreadEncounterStore,
    message_from_row,
    reply_fields,
    thread_from_row,
)
from dungeonmind.hub.db import CAMPAIGN_ID, Storage, StorageError
from dungeonmind.hub.entries import Embedder, Viewer
from dungeonmind.hub.errors import (
    AuthFailed,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    RateLimited,
)
from dungeonmind.hub.models import (
    BOARD_KINDS,
    DEFAULT_KIND,
    DEFAULT_VISIBILITY,
    VISIBILITIES,
    Campaign,
    Character,
    Entry,
    Invite,
    Member,
    Message,
    Role,
    Thread,
    User,
)

DEFAULT_ROOT = Path("data/hub")
INVITE_DAYS = 7
INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
INVITE_LENGTH = 12
MAX_SEARCH_K = 50
LOGIN_FAILED = "Wrong username or password."
ENTRY_FIELDS = frozenset({"title", "body", "kind", "visibility", "session"})


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _text(value: Any, field: str, max_chars: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise Invalid(f"{field} must be text.")
    stripped = value.strip()
    if (not stripped and not allow_empty) or len(value) > max_chars:
        lower = 0 if allow_empty else 1
        raise Invalid(f"{field} must be {lower}–{max_chars} characters.")
    return stripped


def _session(value: Any) -> int | None:
    if value is None:
        return None
    low, high = models.SESSION_RANGE
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Invalid(f"Session must be a number from {low} to {high}.")
    return value


def _id(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotFound(f"{what} not found.")
    return value


class Hub:
    def __init__(
        self,
        root: Path | str = DEFAULT_ROOT,
        embedder: Embedder | None = None,
        engine: ChatEngine | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        scrypt_n: int = 2**15,
        daily_message_limit: int = 50,
    ) -> None:
        self.storage = Storage(root)
        self.embedder = embedder
        self.engine = engine
        self.clock = clock
        self.scrypt_n = scrypt_n
        self.daily_message_limit = daily_message_limit
        self._limiter = auth.LoginLimiter()
        # Verified against for unknown usernames, so both failures cost the same time (8).
        self._dummy_hash = auth.hash_password(secrets.token_urlsafe(16), scrypt_n)

    # --- accounts --------------------------------------------------------------------------

    def register(self, username: str, password: str) -> User:
        if not isinstance(username, str) or not re.fullmatch(models.USERNAME, username):
            raise Invalid("Usernames are 3–32 characters: lowercase letters, digits and _.")
        low, high = models.PASSWORD_CHARS
        if not isinstance(password, str) or not low <= len(password) <= high:
            raise Invalid(f"Passwords must be {low}–{high} characters.")
        user = User(id=uuid.uuid4().hex, username=username)
        hashed = auth.hash_password(password, self.scrypt_n)
        with self.storage.app(immediate=True) as db:
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise Conflict("That username is taken.")
            db.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user.id, username, hashed, _iso(self.clock())),
            )
        return user

    def login(self, username: str, password: str) -> str:
        now = self.clock()
        name = username if isinstance(username, str) else ""
        if self._limiter.blocked(name, now):
            raise RateLimited("Too many failed sign-in attempts. Try again in 15 minutes.")
        with self.storage.app() as db:
            row = db.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (name,)
            ).fetchone()
        ok = isinstance(password, str) and auth.verify_password(
            password, row["password_hash"] if row else self._dummy_hash
        )
        if row is None or not ok:
            self._limiter.failed(name, now)
            raise AuthFailed(LOGIN_FAILED)
        self._limiter.succeeded(name)
        token = auth.new_token()
        expires = now + timedelta(days=auth.SESSION_DAYS)
        with self.storage.app() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (auth.token_hash(token), row["id"], _iso(expires)),
            )
        return token

    def user_for_token(self, token: str) -> User | None:
        if not isinstance(token, str) or not token or len(token) > 200:
            return None
        with self.storage.app() as db:
            row = db.execute(
                "SELECT u.id, u.username, s.expires_at FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (auth.token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= self.clock():
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (auth.token_hash(token),))
                return None
        return User(id=row["id"], username=row["username"])

    def logout(self, token: str) -> None:
        with self.storage.app() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (auth.token_hash(token),))

    def reset_password(self, username: str, password: str) -> None:
        low, high = models.PASSWORD_CHARS
        if not isinstance(password, str) or not low <= len(password) <= high:
            raise Invalid(f"Passwords must be {low}–{high} characters.")
        hashed = auth.hash_password(password, self.scrypt_n)
        with self.storage.app(immediate=True) as db:
            row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                raise NotFound("No such user.")
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        self._limiter.succeeded(username)

    # --- membership ------------------------------------------------------------------------

    def _member(self, user: User, cid: str) -> Campaign:
        """The campaign as `user` sees it; NotFound unless they are a current member."""
        if not isinstance(cid, str) or not CAMPAIGN_ID.fullmatch(cid):
            raise NotFound("Campaign not found.")
        with self.storage.app() as db:
            row = db.execute(
                "SELECT c.id, c.name, c.dm_id, c.created_at, m.role FROM campaigns c "
                "JOIN memberships m ON m.campaign_id = c.id WHERE c.id = ? AND m.user_id = ?",
                (cid, user.id),
            ).fetchone()
        if row is None:
            raise NotFound("Campaign not found.")
        return _campaign(row)

    def _dm(self, user: User, cid: str) -> Campaign:
        camp = self._member(user, cid)
        if camp.role != "dm":
            raise Forbidden("Only the DM can do that.")
        return camp

    @staticmethod
    def _viewer(user: User, camp: Campaign) -> Viewer:
        return Viewer(user_id=user.id, is_dm=camp.role == "dm")

    # --- campaigns -------------------------------------------------------------------------

    def create_campaign(self, user: User, name: str) -> Campaign:
        name = _text(name, "Campaign name", models.CAMPAIGN_NAME_CHARS)
        cid = uuid.uuid4().hex
        now = _iso(self.clock())
        self.storage.create_campaign_db(cid)
        with self.storage.app(immediate=True) as db:
            db.execute(
                "INSERT INTO campaigns (id, name, dm_id, created_at) VALUES (?, ?, ?, ?)",
                (cid, name, user.id, now),
            )
            db.execute(
                "INSERT INTO memberships (campaign_id, user_id, role, joined_at) "
                "VALUES (?, ?, 'dm', ?)",
                (cid, user.id, now),
            )
        return self._member(user, cid)

    def campaigns(self, user: User) -> list[Campaign]:
        with self.storage.app() as db:
            rows = db.execute(
                "SELECT c.id, c.name, c.dm_id, c.created_at, m.role FROM campaigns c "
                "JOIN memberships m ON m.campaign_id = c.id WHERE m.user_id = ? "
                "ORDER BY c.created_at, c.id",
                (user.id,),
            ).fetchall()
        return [_campaign(r) for r in rows]

    def campaign(self, user: User, cid: str) -> Campaign:
        return self._member(user, cid)

    def delete_campaign(self, user: User, cid: str) -> None:
        self._dm(user, cid)
        folder = self.storage.campaign_folder(cid)
        # Access goes first, so a failure while removing files never leaves a usable campaign.
        with self.storage.app(immediate=True) as db:
            db.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
        if folder.exists():
            shutil.rmtree(folder)

    def create_invite(self, user: User, cid: str) -> Invite:
        self._dm(user, cid)
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
        expires = self.clock() + timedelta(days=INVITE_DAYS)
        with self.storage.app() as db:
            db.execute(
                "INSERT INTO invites (code_hash, campaign_id, expires_at) VALUES (?, ?, ?)",
                (auth.token_hash(code), cid, _iso(expires)),
            )
        return Invite(code=code, expires_at=expires)

    def revoke_invite(self, user: User, cid: str, code: str) -> None:
        self._dm(user, cid)
        with self.storage.app() as db:
            gone = db.execute(
                "DELETE FROM invites WHERE code_hash = ? AND campaign_id = ?",
                (auth.token_hash(_invite_code(code)), cid),
            ).rowcount
        if not gone:
            raise NotFound("Invite not found.")

    def accept_invite(self, user: User, code: str) -> Campaign:
        now = self.clock()
        with self.storage.app(immediate=True) as db:
            row = db.execute(
                "SELECT campaign_id, expires_at FROM invites WHERE code_hash = ?",
                (auth.token_hash(_invite_code(code)),),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) <= now:
                raise NotFound("Invite not found or expired.")
            cid = row["campaign_id"]
            if db.execute(
                "SELECT 1 FROM memberships WHERE campaign_id = ? AND user_id = ?", (cid, user.id)
            ).fetchone():
                raise Conflict("You're already in this campaign.")
            members = db.execute(
                "SELECT COUNT(*) FROM memberships WHERE campaign_id = ?", (cid,)
            ).fetchone()[0]
            if members >= models.MAX_MEMBERS:
                raise Invalid(f"A campaign can have at most {models.MAX_MEMBERS} members.")
            db.execute(
                "INSERT INTO memberships (campaign_id, user_id, role, joined_at) "
                "VALUES (?, ?, 'player', ?)",
                (cid, user.id, _iso(now)),
            )
            db.execute(
                "DELETE FROM invites WHERE code_hash = ?", (auth.token_hash(_invite_code(code)),)
            )
        return self._member(user, cid)

    def members(self, user: User, cid: str) -> list[Member]:
        self._member(user, cid)
        with self.storage.app() as db:
            rows = db.execute(
                "SELECT u.id, u.username, m.role FROM memberships m JOIN users u "
                "ON u.id = m.user_id WHERE m.campaign_id = ? ORDER BY m.role, u.username",
                (cid,),
            ).fetchall()
        return [Member(user_id=r["id"], username=r["username"], role=r["role"]) for r in rows]

    def remove_member(self, user: User, cid: str, user_id: str) -> None:
        camp = self._member(user, cid)
        if user_id == user.id:
            if camp.role == "dm":
                raise Forbidden("The DM can't leave; delete the campaign instead.")
        elif camp.role != "dm":
            raise Forbidden("Only the DM can remove members.")
        with self.storage.app() as db:
            gone = db.execute(
                "DELETE FROM memberships WHERE campaign_id = ? AND user_id = ? AND role = 'player'",
                (cid, user_id),
            ).rowcount
        if not gone:
            raise NotFound("Member not found.")

    # --- characters ------------------------------------------------------------------------

    def create_character(self, user: User, cid: str, name: str, summary: str = "") -> Character:
        self._member(user, cid)
        name = _text(name, "Character name", models.CHARACTER_NAME_CHARS)
        summary = _text(summary, "Summary", models.CHARACTER_SUMMARY_CHARS, allow_empty=True)
        with self.storage.campaign(cid) as db:
            cur = db.execute(
                "INSERT INTO characters (owner_id, name, summary, created_at) VALUES (?, ?, ?, ?)",
                (user.id, name, summary, _iso(self.clock())),
            )
            row = db.execute("SELECT * FROM characters WHERE id = ?", (cur.lastrowid,)).fetchone()
        return store.character_from_row(row)

    def characters(self, user: User, cid: str) -> list[Character]:
        self._member(user, cid)
        with self.storage.campaign(cid) as db:
            rows = db.execute("SELECT * FROM characters ORDER BY name, id").fetchall()
        return [store.character_from_row(r) for r in rows]

    def _own_character(self, db, user: User, chid: Any):
        row = db.execute("SELECT * FROM characters WHERE id = ?", (_id(chid, "Character"),))
        row = row.fetchone()
        if row is None:
            raise NotFound("Character not found.")
        if row["owner_id"] != user.id:
            raise Forbidden("Only the character's player can change it.")
        return row

    def update_character(
        self,
        user: User,
        cid: str,
        chid: int,
        *,
        name: str | None = None,
        summary: str | None = None,
    ) -> Character:
        self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._own_character(db, user, chid)
            new_name = (
                row["name"]
                if name is None
                else _text(name, "Character name", models.CHARACTER_NAME_CHARS)
            )
            new_summary = (
                row["summary"]
                if summary is None
                else _text(summary, "Summary", models.CHARACTER_SUMMARY_CHARS, allow_empty=True)
            )
            db.execute(
                "UPDATE characters SET name = ?, summary = ? WHERE id = ?",
                (new_name, new_summary, row["id"]),
            )
            row = db.execute("SELECT * FROM characters WHERE id = ?", (row["id"],)).fetchone()
        return store.character_from_row(row)

    def delete_character(self, user: User, cid: str, chid: int) -> None:
        self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._own_character(db, user, chid)
            count = db.execute(
                "SELECT COUNT(*) FROM entries WHERE character_id = ?", (row["id"],)
            ).fetchone()[0]
            if count:
                raise Conflict(
                    f"This character's board still has {count} entries; delete them first."
                )
            db.execute("DELETE FROM characters WHERE id = ?", (row["id"],))

    # --- entries ---------------------------------------------------------------------------

    def _visibility(self, board: str, visibility: Any, author_is_dm: bool) -> str:
        if visibility is None:
            visibility = DEFAULT_VISIBILITY[board]
        if visibility not in VISIBILITIES:
            raise Invalid("Visibility must be private, dm or party.")
        if visibility == "dm" and (author_is_dm or board == "lore"):
            return "private"  # the DM already reads their own private entries
        return visibility

    def _kind(self, board: str, kind: Any) -> str:
        kind = DEFAULT_KIND[board] if kind is None else kind
        if kind not in BOARD_KINDS[board]:
            raise Invalid(
                f"Kind for the {board} board must be one of: {', '.join(BOARD_KINDS[board])}."
            )
        return kind

    def create_entry(
        self,
        user: User,
        cid: str,
        *,
        board: str,
        title: str,
        body: str,
        kind: str | None = None,
        visibility: str | None = None,
        character_id: int | None = None,
        session: int | None = None,
    ) -> Entry:
        camp = self._member(user, cid)
        if board not in BOARD_KINDS:
            raise Invalid("Board must be lore, character or journal.")
        if board == "lore" and camp.role != "dm":
            raise Forbidden("Only the DM writes on the lore board.")
        if board == "character" and character_id is None:
            raise Invalid("Entries on a character board need a character_id.")
        if board != "character" and character_id is not None:
            raise Invalid("Only character-board entries have a character_id.")
        title = _text(title, "Title", models.TITLE_CHARS)
        body = _text(body, "Body", models.BODY_CHARS)
        kind = self._kind(board, kind)
        visibility = self._visibility(board, visibility, camp.role == "dm")
        session = _session(session)
        chunks = self._embed(title, body)
        now = _iso(self.clock())
        with self.storage.campaign(cid, immediate=True) as db:
            if board == "character":
                self._own_character(db, user, character_id)
            count = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            if count >= models.MAX_ENTRIES:
                raise Invalid(f"A campaign can hold at most {models.MAX_ENTRIES} entries.")
            cur = db.execute(
                "INSERT INTO entries (board, character_id, author_id, author_name, kind, title, "
                "body, visibility, session, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    board,
                    character_id,
                    user.id,
                    user.username,
                    kind,
                    title,
                    body,
                    visibility,
                    session,
                    now,
                    now,
                ),
            )
            store.replace_chunks(db, cur.lastrowid, chunks)
            row = store.get_entry(db, cur.lastrowid)
        return store.entry_from_row(row)

    def _embed(self, title: str, body: str) -> list[tuple[str, bytes]]:
        if self.embedder is None:
            raise StorageError("Search isn't available, so the entry wasn't saved.")
        try:
            return store.embed_entry(self.embedder, title, body)
        except Exception as e:  # never show model or path details to users
            raise StorageError("Couldn't index this entry right now; it wasn't saved.") from e

    def entries(
        self,
        user: User,
        cid: str,
        *,
        board: str | None = None,
        character_id: int | None = None,
        session: int | None = None,
        q: str | None = None,
    ) -> list[Entry]:
        camp = self._member(user, cid)
        if board is not None and board not in BOARD_KINDS:
            raise Invalid("Board must be lore, character or journal.")
        if q is not None:
            q = _text(q, "Search text", models.QUESTION_CHARS, allow_empty=True) or None
        with self.storage.campaign(cid) as db:
            return store.list_entries(
                db,
                self._viewer(user, camp),
                board=board,
                character_id=character_id,
                session=_session(session),
                q=q,
            )

    def _readable(self, db, user: User, camp: Campaign, eid: Any):
        """The entry row if `user` may read it; NotFound otherwise (existence isn't revealed)."""
        where, params = store.visible_clause(self._viewer(user, camp))
        row = db.execute(
            f"SELECT e.* FROM entries e WHERE e.id = ? AND {where}",
            [_id(eid, "Entry"), *params],
        ).fetchone()
        if row is None:
            raise NotFound("Entry not found.")
        return row

    def entry(self, user: User, cid: str, eid: int) -> Entry:
        camp = self._member(user, cid)
        with self.storage.campaign(cid) as db:
            return store.entry_from_row(self._readable(db, user, camp, eid))

    def update_entry(self, user: User, cid: str, eid: int, **changes: Any) -> Entry:
        camp = self._member(user, cid)
        unknown = set(changes) - ENTRY_FIELDS
        if unknown:
            raise Invalid(f"These fields can't be changed: {', '.join(sorted(unknown))}.")
        with self.storage.campaign(cid) as db:
            row = self._readable(db, user, camp, eid)
        if row["author_id"] != user.id:
            raise Forbidden("Only the author can change an entry.")
        board = row["board"]
        title = (
            _text(changes["title"], "Title", models.TITLE_CHARS)
            if "title" in changes
            else row["title"]
        )
        body = (
            _text(changes["body"], "Body", models.BODY_CHARS) if "body" in changes else row["body"]
        )
        kind = self._kind(board, changes["kind"]) if "kind" in changes else row["kind"]
        visibility = (
            self._visibility(board, changes["visibility"], camp.role == "dm")
            if "visibility" in changes
            else row["visibility"]
        )
        session = _session(changes["session"]) if "session" in changes else row["session"]
        rechunk = title != row["title"] or body != row["body"]
        chunks = self._embed(title, body) if rechunk else None
        with self.storage.campaign(cid, immediate=True) as db:
            current = store.get_entry(db, row["id"])
            if current is None or current["author_id"] != user.id:
                raise NotFound("Entry not found.")
            db.execute(
                "UPDATE entries SET title = ?, body = ?, kind = ?, visibility = ?, session = ?, "
                "updated_at = ? WHERE id = ?",
                (title, body, kind, visibility, session, _iso(self.clock()), row["id"]),
            )
            if chunks is not None:
                store.replace_chunks(db, row["id"], chunks)
            return store.entry_from_row(store.get_entry(db, row["id"]))

    def delete_entry(self, user: User, cid: str, eid: int) -> None:
        camp = self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._readable(db, user, camp, eid)
            if row["author_id"] != user.id:
                raise Forbidden("Only the author can delete an entry.")
            db.execute("DELETE FROM entries WHERE id = ?", (row["id"],))

    def search(self, user: User, cid: str, query: str, k: int = 8) -> list[NoteHit]:
        camp = self._member(user, cid)
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_SEARCH_K:
            raise Invalid(f"k must be 1–{MAX_SEARCH_K}.")
        query = _text(query, "Search text", models.QUESTION_CHARS)
        return self._search(cid, self._viewer(user, camp), query, k)

    def _search(self, cid: str, viewer: Viewer, query: str, k: int) -> list[NoteHit]:
        with self.storage.campaign(cid) as db:
            if self.embedder is None:
                raise StorageError("Search isn't available.")
            return store.search(db, viewer, self.embedder, query, k)

    # --- chats -----------------------------------------------------------------------------

    def create_thread(self, user: User, cid: str, title: str = "New chat") -> Thread:
        self._member(user, cid)
        title = _text(title, "Title", models.THREAD_TITLE_CHARS)
        with self.storage.campaign(cid) as db:
            cur = db.execute(
                "INSERT INTO threads (owner_id, title, created_at) VALUES (?, ?, ?)",
                (user.id, title, _iso(self.clock())),
            )
            row = db.execute("SELECT * FROM threads WHERE id = ?", (cur.lastrowid,)).fetchone()
        return thread_from_row(row)

    def threads(self, user: User, cid: str) -> list[Thread]:
        self._member(user, cid)
        with self.storage.campaign(cid) as db:
            rows = db.execute(
                "SELECT * FROM threads WHERE owner_id = ? ORDER BY id DESC", (user.id,)
            ).fetchall()
        return [thread_from_row(r) for r in rows]

    def _own_thread(self, db, user: User, tid: Any):
        row = db.execute(
            "SELECT * FROM threads WHERE id = ? AND owner_id = ?", (_id(tid, "Chat"), user.id)
        ).fetchone()
        if row is None:  # someone else's thread doesn't exist, as far as the caller knows
            raise NotFound("Chat not found.")
        return row

    def thread(self, user: User, cid: str, tid: int) -> Thread:
        self._member(user, cid)
        with self.storage.campaign(cid) as db:
            return thread_from_row(self._own_thread(db, user, tid))

    def messages(self, user: User, cid: str, tid: int) -> list[Message]:
        self._member(user, cid)
        with self.storage.campaign(cid) as db:
            row = self._own_thread(db, user, tid)
            rows = db.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (row["id"],)
            ).fetchall()
        return [message_from_row(r) for r in rows]

    def ask(self, user: User, cid: str, tid: int, text: str) -> Message:
        camp = self._member(user, cid)
        question = _text(text, "Message", models.QUESTION_CHARS)
        with self.storage.campaign(cid) as db:
            thread_id = self._own_thread(db, user, tid)["id"]
        self._count_message(user)
        self._save_message(cid, thread_id, "user", question, {})

        viewer = self._viewer(user, camp)

        def search_notes(query: str, k: int) -> list[NoteHit]:
            # Bound to this campaign and viewer; the graph can only choose the query and k.
            return self._search(cid, viewer, query, max(1, min(int(k), MAX_SEARCH_K)))

        thread_store = ThreadEncounterStore(
            lambda immediate=False: self.storage.campaign(cid, immediate), thread_id
        )
        try:
            if self.engine is None:
                raise RuntimeError("no chat engine configured")
            state = self.engine(question, search_notes, thread_store, f"thread-{thread_id}")
        except Exception:
            state = {"stop_reason": "error"}
        fields = reply_fields(state)
        text_out = fields.pop("text")
        return self._save_message(cid, thread_id, "assistant", text_out, fields)

    def _count_message(self, user: User) -> None:
        day = self.clock().astimezone(UTC).date().isoformat()
        with self.storage.app(immediate=True) as db:
            row = db.execute(
                "SELECT count FROM usage WHERE user_id = ? AND day = ?", (user.id, day)
            ).fetchone()
            if row is not None and row["count"] >= self.daily_message_limit:
                raise RateLimited(
                    f"You've reached today's limit of {self.daily_message_limit} questions. "
                    "It resets at midnight UTC."
                )
            db.execute(
                "INSERT INTO usage (user_id, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1",
                (user.id, day),
            )

    def _save_message(
        self, cid: str, thread_id: int, role: str, text: str, meta: dict[str, Any]
    ) -> Message:
        with self.storage.campaign(cid) as db:
            cur = db.execute(
                "INSERT INTO messages (thread_id, role, text, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, role, text, json.dumps(meta, default=str), _iso(self.clock())),
            )
            row = db.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return message_from_row(row)


def _invite_code(code: Any) -> str:
    return code.strip().lower() if isinstance(code, str) else ""


def _campaign(row) -> Campaign:
    role: Role = row["role"]
    return Campaign(
        id=row["id"],
        name=row["name"],
        dm_id=row["dm_id"],
        role=role,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
