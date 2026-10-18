"""Hub: the single place that checks membership, roles and authorship (M6 contract).

Every public method that touches a campaign first resolves the caller's membership
(`_member`); a non-member, a malformed id or an entry/thread the caller can't read is
`NotFound`, so responses never reveal what exists. `Forbidden` is only for things the caller
can see but their role doesn't allow them to change.
"""

from __future__ import annotations

import json
import random
import re
import secrets
import shutil
import threading
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dungeonmind.graph.state import NoteHit
from dungeonmind.hub import auth, models
from dungeonmind.hub import entries as store
from dungeonmind.hub import rolls as dice_rolls
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
from dungeonmind.hub.sheets import Sheet, derive, sheet_from_monster, summary_text

DEFAULT_ROOT = Path("data/hub")
INVITE_DAYS = 7
INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
INVITE_LENGTH = 12
MAX_SEARCH_K = 50
SEARCHES_PER_MINUTE = 20
SESSIONS_PER_USER = 20
ROLLS_PER_MINUTE = 30  # M10 R4
ROLLS_KEPT = 500  # M10 R3: per campaign
REFUNDS_PER_DAY = 3
LOGIN_FAILED = "Wrong username or password."
SIGNUP_MODES = ("open", "invite")
NEEDS_INVITE = "Ask your DM for an invite code."
# M9e H2: records the oldest account as owner unless an owner is already recorded.
OWNER_FROM_OLDEST = (
    "INSERT OR IGNORE INTO settings (key, value) "
    "SELECT 'owner_id', id FROM users ORDER BY rowid LIMIT 1"
)
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
    stripped = unicodedata.normalize("NFC", value.strip())  # one form for every stored text
    if (not stripped and not allow_empty) or len(value) > max_chars or len(stripped) > max_chars:
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


MAX_ID = 2**63 - 1  # SQLite INTEGER; larger ids would overflow (M10.1 P1)


