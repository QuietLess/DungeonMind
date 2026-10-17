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
import threading
import uuid
from collections import deque
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
SEARCHES_PER_MINUTE = 20
SESSIONS_PER_USER = 20
REFUNDS_PER_DAY = 3
LOGIN_FAILED = "Wrong username or password."
ENTRY_FIELDS = frozenset({"title", "body", "kind", "visibility", "session"})


def embedder_name(embedder: Embedder | None) -> str:
    return str(getattr(embedder, "name", None) or type(embedder).__name__)


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
        self._limiter = auth.Limiter(auth.LOGIN_WINDOW)
        self._registrations = auth.Limiter(auth.REGISTER_WINDOW)
        self._searches: dict[str, deque[datetime]] = {}
        self._search_lock = threading.Lock()
        # Verified against for unknown usernames, so both failures cost the same time (8).
        self._dummy_hash = auth.hash_password(secrets.token_urlsafe(16), scrypt_n)

    def healthy(self) -> bool:
        """Whether app.db answers a query that reads it (M9a O1)."""
        try:
            with self.storage.app() as db:
                db.execute("SELECT count(*) FROM users").fetchone()
        except Exception:
            return False
        return True

    # --- accounts --------------------------------------------------------------------------

    def register(self, username: str, password: str, client: str = "local") -> User:
        if not isinstance(username, str) or not re.fullmatch(models.USERNAME, username):
            raise Invalid("Usernames are 3–32 characters: lowercase letters, digits and _.")
        low, high = models.PASSWORD_CHARS
        if not isinstance(password, str) or not low <= len(password) <= high:
            raise Invalid(f"Passwords must be {low}–{high} characters.")
        if not self._registrations.reserve(
            [(f"register:{client}", auth.REGISTRATIONS_PER_CLIENT)], self.clock()
        ):
            raise RateLimited("Too many new accounts from this device. Try again later.")
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

    def login(self, username: str, password: str, client: str = "local") -> str:
        now = self.clock()
        if not isinstance(username, str) or not re.fullmatch(models.USERNAME, username):
            raise AuthFailed(LOGIN_FAILED)  # can't be an account; not worth tracking (F4)
        # Reserve the attempt before the password check, keyed per client (F4).
        keys = [
            (f"{client}|{username}", auth.LOGIN_FAILURES_PER_USER),
            (f"client:{client}", auth.LOGIN_FAILURES_PER_CLIENT),
        ]
        if not self._limiter.reserve(keys, now):
            raise RateLimited("Too many failed sign-in attempts. Try again in 15 minutes.")
        with self.storage.app() as db:
            row = db.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        ok = isinstance(password, str) and auth.verify_password(
            password, row["password_hash"] if row else self._dummy_hash
        )
        if row is None or not ok:
            raise AuthFailed(LOGIN_FAILED)  # the reserved attempt stays counted
        self._limiter.release(keys, now)
        return self.new_session(User(id=row["id"], username=row["username"]))

    def new_session(self, user: User) -> str:
        """A fresh session token for `user` (F5: register uses this, no second scrypt)."""
        now = self.clock()
        token = auth.new_token()
        expires = now + timedelta(days=auth.SESSION_DAYS)
        with self.storage.app() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))  # F7
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (auth.token_hash(token), user.id, _iso(expires)),
            )
            db.execute(  # F12: keep only the newest sessions per user
                # Insertion order, not expiry: clocks can stand still or step back.
                "DELETE FROM sessions WHERE user_id = ? AND token_hash NOT IN ("
                "SELECT token_hash FROM sessions WHERE user_id = ? "
                "ORDER BY rowid DESC LIMIT ?)",
                (user.id, user.id, SESSIONS_PER_USER),
            )
        return token

    def user_for_token(self, token: str) -> User | None:
        if not isinstance(token, str) or not token or len(token) > 200:
            return None
        with self.storage.app() as db:  # read-only: expired sessions are purged at login (F7)
            row = db.execute(
                "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (auth.token_hash(token), _iso(self.clock())),
            ).fetchone()
        if row is None:
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

    # --- account management (M9b) ----------------------------------------------------------

    def account(self, user: User) -> dict[str, Any]:
        """A1: the user's own account summary."""
        now = self.clock()
        day = now.astimezone(UTC).date().isoformat()
        with self.storage.app() as db:
            row = db.execute("SELECT created_at FROM users WHERE id = ?", (user.id,)).fetchone()
            if row is None:
                raise NotFound("No such user.")
            sessions = db.execute(
                "SELECT count(*) FROM sessions WHERE user_id = ? AND expires_at > ?",
                (user.id, _iso(now)),
            ).fetchone()[0]
            used = db.execute(
                "SELECT count FROM usage WHERE user_id = ? AND day = ?", (user.id, day)
            ).fetchone()
        return {
            "username": user.username,
            "created_at": row["created_at"],
            "sessions": sessions,
            "questions_today": used["count"] if used else 0,
            "daily_limit": self.daily_message_limit,
        }

    def _confirm_password(self, user: User, password: Any, client: str) -> None:
        """A2/A4: a password check counted exactly like a failed sign-in for this username."""
        now = self.clock()
        keys = [
            (f"{client}|{user.username}", auth.LOGIN_FAILURES_PER_USER),
            (f"client:{client}", auth.LOGIN_FAILURES_PER_CLIENT),
        ]
        if not self._limiter.reserve(keys, now):
            raise RateLimited("Too many wrong passwords. Try again in 15 minutes.")
        with self.storage.app() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        ok = isinstance(password, str) and auth.verify_password(
            password, row["password_hash"] if row else self._dummy_hash
        )
        if row is None or not ok:
            raise Forbidden("Current password is wrong.")
        self._limiter.release(keys, now)

    def change_password(
        self, user: User, token: str, current: Any, new: Any, client: str = "local"
    ) -> None:
        """A2: keeps the session `token`, ends every other session of the user."""
        low, high = models.PASSWORD_CHARS
        if not isinstance(new, str) or not low <= len(new) <= high:
            raise Invalid(f"Passwords must be {low}–{high} characters.")
        self._confirm_password(user, current, client)
        hashed = auth.hash_password(new, self.scrypt_n)
        with self.storage.app(immediate=True) as db:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user.id))
            db.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user.id, auth.token_hash(token)),
            )

    def sign_out_others(self, user: User, token: str) -> int:
        """A3: ends every session of the user except `token`; returns how many ended."""
        with self.storage.app() as db:
            return db.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user.id, auth.token_hash(token)),
            ).rowcount

    def delete_account(
        self, user: User, password: Any, confirm_username: Any, client: str = "local"
    ) -> None:
        """A4: private text and chats go first (every campaign folder), then the account."""
        if confirm_username != user.username:
            raise Invalid("Type your username to confirm.")
        self._confirm_password(user, password, client)
        with self.storage.app() as db:
            led = db.execute(
                "SELECT count(*) FROM campaigns WHERE dm_id = ?", (user.id,)
            ).fetchone()[0]
        if led:
            noun = "campaign" if led == 1 else "campaigns"
            raise Conflict(f"You're the DM of {led} {noun}. Delete them first.")
        for folder in sorted(self.storage.campaigns_root.iterdir()):
            if folder.is_symlink() or not CAMPAIGN_ID.fullmatch(folder.name):
                continue
            if not (folder / "campaign.db").is_file():
                continue
            with self.storage.campaign(folder.name, immediate=True) as db:
                db.execute(
                    "DELETE FROM entries WHERE author_id = ? AND visibility = 'private'",
                    (user.id,),
                )
                db.execute("DELETE FROM threads WHERE owner_id = ?", (user.id,))
        with self.storage.app(immediate=True) as db:
            db.execute("DELETE FROM usage WHERE user_id = ?", (user.id,))
            db.execute(
                "DELETE FROM users WHERE id = ?", (user.id,)
            )  # sessions, memberships cascade

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
        if self.embedder is not None:  # T2: the model that will embed this campaign's entries
            with self.storage.campaign(cid) as db:
                db.execute(
                    "INSERT INTO meta (key, value) VALUES ('embed_model', ?)",
                    (embedder_name(self.embedder),),
                )
        try:  # all-or-nothing (F10): a failed insert removes the new folder again
            with self.storage.app(immediate=True) as db:
                owned = db.execute(
                    "SELECT COUNT(*) FROM memberships WHERE user_id = ? AND role = 'dm'",
                    (user.id,),
                ).fetchone()[0]
                if owned >= models.MAX_CAMPAIGNS_PER_USER:
                    raise Invalid(f"You can run at most {models.MAX_CAMPAIGNS_PER_USER} campaigns.")
                db.execute(
                    "INSERT INTO campaigns (id, name, dm_id, created_at) VALUES (?, ?, ?, ?)",
                    (cid, name, user.id, now),
                )
                db.execute(
                    "INSERT INTO memberships (campaign_id, user_id, role, joined_at) "
                    "VALUES (?, ?, 'dm', ?)",
                    (cid, user.id, now),
                )
        except BaseException:
            shutil.rmtree(self.storage.campaign_folder(cid), ignore_errors=True)
            raise
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
        now = self.clock()
        expires = now + timedelta(days=INVITE_DAYS)
        with self.storage.app() as db:
            db.execute("DELETE FROM invites WHERE expires_at <= ?", (_iso(now),))  # F7
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
        chunks = self._embed(cid, title, body)
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
            store.replace_chunks(db, cur.lastrowid, title, chunks)
            row = store.get_entry(db, cur.lastrowid)
        return store.entry_from_row(row)

    def _check_model(self, cid: str) -> None:
        """T2: a campaign is searched and written only with the model that embedded it."""
        with self.storage.campaign(cid, immediate=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
            current = embedder_name(self.embedder)
            if row is None:  # a campaign from before M8d: adopt the current model
                db.execute("INSERT INTO meta (key, value) VALUES ('embed_model', ?)", (current,))
            elif row["value"] != current:
                raise StorageError(
                    f"This campaign was indexed with {row['value']}, but the hub now uses "
                    f"{current}. Run `dungeonmind hub reindex` to re-index this campaign."
                )

    def reindex(self, cid: str) -> int:
        """Re-embed every entry of a campaign with the current model (admin CLI only)."""
        if self.embedder is None or not CAMPAIGN_ID.fullmatch(cid):
            raise StorageError("Nothing to re-index with.")
        with self.storage.campaign(cid) as db:
            rows = db.execute("SELECT id, title, body FROM entries").fetchall()
        planned = [
            (r["id"], r["title"], store.embed_entry(self.embedder, r["title"], r["body"]))
            for r in rows
        ]
        with self.storage.campaign(cid, immediate=True) as db:
            for entry_id, title, chunks in planned:
                store.replace_chunks(db, entry_id, title, chunks)
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('embed_model', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (embedder_name(self.embedder),),
            )
        return len(planned)

    def _embed(self, cid: str, title: str, body: str) -> list[tuple[str, bytes]]:
        if self.embedder is None:
            raise StorageError("Search isn't available, so the entry wasn't saved.")
        self._check_model(cid)
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
        chunks = self._embed(cid, title, body) if rechunk else None
        with self.storage.campaign(cid, immediate=True) as db:
            current = store.get_entry(db, row["id"])
            if current is None or current["author_id"] != user.id:
                raise NotFound("Entry not found.")
            if current["version"] != row["version"]:  # F1: someone saved in between
                raise Conflict("This entry changed while you were editing. Reload and try again.")
            db.execute(
                "UPDATE entries SET title = ?, body = ?, kind = ?, visibility = ?, session = ?, "
                "version = version + 1, updated_at = ? WHERE id = ?",
                (title, body, kind, visibility, session, _iso(self.clock()), row["id"]),
            )
            if chunks is not None:
                store.replace_chunks(db, row["id"], title, chunks)
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
        self._count_search(user)
        return self._search(cid, self._viewer(user, camp), query, k)

    def _count_search(self, user: User) -> None:
        """At most SEARCHES_PER_MINUTE direct searches per user (F3); chat has its own limit."""
        now = self.clock()
        with self._search_lock:
            times = self._searches.setdefault(user.id, deque())
            while times and now - times[0] >= timedelta(minutes=1):
                times.popleft()
            if len(times) >= SEARCHES_PER_MINUTE:
                raise RateLimited("Too many searches. Wait a minute and try again.")
            times.append(now)

    def _search(self, cid: str, viewer: Viewer, query: str, k: int) -> list[NoteHit]:
        if self.embedder is None:
            raise StorageError("Search isn't available.")
        self._check_model(cid)
        with self.storage.campaign(cid) as db:
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

    def rename_thread(self, user: User, cid: str, tid: int, title: str) -> Thread:
        """CT1: the owner only; anyone else's thread doesn't exist for them."""
        self._member(user, cid)
        title = _text(title, "Title", models.THREAD_TITLE_CHARS)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._own_thread(db, user, tid)
            db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, row["id"]))
            row = db.execute("SELECT * FROM threads WHERE id = ?", (row["id"],)).fetchone()
        return thread_from_row(row)

    def delete_thread(self, user: User, cid: str, tid: int) -> None:
        """CT1: removes the thread; its messages and encounter state cascade."""
        self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._own_thread(db, user, tid)
            db.execute("DELETE FROM threads WHERE id = ?", (row["id"],))

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
            history = _history(db, thread_id)
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
            state = self.engine(
                question, search_notes, thread_store, f"thread-{thread_id}", history
            )
        except Exception:
            # The engine failed before producing a state: nothing was billed, so refund (F8).
            # A graph "error" state may already have cost a model call and is not refunded.
            state = {"stop_reason": "error"}
            self._refund_message(user)
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

    def _refund_message(self, user: User) -> None:
        day = self.clock().astimezone(UTC).date().isoformat()
        with self.storage.app(immediate=True) as db:
            db.execute(
                "UPDATE usage SET count = count - 1, refunds = refunds + 1 "
                "WHERE user_id = ? AND day = ? AND count > 0 AND refunds < ?",
                (user.id, day, REFUNDS_PER_DAY),
            )

    def _save_message(
        self, cid: str, thread_id: int, role: str, text: str, meta: dict[str, Any]
    ) -> Message:
        with self.storage.campaign(cid, immediate=True) as db:
            if db.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone() is None:
                raise NotFound("Chat not found.")  # CT2: deleted while it was being answered
            cur = db.execute(
                "INSERT INTO messages (thread_id, role, text, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, role, text, json.dumps(meta, default=str), _iso(self.clock())),
            )
            row = db.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return message_from_row(row)


def _history(db, thread_id: int, exchanges: int = 3) -> list[dict[str, str]]:
    """The thread's last answered exchanges (UI8), oldest first; failed answers are skipped."""
    rows = db.execute(
        "SELECT role, text, meta FROM messages WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
        (thread_id, exchanges * 4),
    ).fetchall()[::-1]
    pairs: list[dict[str, str]] = []
    for question, answer in zip(rows, rows[1:], strict=False):
        if (
            question["role"] == "user"
            and answer["role"] == "assistant"
            and json.loads(answer["meta"]).get("stop_reason") != "error"
        ):
            pairs.append({"question": question["text"], "answer": answer["text"]})
    return pairs[-exchanges:]


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