def _id(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ID:
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
        signup: str = "open",
        read_only: bool = False,
        rng: random.Random | None = None,
        monsters: Callable[[str], dict[str, Any] | None] | None = None,
        monster_names: Callable[[str], list[str]] | None = None,
    ) -> None:
        if signup not in SIGNUP_MODES:
            raise ValueError(f"signup must be one of {', '.join(SIGNUP_MODES)}")
        self.signup = signup  # M9d S1: "invite" = sign-up only with a campaign invite code
        self.storage = Storage(root, read_only=read_only)
        if not read_only:  # review R1: a hub from before M9e records its owner at start-up
            with self.storage.app(immediate=True) as db:
                db.execute(OWNER_FROM_OLDEST)
        self.embedder = embedder
        self.engine = engine
        self.clock = clock
        self.scrypt_n = scrypt_n
        self.daily_message_limit = daily_message_limit
        self._limiter = auth.Limiter(auth.LOGIN_WINDOW)
        # H1: keyed by account id, existing accounts only, so it stays bounded and unflushable.
        self._accounts = auth.Limiter(auth.LOGIN_WINDOW)
        self._registrations = auth.Limiter(auth.REGISTER_WINDOW)
        self._searches: dict[str, deque[datetime]] = {}
        self._roll_times: dict[str, deque[datetime]] = {}  # R4
        self.rng = rng  # None: the dice tool uses SystemRandom
        self.monsters = monsters  # M11 N3: SRD stat block by name or key
        self.monster_names = monster_names
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

    def register(
        self, username: str, password: str, client: str = "local", invite: Any = None
    ) -> User:
        if not isinstance(username, str) or not re.fullmatch(models.USERNAME, username):
            raise Invalid("Usernames are 3–32 characters: lowercase letters, digits and _.")
        low, high = models.PASSWORD_CHARS
        if not isinstance(password, str) or not low <= len(password) <= high:
            raise Invalid(f"Passwords must be {low}–{high} characters.")
        if not self._registrations.reserve(
            [(f"register:{client}", auth.REGISTRATIONS_PER_CLIENT)], self.clock()
        ):
            raise RateLimited("Too many new accounts from this device. Try again later.")
        needs_invite = self.signup == "invite"
        first = False
        if needs_invite:  # S1: checked before the (slow) password hash
            with self.storage.app() as db:
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
                    needs_invite, first = False, True  # S1: the owner's first account
                elif self._live_invite(db, invite) is None:
                    raise Forbidden(NEEDS_INVITE)
        user = User(id=uuid.uuid4().hex, username=username)
        hashed = auth.hash_password(password, self.scrypt_n)
        now = self.clock()
        with self.storage.app(immediate=True) as db:
            invite_row = self._live_invite(db, invite) if needs_invite else None
            if needs_invite and invite_row is None:  # used up while the password was hashed
                raise Forbidden(NEEDS_INVITE)
            if first and db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise Forbidden(NEEDS_INVITE)  # someone else became the first account meanwhile
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise Conflict("That username is taken.")
            db.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user.id, username, hashed, _iso(now)),
            )
            db.execute(OWNER_FROM_OLDEST)  # H2: the first account (or a pre-M9e hub's oldest)
            if invite_row is not None:  # S1: the new account joins that campaign
                members = db.execute(
                    "SELECT COUNT(*) FROM memberships WHERE campaign_id = ?",
                    (invite_row["campaign_id"],),
                ).fetchone()[0]
                if members >= models.MAX_MEMBERS:
                    raise Invalid(f"A campaign can have at most {models.MAX_MEMBERS} members.")
                db.execute(
                    "INSERT INTO memberships (campaign_id, user_id, role, joined_at) "
                    "VALUES (?, ?, 'player', ?)",
                    (invite_row["campaign_id"], user.id, _iso(now)),
                )
                db.execute("DELETE FROM invites WHERE code_hash = ?", (invite_row["code_hash"],))
        return user

    def _live_invite(self, db, code: Any):
        """The unexpired invite row for `code` that may create an account, or None.

        Review R1: only invites to campaigns the hub's owner (its first account) runs can
        create accounts; otherwise one invited player could mint unlimited accounts, each with
        its own daily model budget. Invites to other campaigns still work for existing accounts.
        """
        if not isinstance(code, str) or not code.strip():
            return None
        row = db.execute(
            "SELECT i.code_hash, i.campaign_id, i.expires_at FROM invites i "
            "JOIN campaigns c ON c.id = i.campaign_id "
            "WHERE i.code_hash = ? AND c.dm_id = ("
            "SELECT value FROM settings WHERE key = 'owner_id')",
            (auth.token_hash(_invite_code(code)),),
        ).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) <= self.clock():
            return None
        return row

    def owner_id(self) -> str | None:
        """H2: the hub's owner; a hub from before M9e records its oldest account now."""
        with self.storage.app(immediate=True) as db:
            db.execute(OWNER_FROM_OLDEST)
            row = db.execute("SELECT value FROM settings WHERE key = 'owner_id'").fetchone()
        return row["value"] if row else None

    def signup_config(self) -> dict[str, Any]:
        """S2: the sign-up mode, and whether the hub is still empty (the owner signs up first)."""
        with self.storage.app() as db:
            empty = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
        return {"signup": self.signup, "first": empty}

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
        account = [(row["id"], auth.LOGIN_FAILURES_PER_ACCOUNT)] if row else []
        # Review R6: at the account limit the answer (and the scrypt cost) is the same as for a
        # wrong password, so the limit can't be used to learn which usernames exist.
        locked = bool(account) and not self._accounts.reserve(account, now)
        ok = isinstance(password, str) and auth.verify_password(
            password, self._dummy_hash if locked or row is None else row["password_hash"]
        )
        if row is None or locked or not ok:
            raise AuthFailed(LOGIN_FAILED)  # the reserved attempt stays counted
        self._limiter.release(keys, now)
        self._accounts.release(account, now)
        user = User(id=row["id"], username=row["username"])
        return self.new_session(user, verified_hash=row["password_hash"])

    def new_session(self, user: User, verified_hash: str | None = None) -> str:
        """A fresh session token for `user` (F5: register uses this, no second scrypt).

        `verified_hash` is the hash a sign-in checked; if the password changed meanwhile, no
        session is made (R4), so a change really ends every sign-in with the old password.
        """
        now = self.clock()
        token = auth.new_token()
        expires = now + timedelta(days=auth.SESSION_DAYS)
        with self.storage.app(immediate=True) as db:
            if verified_hash is not None:
                current = db.execute(
                    "SELECT password_hash FROM users WHERE id = ?", (user.id,)
                ).fetchone()
                if current is None or current["password_hash"] != verified_hash:
                    raise AuthFailed(LOGIN_FAILED)
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
        account = [(user.id, auth.LOGIN_FAILURES_PER_ACCOUNT)]
        if not self._accounts.reserve(account, now):
            raise RateLimited("Too many wrong passwords. Try again in 15 minutes.")
        with self.storage.app() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        ok = isinstance(password, str) and auth.verify_password(
            password, row["password_hash"] if row else self._dummy_hash
        )
        if row is None or not ok:
            raise Forbidden("Current password is wrong.")
        self._limiter.release(keys, now)
        self._accounts.release(account, now)

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
        if user.id == self.owner_id():  # H2: ownership never moves
            raise Conflict("The hub's owner can't delete their account.")
        with self.storage.app() as db:
            self._refuse_if_dm(db, user)
        self._sweep_private(user, checkpoint=False)
        with self.storage.app(immediate=True) as db:
            self._refuse_if_dm(db, user)  # R2: a campaign created meanwhile is a 409, not a 500
            db.execute("PRAGMA secure_delete = ON")
            db.execute("DELETE FROM usage WHERE user_id = ?", (user.id,))
            db.execute(
                "DELETE FROM users WHERE id = ?", (user.id,)
            )  # sessions, memberships cascade
        # R1: anything written between the first sweep and the account's removal goes too;
        # writes after it are undone by the writer (create_entry / create_thread check).
        self._sweep_private(user)

    @staticmethod
    def _refuse_if_dm(db, user: User) -> None:
        led = db.execute("SELECT count(*) FROM campaigns WHERE dm_id = ?", (user.id,)).fetchone()[0]
        if led:
            noun = "campaign" if led == 1 else "campaigns"
            raise Conflict(f"You're the DM of {led} {noun}. Delete them first.")

    def _sweep_private(self, user: User, checkpoint: bool = True) -> None:
        """Deletes the user's private entries and chat threads in every campaign folder, with
        the freed pages zeroed and the WAL truncated (R3: no readable copy left on disk).

        A reader holding the file (e.g. a running backup) delays the truncation; the wipe
        then completes at SQLite's next checkpoint (R-B, documented)."""
        for folder in sorted(self.storage.campaigns_root.iterdir()):
            if folder.is_symlink() or not CAMPAIGN_ID.fullmatch(folder.name):
                continue
            if not (folder / "campaign.db").is_file():
                continue
            with self.storage.campaign(folder.name, immediate=True) as db:
                db.execute("PRAGMA secure_delete = ON")
                db.execute(
                    "DELETE FROM entries WHERE author_id = ? AND visibility = 'private'",
                    (user.id,),
                )
                db.execute("DELETE FROM threads WHERE owner_id = ?", (user.id,))
            if checkpoint:
                self.storage.checkpoint(folder.name)

    def _gone(self, user: User) -> bool:
        with self.storage.app() as db:
            return db.execute("SELECT 1 FROM users WHERE id = ?", (user.id,)).fetchone() is None

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

    def create_character(
        self,
        user: User,
        cid: str,
        name: str,
        summary: str = "",
        npc: bool = False,
        from_encounter: int | None = None,
    ) -> Character:
        camp = self._member(user, cid)
        if npc and camp.role != "dm":
            raise Forbidden("Only the DM creates NPCs.")  # N1
        name = _text(name, "Character name", models.CHARACTER_NAME_CHARS)
        summary = _text(summary, "Summary", models.CHARACTER_SUMMARY_CHARS, allow_empty=True)
        with self.storage.campaign(cid) as db:
            cur = db.execute(
                "INSERT INTO characters (owner_id, name, summary, created_at, is_npc, "
                "from_encounter) VALUES (?, ?, ?, ?, ?, ?)",
                (user.id, name, summary, _iso(self.clock()), int(bool(npc)), from_encounter),
            )
            row = db.execute("SELECT * FROM characters WHERE id = ?", (cur.lastrowid,)).fetchone()
        return store.character_from_row(row)

    def characters(self, user: User, cid: str) -> list[Character]:
        camp = self._member(user, cid)
        npcs = "" if camp.role == "dm" else " WHERE is_npc = 0"  # N1: players never see NPCs
        with self.storage.campaign(cid) as db:
            rows = db.execute(f"SELECT * FROM characters{npcs} ORDER BY name, id").fetchall()
        return [store.character_from_row(r) for r in rows]

    def _own_character(self, db, user: User, chid: Any):
        row = db.execute("SELECT * FROM characters WHERE id = ?", (_id(chid, "Character"),))
        row = row.fetchone()
        if row is None or (row["is_npc"] and row["owner_id"] != user.id):
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
            renamed = new_name != row["name"]
            row = db.execute("SELECT * FROM characters WHERE id = ?", (row["id"],)).fetchone()
        if renamed:
            self._resync_sheet_entry(cid, row["id"])  # F6
        return store.character_from_row(row)

    def delete_character(self, user: User, cid: str, chid: int) -> None:
        self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._own_character(db, user, chid)
            count = db.execute(
                "SELECT COUNT(*) FROM entries WHERE character_id = ? AND kind != 'sheet'",
                (row["id"],),
            ).fetchone()[0]
            if count:
                raise Conflict(
                    f"This character's board still has {count} entries; delete them first."
                )
            db.execute(  # C2: the generated sheet entry goes with the character
                "DELETE FROM entries WHERE character_id = ? AND kind = 'sheet'", (row["id"],)
            )
            db.execute("DELETE FROM characters WHERE id = ?", (row["id"],))

    # --- character sheets (M10a) -------------------------------------------------------------

    def _character_row(self, db, chid: Any, camp: Campaign | None = None):
        """A character row; with `camp`, NPCs exist only for the DM (N1)."""
        row = db.execute("SELECT * FROM characters WHERE id = ?", (_id(chid, "Character"),))
        row = row.fetchone()
        if row is None or (camp is not None and row["is_npc"] and camp.role != "dm"):
            raise NotFound("Character not found.")
        return row

    @staticmethod
    def _load_sheet(db, character_id: int) -> tuple[Sheet, int]:
        row = db.execute(
            "SELECT data, version FROM sheets WHERE character_id = ?", (character_id,)
        ).fetchone()
        if row is None:
            return Sheet(), 0
        return Sheet.model_validate_json(row["data"]), row["version"]

    def sheet(self, user: User, cid: str, chid: int) -> dict[str, Any]:
        """S3: every member may view; the player and the DM may edit."""
        camp = self._member(user, cid)
        with self.storage.campaign(cid) as db:
            character = self._character_row(db, chid, camp)
            sheet, version = self._load_sheet(db, character["id"])
        return {
            "sheet": sheet.model_dump(),
            "derived": derive(sheet),
            "version": version,
            "can_edit": character["owner_id"] == user.id or camp.role == "dm",
        }

    def save_sheet(
        self, user: User, cid: str, chid: int, data: Any, version: Any
    ) -> dict[str, Any]:
        """S3 + C1: the sheet and its generated entry are written in one transaction, after the
        slow work (validation, summary, embedding), so a failure saves neither (review F1)."""
        camp = self._member(user, cid)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise Invalid("version must be a whole number.")
        try:
            sheet = Sheet.model_validate(data)
        except ValidationError as e:
            err = e.errors()[0]
            where = ".".join(str(p) for p in err.get("loc", ()))
            message = f"{where}: {err.get('msg', 'invalid value')}" if where else "Invalid sheet."
            raise Invalid(message) from None
        with self.storage.campaign(cid) as db:
            character = self._character_row(db, chid, camp)
        if character["owner_id"] != user.id and camp.role != "dm":
            raise Forbidden("Only the character's player or the DM can edit this sheet.")
        derived = derive(sheet)
        for _attempt in range(3):  # P2: rebuild the entry if a rename lands meanwhile
            entry = self._sheet_entry_parts(cid, character, sheet, derived)
            with self.storage.campaign(cid, immediate=True) as db:
                fresh = self._character_row(db, chid)  # still there (F7)
                if fresh["name"] != character["name"]:
                    character = fresh
                    continue
                _, current = self._load_sheet(db, fresh["id"])
                if current != version:
                    raise Conflict(
                        "This sheet changed while you were editing. Reload and try again."
                    )
                db.execute(
                    "INSERT INTO sheets (character_id, data, version, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(character_id) DO UPDATE SET "
                    "data = excluded.data, version = excluded.version, "
                    "updated_at = excluded.updated_at",
                    (fresh["id"], sheet.model_dump_json(), version + 1, _iso(self.clock())),
                )
                self._write_sheet_entry(db, fresh, *entry)
                break
        else:
            raise Conflict("The character was renamed while saving. Try again.")
        return {
            "sheet": sheet.model_dump(),
            "derived": derived,
            "version": version + 1,
            "can_edit": True,
        }

    def _sheet_entry_parts(self, cid: str, character, sheet: Sheet, derived: dict[str, Any]):
        """Title, text, embedded chunks and author name of a sheet's generated entry (slow).
        Chunks are None when the entry already says exactly this (N4: no re-embedding)."""
        title = f"{character['name']} — character sheet"[: models.TITLE_CHARS]
        body = summary_text(character["name"], sheet, derived)
        with self.storage.campaign(cid) as db:
            existing = db.execute(
                "SELECT title, body FROM entries WHERE character_id = ? AND kind = 'sheet'",
                (character["id"],),
            ).fetchone()
        unchanged = existing is not None and (existing["title"], existing["body"]) == (title, body)
        chunks = None if unchanged else self._embed(cid, title, body)
        with self.storage.app() as db:
            owner = db.execute(
                "SELECT username FROM users WHERE id = ?", (character["owner_id"],)
            ).fetchone()
        return title, body, chunks, owner["username"] if owner else "player"

    def _write_sheet_entry(self, db, character, title, body, chunks, author_name) -> None:
        """C1: one generated, party-visible entry per sheet, written as the character's player,
        so the chatbot finds and cites sheets through the normal notes search."""
        now = _iso(self.clock())
        row = db.execute(
            "SELECT id, title, body FROM entries WHERE character_id = ? AND kind = 'sheet'",
            (character["id"],),
        ).fetchone()
        if chunks is None and row is not None and (row["title"], row["body"]) == (title, body):
            return  # N4: nothing to re-index
        if chunks is None:  # changed since it was checked: index it now
            chunks = self._embed_unlocked(title, body)
        visibility = "private" if character["is_npc"] else "party"  # N2: NPCs are the DM's
        if row is None:
            cur = db.execute(
                "INSERT INTO entries (board, character_id, author_id, author_name, kind, "
                "title, body, visibility, session, created_at, updated_at) "
                "VALUES ('character', ?, ?, ?, 'sheet', ?, ?, ?, NULL, ?, ?)",
                (
                    character["id"],
                    character["owner_id"],
                    author_name,
                    title,
                    body,
                    visibility,
                    now,
                    now,
                ),
            )
            entry_id = cur.lastrowid
        else:
            entry_id = row["id"]
            db.execute(
                "UPDATE entries SET title = ?, body = ?, visibility = ?, version = version + 1, "
                "updated_at = ? WHERE id = ?",
                (title, body, visibility, now, entry_id),
            )
        store.replace_chunks(db, entry_id, title, chunks)

    def _embed_unlocked(self, title: str, body: str):
        if self.embedder is None:
            raise StorageError("Search isn't available, so the entry wasn't saved.")
        try:
            return store.embed_entry(self.embedder, title, body)
        except Exception as e:
            raise StorageError("Couldn't index this entry right now; it wasn't saved.") from e

    def _resync_sheet_entry(self, cid: str, chid: int) -> None:
        """F6: after a rename, the generated entry follows the character's new name."""
        with self.storage.campaign(cid) as db:
            character = self._character_row(db, chid)
            row = db.execute(
                "SELECT version FROM sheets WHERE character_id = ?", (character["id"],)
            ).fetchone()
            if row is None:
                return
            sheet, version = self._load_sheet(db, character["id"])
        try:
            entry = self._sheet_entry_parts(cid, character, sheet, derive(sheet))
        except StorageError:
            return  # review N1: the rename stands; the next sheet save refreshes the entry
        with self.storage.campaign(cid, immediate=True) as db:
            character = self._character_row(db, chid)
            _, current = self._load_sheet(db, character["id"])
            if current == version:  # a newer save already wrote a fresh entry otherwise
                self._write_sheet_entry(db, character, *entry)

    # --- dice and the game log (M10b) ---------------------------------------------------------

    def _count_roll(self, user: User) -> None:
        now = self.clock()
        with self._search_lock:
            times = self._roll_times.setdefault(user.id, deque())
            while times and now - times[0] >= timedelta(minutes=1):
                times.popleft()
            if len(times) >= ROLLS_PER_MINUTE:
                raise RateLimited("Too many rolls. Wait a minute and try again.")
            times.append(now)

    def roll(
        self,
        user: User,
        cid: str,
        *,
        kind: str,
        key: str | None = None,
        mode: str = "normal",
        to_dm: bool = False,
        character_id: int | None = None,
        expression: str | None = None,
        dice: dict[str, Any] | None = None,
        stat: str | None = None,
        modifier: int | None = None,
    ) -> dict[str, Any]:
        """R1/R2 + M11 D1/D2: rolls on the server with the sheet's bonus and logs it."""
        camp = self._member(user, cid)
        if kind not in dice_rolls.KINDS:
            raise Invalid("Unknown roll.")
        if mode not in dice_rolls.MODES:
            raise Invalid("Mode must be normal, advantage, disadvantage or critical.")
        name = user.username
        character = sheet = None
        if character_id is not None or (kind not in ("expression", "pool")):
            if character_id is None:
                raise Invalid("Which character is rolling?")
            with self.storage.campaign(cid) as db:
                character = self._character_row(db, character_id, camp)
                if character["owner_id"] != user.id and camp.role != "dm":
                    raise Forbidden("Only the character's player or the DM can roll for them.")
                sheet, _ = self._load_sheet(db, character["id"])
            name = character["name"]
        if kind == "expression":
            spec = dice_rolls.spec_for_expression(expression, mode)
            character = None
            name = user.username
        elif kind == "pool":
            if stat and character is None:
                raise Invalid("Pick who is rolling to add a stat.")
            derived = derive(sheet) if sheet is not None else None
            spec = dice_rolls.spec_for_pool(dice, mode, modifier or 0, stat, sheet, derived)
        else:
            spec = dice_rolls.spec_for(kind, key, mode, sheet, derive(sheet))
        self._count_roll(user)
        detail = dice_rolls.perform(spec, self.rng)
        if kind == "pool" and modifier:
            detail["modifier"] = modifier
        row = self._log_roll(
            cid,
            user,
            character["id"] if character else None,
            f"{name} — {spec.label}",
            detail,
            mode,
            to_dm,
        )
        lone_d20 = kind == "pool" and isinstance(dice, dict) and dice == {"20": 1}
        if character is not None and (
            kind == "initiative" or (stat == "initiative" and lone_d20)
        ):  # C2: only a d20 initiative roll fills a slot (review F3)
            self._fill_initiative(cid, character["id"], detail["total"])
        return row

    def _log_roll(self, cid, user, character_id, label, detail, mode, to_dm) -> dict[str, Any]:
        now = _iso(self.clock())
        with self.storage.campaign(cid, immediate=True) as db:
            cur = db.execute(
                "INSERT INTO rolls (user_id, author_name, character_id, label, expression, detail, "
                "total, to_dm, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user.id,
                    user.username,
                    character_id,
                    label,
                    detail["expression"],
                    json.dumps({**detail, "mode": mode}),
                    detail["total"],
                    int(bool(to_dm)),
                    now,
                ),
            )
            db.execute(  # R3: keep the newest rolls only
                "DELETE FROM rolls WHERE id NOT IN (SELECT id FROM rolls ORDER BY id DESC LIMIT ?)",
                (ROLLS_KEPT,),
            )
            row = db.execute("SELECT * FROM rolls WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _roll_from_row(row)

    # --- NPCs from SRD monsters (M11a) --------------------------------------------------------

    def monster_search(self, user: User, cid: str, q: str) -> list[str]:
        self._dm(user, cid)
        q = _text(q, "Search", 60, allow_empty=True)
        return (self.monster_names(q) if self.monster_names else [])[:20]

    def npcs_from_monster(
        self, user: User, cid: str, monster: str, count: int = 1, from_encounter: int | None = None
    ) -> list[Character]:
        """N3: an SRD stat block copied into `count` NPC sheets."""
        self._dm(user, cid)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise Invalid("Add 1–10 at a time.")
        record = self.monsters(str(monster)[:100]) if self.monsters else None
        if record is None:
            raise NotFound("No such monster in the SRD.")
        sheet = sheet_from_monster(record)
        base = record["name"]
        with self.storage.campaign(cid) as db:  # F12: numbering continues across additions
            taken = [
                r["name"][len(base) + 1 :]
                for r in db.execute("SELECT name FROM characters WHERE name LIKE ?", (f"{base} %",))
            ]
        start = max([int(t) for t in taken if t.isdigit()] or [0]) + 1
        numbered = count > 1 or from_encounter or taken
        made = []
        for n in range(start, start + count):
            name = f"{base} {n}" if numbered else base
            npc = self.create_character(
                user,
                cid,
                name[: models.CHARACTER_NAME_CHARS],
                npc=True,
                from_encounter=from_encounter,
            )
            self.save_sheet(user, cid, npc.id, sheet.model_dump(), 0)
            made.append(npc)
        return made

    # --- combat tracker (M11c) ----------------------------------------------------------------

    @staticmethod
    def _active_encounter(db):
        return db.execute("SELECT * FROM encounters WHERE active = 1 ORDER BY id DESC").fetchone()

    def _encounter(self, db, eid: Any):
        row = db.execute(
            "SELECT * FROM encounters WHERE id = ? AND active = 1", (_id(eid, "Encounter"),)
        ).fetchone()
        if row is None:
            raise NotFound("Encounter not found.")
        return row

    def start_encounter(self, user: User, cid: str, name: str) -> dict[str, Any]:
        self._dm(user, cid)
        name = _text(name or "Encounter", "Encounter name", 80)
        with self.storage.campaign(cid, immediate=True) as db:
            if self._active_encounter(db) is not None:
                raise Conflict("An encounter is already running. End it first.")
            cur = db.execute(
                "INSERT INTO encounters (name, created_at) VALUES (?, ?)",
                (name, _iso(self.clock())),
            )
        return {"id": cur.lastrowid, "name": name}

    def add_combatants(
        self,
        user: User,
        cid: str,
        eid: int,
        *,
        character_id: int | None = None,
        monster: str | None = None,
        count: int = 1,
    ) -> list[dict[str, Any]]:
        self._dm(user, cid)
        with self.storage.campaign(cid) as db:
            self._encounter(db, eid)
        if monster:
            ids = [c.id for c in self.npcs_from_monster(user, cid, monster, count, eid)]
        elif character_id is not None:
            with self.storage.campaign(cid) as db:
                ids = [self._character_row(db, character_id)["id"]]
        else:
            raise Invalid("Add a character or a monster.")
        added = []
        with self.storage.campaign(cid, immediate=True) as db:
            self._encounter(db, eid)
            for chid in ids:
                if db.execute(
                    "SELECT 1 FROM combatants WHERE encounter_id = ? AND character_id = ?",
                    (eid, chid),
                ).fetchone():
                    raise Conflict("That character is already in this encounter.")
                position = db.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM combatants WHERE encounter_id = ?",
                    (eid,),
                ).fetchone()[0]
                cur = db.execute(
                    "INSERT INTO combatants (encounter_id, character_id, position) "
                    "VALUES (?, ?, ?)",
                    (eid, chid, position),
                )
                added.append({"id": cur.lastrowid, "character_id": chid})
        return added

    def update_combatant(
        self, user: User, cid: str, eid: int, combatant: int, *, initiative=None, hidden=None
    ) -> None:
        self._dm(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            self._encounter(db, eid)
            row = self._combatant(db, eid, combatant)
            if initiative is not None:
                if isinstance(initiative, bool) or not isinstance(initiative, int):
                    raise Invalid("Initiative must be a whole number.")
                if not -20 <= initiative <= 60:
                    raise Invalid("Initiative must be between -20 and 60.")
                sheet, _ = self._load_sheet(db, row["character_id"])
                db.execute(  # the sheet's bonus still breaks ties (review F14)
                    "UPDATE combatants SET initiative = ?, init_bonus = ? WHERE id = ?",
                    (initiative, derive(sheet)["initiative"], row["id"]),
                )
            if hidden is not None:
                db.execute(
                    "UPDATE combatants SET hidden = ? WHERE id = ?", (int(bool(hidden)), row["id"])
                )

    def remove_combatant(self, user: User, cid: str, eid: int, combatant: int) -> None:
        self._dm(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            self._encounter(db, eid)
            enc = self._encounter(db, eid)
            row = self._combatant(db, eid, combatant)
            if enc["current_combatant"] == row["id"]:  # the turn passes to the next one
                order = self._order(self._rows(db, eid))
                i = [r["id"] for r in order].index(row["id"])
                rnd = enc["round"]
                if len(order) == 1:
                    nxt = None
                elif i + 1 < len(order):
                    nxt = order[i + 1]["id"]
                else:
                    nxt, rnd = order[0]["id"], rnd + 1
                db.execute(
                    "UPDATE encounters SET current_combatant = ?, round = ? WHERE id = ?",
                    (nxt, rnd, eid),
                )
            db.execute("DELETE FROM combatants WHERE id = ?", (row["id"],))

    @staticmethod
    def _rows(db, eid: int):
        return db.execute("SELECT * FROM combatants WHERE encounter_id = ?", (eid,)).fetchall()

    @staticmethod
    def _combatant(db, eid: int, combatant: Any):
        row = db.execute(
            "SELECT * FROM combatants WHERE id = ? AND encounter_id = ?",
            (_id(combatant, "Combatant"), eid),
        ).fetchone()
        if row is None:
            raise NotFound("Combatant not found.")
        return row

    @staticmethod
    def _order(rows) -> list:
        """C2: initiative, then initiative bonus, then the order added; unrolled last."""
        return sorted(
            rows,
            key=lambda r: (
                r["initiative"] is None,
                -(r["initiative"] or 0),
                -r["init_bonus"],
                r["position"],
            ),
        )

    def roll_initiative(self, user: User, cid: str, eid: int, only_missing: bool = True) -> None:
        """C2: d20 + the sheet's initiative for each combatant, logged (NPCs to the DM)."""
        self._dm(user, cid)
        with self.storage.campaign(cid) as db:
            self._encounter(db, eid)
            rows = db.execute(
                "SELECT c.*, ch.name, ch.is_npc FROM combatants c "
                "JOIN characters ch ON ch.id = c.character_id WHERE c.encounter_id = ?",
                (eid,),
            ).fetchall()
            sheets = {r["character_id"]: self._load_sheet(db, r["character_id"])[0] for r in rows}
        results = {}
        for r in rows:
            if only_missing and r["initiative"] is not None:
                continue
            sheet = sheets[r["character_id"]]
            derived = derive(sheet)
            spec = dice_rolls.spec_for("initiative", None, "normal", sheet, derived)
            detail = dice_rolls.perform(spec, self.rng)
            self._log_roll(
                cid,
                user,
                r["character_id"],
                f"{r['name']} — Initiative",
                detail,
                "normal",
                bool(r["is_npc"]),
            )
            results[r["id"]] = (detail["total"], derived["initiative"])
        with self.storage.campaign(cid, immediate=True) as db:
            self._encounter(db, eid)
            enc = self._encounter(db, eid)
            for combatant, (total, bonus) in results.items():
                db.execute(
                    "UPDATE combatants SET initiative = ?, init_bonus = ? WHERE id = ?",
                    (total, bonus, combatant),
                )
            if enc["current_combatant"] is None:  # the fight starts at the top of the order
                order = self._order(self._rows(db, eid))
                if order:
                    db.execute(
                        "UPDATE encounters SET current_combatant = ? WHERE id = ?",
                        (order[0]["id"], eid),
                    )

    def _fill_initiative(self, cid: str, character_id: int, total: int) -> None:
        """C2: a character's own Initiative roll fills its empty slot in the active encounter."""
        with self.storage.campaign(cid, immediate=True) as db:
            enc = self._active_encounter(db)
            if enc is None:
                return
            sheet, _ = self._load_sheet(db, character_id)
            db.execute(
                "UPDATE combatants SET initiative = ?, init_bonus = ? "
                "WHERE encounter_id = ? AND character_id = ? AND initiative IS NULL",
                (max(-20, min(60, total)), derive(sheet)["initiative"], enc["id"], character_id),
            )

    def next_turn(self, user: User, cid: str, eid: int, direction: str = "next") -> None:
        """C3: moves to the next / previous combatant in the current order; the round goes up
        when the order wraps (and back down going back). Tracked by combatant (review F1)."""
        self._dm(user, cid)
        if direction not in ("next", "prev"):
            raise Invalid("direction must be next or prev.")
        with self.storage.campaign(cid, immediate=True) as db:
            enc = self._encounter(db, eid)
            ids = [r["id"] for r in self._order(self._rows(db, eid))]
            if not ids:
                return
            rnd = enc["round"]
            if enc["current_combatant"] not in ids:
                current = ids[0]
            else:
                i = ids.index(enc["current_combatant"])
                if direction == "next":
                    if i + 1 < len(ids):
                        current = ids[i + 1]
                    else:
                        current, rnd = ids[0], rnd + 1
                elif i > 0:
                    current = ids[i - 1]
                elif rnd > 1:
                    current, rnd = ids[-1], rnd - 1
                else:
                    current = ids[0]
            db.execute(
                "UPDATE encounters SET current_combatant = ?, round = ? WHERE id = ?",
                (current, rnd, eid),
            )

    def _mutate_sheet(self, cid: str, character_id: int, change) -> None:
        """Applies `change(sheet)` to a stored sheet (version + 1), re-indexing only if its
        summary changed; retried if someone else saved in between."""
        for _attempt in range(3):
            with self.storage.campaign(cid) as db:
                character = self._character_row(db, character_id)
                sheet, version = self._load_sheet(db, character_id)
            updated = Sheet.model_validate(change(sheet.model_copy(deep=True)).model_dump())
            entry = self._sheet_entry_parts(cid, character, updated, derive(updated))
            with self.storage.campaign(cid, immediate=True) as db:
                character = self._character_row(db, character_id)
                _, current = self._load_sheet(db, character_id)
                if current != version:
                    continue
                db.execute(
                    "INSERT INTO sheets (character_id, data, version, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(character_id) DO UPDATE SET "
                    "data = excluded.data, version = excluded.version, "
                    "updated_at = excluded.updated_at",
                    (character_id, updated.model_dump_json(), version + 1, _iso(self.clock())),
                )
                self._write_sheet_entry(db, character, *entry)
                return
        raise Conflict("The sheet kept changing; try again.")

    def _combatant_for_change(self, user: User, cid: str, eid: int, combatant: int):
        camp = self._member(user, cid)
        with self.storage.campaign(cid) as db:
            self._encounter(db, eid)
            row = self._combatant(db, eid, combatant)
            character = self._character_row(db, row["character_id"])
        if camp.role != "dm" and row["hidden"]:
            raise NotFound("Combatant not found.")  # F7: hidden ones don't exist for players
        if camp.role != "dm" and (character["owner_id"] != user.id or character["is_npc"]):
            raise Forbidden("Only the DM or the character's player can change this.")
        return row

    def change_hp(self, user: User, cid: str, eid: int, combatant: int, delta: Any) -> None:
        """C4: damage takes temporary HP first (2024); healing stops at max; never below 0."""
        if isinstance(delta, bool) or not isinstance(delta, int) or abs(delta) > 2000:
            raise Invalid("HP change must be a whole number up to 2000.")
        row = self._combatant_for_change(user, cid, eid, combatant)

        def apply(sheet: Sheet) -> Sheet:
            hp = sheet.hp
            if delta < 0:
                damage = -delta
                absorbed = min(hp.temp, damage)
                hp.temp -= absorbed
                hp.current = max(0, hp.current - (damage - absorbed))
            else:
                if hp.current == 0 and delta > 0:  # 2024: regaining HP ends death saves
                    sheet.death_saves.successes = sheet.death_saves.failures = 0
                hp.current = min(hp.max, hp.current + delta)
            return sheet

        self._mutate_sheet(cid, row["character_id"], apply)

    def set_conditions(self, user: User, cid: str, eid: int, combatant: int, conditions) -> None:
        row = self._combatant_for_change(user, cid, eid, combatant)
        if not isinstance(conditions, list) or not all(isinstance(c, str) for c in conditions):
            raise Invalid("conditions must be a list.")

        def apply(sheet: Sheet) -> Sheet:
            sheet.conditions = conditions
            return sheet

        try:
            self._mutate_sheet(cid, row["character_id"], apply)
        except ValidationError as e:
            raise Invalid("Unknown condition.") from e

    def end_encounter(self, user: User, cid: str, eid: int, remove_monsters: bool = False) -> None:
        """C1: ends it; optionally deletes the monsters this encounter added (with their boards)."""
        self._dm(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            self._encounter(db, eid)
            db.execute("UPDATE encounters SET active = 0 WHERE id = ?", (eid,))
            db.execute("DELETE FROM combatants WHERE encounter_id = ?", (eid,))
            if remove_monsters:
                ids = [
                    r["id"]
                    for r in db.execute(
                        "SELECT id FROM characters WHERE from_encounter = ? AND is_npc = 1", (eid,)
                    )
                ]
                for chid in ids:
                    db.execute("DELETE FROM entries WHERE character_id = ?", (chid,))
                    db.execute("DELETE FROM characters WHERE id = ?", (chid,))

    def encounter(self, user: User, cid: str) -> dict[str, Any] | None:
        """C5: the active encounter as this viewer may see it."""
        camp = self._member(user, cid)
        dm = camp.role == "dm"
        with self.storage.campaign(cid) as db:
            enc = self._active_encounter(db)
            if enc is None:
                return None
            rows = db.execute(
                "SELECT c.*, ch.name, ch.is_npc, ch.owner_id FROM combatants c "
                "JOIN characters ch ON ch.id = c.character_id WHERE c.encounter_id = ?",
                (enc["id"],),
            ).fetchall()
            sheets = {r["character_id"]: self._load_sheet(db, r["character_id"])[0] for r in rows}
        ordered = self._order(rows)
        ids = [r["id"] for r in ordered]
        current = enc["current_combatant"] if enc["current_combatant"] in ids else None
        out = []
        for r in ordered:
            if r["hidden"] and not dm:
                continue
            sheet = sheets[r["character_id"]]
            hp = sheet.hp
            item: dict[str, Any] = {
                "id": r["id"],
                "name": r["name"],
                "initiative": r["initiative"],
                "is_npc": bool(r["is_npc"]),
                "conditions": sheet.conditions,
                "health": _health(hp.current, hp.max),
                "can_edit": dm or (r["owner_id"] == user.id and not r["is_npc"]),
            }
            if dm or not r["is_npc"]:
                item |= {
                    "character_id": r["character_id"],
                    "hp": hp.model_dump(),
                    "ac": sheet.armor_class,
                }
            if dm:
                item |= {"hidden": bool(r["hidden"]), "init_bonus": r["init_bonus"]}
            out.append(item)
        view = {
            "id": enc["id"],
            "name": enc["name"],
            "round": enc["round"],
            "current_id": current if (dm or current in {c["id"] for c in out}) else None,
            "combatants": out,
        }
        if dm:  # players get no position in the full order (review F6: hidden combatants)
            view["turn"] = ids.index(current) if current in ids else 0
        return view

    def rolls(self, user: User, cid: str, after: int = 0) -> list[dict[str, Any]]:
        """R3: newer rolls this viewer may see (public, their own, and all for the DM)."""
        camp = self._member(user, cid)
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise Invalid("after must be a whole number.")
        visible = "(to_dm = 0 OR user_id = ? OR ?)"
        args = (user.id, int(camp.role == "dm"))
        with self.storage.campaign(cid) as db:
            npcs = (
                set()
                if camp.role == "dm"
                else {r["id"] for r in db.execute("SELECT id FROM characters WHERE is_npc = 1")}
            )
            if after == 0:  # the first page: the newest rolls (review F2), oldest first
                rows = db.execute(
                    f"SELECT * FROM rolls WHERE {visible} ORDER BY id DESC LIMIT 100", args
                ).fetchall()[::-1]
            else:
                rows = db.execute(
                    f"SELECT * FROM rolls WHERE id > ? AND {visible} ORDER BY id LIMIT 100",
                    (after, *args),
                ).fetchall()
        out = [_roll_from_row(r) for r in rows]
        for r in out:  # F8: a public NPC roll shows its words, never which character it is
            if r["character_id"] in npcs:
                r["character_id"] = None
        return out

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
        if self._gone(user):  # R1: the account was deleted while this was being embedded
            with self.storage.campaign(cid, immediate=True) as db:
                db.execute("PRAGMA secure_delete = ON")
                db.execute("DELETE FROM entries WHERE id = ?", (row["id"],))
            raise NotFound("Campaign not found.")
        return store.entry_from_row(row)

    def _check_model(self, cid: str) -> None:
        """T2: a campaign is searched and written only with the model that embedded it."""
        with self.storage.campaign(cid, immediate=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
            current = embedder_name(self.embedder)
            if row is None:  # a campaign from before M8d: adopt the current model
                if not self.storage.read_only:  # E3: a read-only hub only compares
                    db.execute(
                        "INSERT INTO meta (key, value) VALUES ('embed_model', ?)", (current,)
                    )
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
        if character_id is not None:
            character_id = _id(character_id, "Character")  # P1: no SQLite overflow (404)
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
        if row["kind"] == "sheet":  # C2: generated from the character sheet
            raise Forbidden("This entry is generated from the character sheet; edit the sheet.")
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
            saved = store.get_entry(db, row["id"])
        if saved["visibility"] == "private" and self._gone(user):  # R-A, as in create_entry
            with self.storage.campaign(cid, immediate=True) as db:
                db.execute("PRAGMA secure_delete = ON")
                db.execute("DELETE FROM entries WHERE id = ?", (row["id"],))
            raise NotFound("Entry not found.")
        return store.entry_from_row(saved)

    def delete_entry(self, user: User, cid: str, eid: int) -> None:
        camp = self._member(user, cid)
        with self.storage.campaign(cid, immediate=True) as db:
            row = self._readable(db, user, camp, eid)
            if row["kind"] == "sheet":  # C2
                raise Forbidden("This entry is generated from the character sheet.")
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
        if self._gone(user):  # R1, as for entries
            with self.storage.campaign(cid, immediate=True) as db:
                db.execute("DELETE FROM threads WHERE id = ?", (row["id"],))
            raise NotFound("Campaign not found.")
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


def _health(current: int, maximum: int) -> str:
    """C5: what players see of an NPC's HP."""
    if current <= 0:
        return "down"
    if maximum <= 0 or current >= maximum:
        return "unharmed"
    return "bloodied" if current * 2 <= maximum else "injured"


def _roll_from_row(row) -> dict[str, Any]:
    detail = json.loads(row["detail"])
    return {
        "id": row["id"],
        "label": row["label"],
        "author": row["author_name"],
        "character_id": row["character_id"],
        "expression": row["expression"],
        "terms": detail.get("terms", []),
        "bonus": detail.get("bonus", 0),
        "total": row["total"],
        "natural": detail.get("natural"),
        "outcome": detail.get("outcome"),
        "mode": detail.get("mode", "normal"),
        "to_dm": bool(row["to_dm"]),
        "created_at": row["created_at"],
    }


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
