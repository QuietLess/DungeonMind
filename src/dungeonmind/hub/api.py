"""FastAPI app for the campaign hub. Routes stay thin: every rule lives in service.Hub.

Cross-cutting checks, in order: request size (413), the CSRF header on unsafe methods (403),
then the session (401) as a router-level dependency, so no route can be added unprotected.
Errors are always `{"detail": <plain message>}`; unexpected ones never show their text.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt

from dungeonmind.hub.auth import SESSION_DAYS
from dungeonmind.hub.errors import AuthFailed, HubError
from dungeonmind.hub.models import User
from dungeonmind.hub.service import Hub

SESSION_COOKIE = "dm_session"
CSRF_HEADER = "X-DungeonMind"
MAX_BODY_BYTES = 64 * 1024
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
INTERNAL_ERROR = "Something went wrong. Please try again."
DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
# UI2: the UI needs nothing from other origins; remote images can't load even if a renderer
# bug ever produced one (defence in depth for H1).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
NO_BUILD_PAGE = (
    "<!doctype html><title>DungeonMind</title><h1>DungeonMind</h1>"
    "<p>The web UI hasn't been built yet. Run <code>uv run npm --prefix web install</code> and "
    "<code>uv run npm --prefix web run build</code>, then restart <code>dungeonmind serve</code>."
    "</p><p>The API is available under <code>/api</code>.</p>"
)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(_In):
    username: str
    password: str


class Registration(Credentials):
    invite: str | None = None  # M9d S1: needed when sign-up is invite-only


class InviteCode(_In):
    code: str


class CampaignIn(_In):
    name: str


class CharacterIn(_In):
    name: str
    summary: str = ""


class CharacterPatch(_In):
    name: str | None = None
    summary: str | None = None


class EntryIn(_In):
    board: str
    title: str
    body: str
    kind: str | None = None
    visibility: str | None = None
    character_id: StrictInt | None = None
    session: StrictInt | None = None


class EntryPatch(_In):
    title: str | None = None
    body: str | None = None
    kind: str | None = None
    visibility: str | None = None
    session: StrictInt | None = None


class PasswordChange(_In):
    current_password: str
    new_password: str


class AccountDelete(_In):
    password: str
    confirm_username: str


class ThreadIn(_In):
    title: str = "New chat"


class ThreadPatch(_In):
    title: str


class MessageIn(_In):
    text: str


def _plain(errors: list[dict[str, Any]]) -> str:
    """One readable line from pydantic errors, without echoing the submitted values."""
    if not errors:
        return "Invalid request."
    err = errors[0]
    if err.get("type") == "json_invalid":
        return "The request body isn't valid JSON."
    field = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
    message = err.get("msg", "invalid value")
    return f"{field}: {message}" if field else message


def _servable(path: str) -> bool:
    """W2: never hidden files (any segment starting with '.') or source maps from web/dist."""
    parts = [p for p in path.split("/") if p]
    hidden = any(p.startswith(".") for p in parts)
    return bool(parts) and not hidden and not path.lower().endswith(".map")  # APFS ignores case


PROXIES = ("cloudflare",)


def _from_loopback(request: Request) -> bool:
    try:
        return bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _proxied(request: Request) -> bool:
    """M9d P1: the request came through the configured proxy (cloudflared on this machine)."""
    return getattr(request.app.state, "proxy", None) == "cloudflare" and _from_loopback(request)


def client_of(request: Request) -> str:
    """The client address, for per-client rate limits (F4, F5).

    Behind Cloudflare (P1) it's `CF-Connecting-IP`, trusted only from loopback; IPv6 is
    grouped by /64. Otherwise the connection's own address; the header is never read."""
    peer = request.client.host if request.client else "unknown"
    if not _proxied(request):
        return peer
    try:
        ip = ipaddress.ip_address(request.headers.get("cf-connecting-ip", "").strip())
    except ValueError:
        return peer
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def current_user(request: Request) -> User:
    """The signed-in user, or 401. Router-level on every private route."""
    user = request.app.state.hub.user_for_token(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise AuthFailed("Please sign in.")
    return user


# Module level, so FastAPI can resolve it under `from __future__ import annotations`.
Me = Annotated[User, Depends(current_user)]


def create_app(
    hub: Hub, web_dist: Path | None = DEFAULT_WEB_DIST, proxy: str | None = None
) -> FastAPI:
    if proxy is not None and proxy not in PROXIES:
        raise ValueError(f"proxy must be one of {', '.join(PROXIES)}")
    app = FastAPI(title="DungeonMind", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub
    app.state.proxy = proxy

    def refuse(request: Request) -> JSONResponse | None:
        if request.url.path.startswith("/api") and request.method in UNSAFE:
            length = request.headers.get("content-length")
            if length is None and request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "Send a Content-Length header."}, 411)
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return JSONResponse({"detail": "Request body is too large (64 KB max)."}, 413)
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": f"Missing {CSRF_HEADER} header."}, 403)
        return None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        response = refuse(request) or await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if _proxied(request) and request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"  # review R5
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"  # W4: private notes aren't cached
        return response

    @app.exception_handler(HubError)
    async def hub_error(request: Request, exc: HubError):
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": _plain(exc.errors())}, 422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # Runs outside the http middleware, so it adds the headers itself (W1).
        headers = {**SECURITY_HEADERS, "Cache-Control": "no-store"}
        return JSONResponse({"detail": INTERNAL_ERROR}, 500, headers=headers)

    def set_session(request: Request, response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_DAYS * 24 * 3600,
            path="/",
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https"
            or (_proxied(request) and request.headers.get("x-forwarded-proto") == "https"),
        )

    public = APIRouter(prefix="/api")
    private = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

    @public.get("/health")
    def health():
        # O1: says only whether the hub can read its database; nothing about models or keys.
        if hub.healthy():
            return {"status": "ok"}
        return JSONResponse({"status": "unavailable"}, 503)

    # --- auth --------------------------------------------------------------------------------

    @public.get("/auth/config")
    def auth_config():
        return hub.signup_config()  # S2

    @public.post("/auth/register", status_code=201)
    def register(body: Registration, request: Request, response: Response):
        user = hub.register(
            body.username, body.password, client=client_of(request), invite=body.invite
        )
        set_session(request, response, hub.new_session(user))  # F5: no second scrypt
        return asdict(user)

    @public.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        token = hub.login(body.username, body.password, client=client_of(request))
        set_session(request, response, token)
        user = hub.user_for_token(token)
        return asdict(user) if user else {}

    @private.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        hub.logout(request.cookies.get(SESSION_COOKIE, ""))
        response.delete_cookie(SESSION_COOKIE, path="/")

    @private.get("/me")
    def me(user: Me):
        return asdict(user)

    # --- account (M9b) ----------------------------------------------------------------------

    @private.get("/account")
    def account(user: Me):
        return hub.account(user)

    @private.post("/account/password", status_code=204)
    def change_password(body: PasswordChange, request: Request, user: Me):
        token = request.cookies.get(SESSION_COOKIE, "")
        hub.change_password(
            user, token, body.current_password, body.new_password, client=client_of(request)
        )

    @private.post("/account/sign-out-others")
    def sign_out_others(request: Request, user: Me):
        return {"signed_out": hub.sign_out_others(user, request.cookies.get(SESSION_COOKIE, ""))}

    @private.post("/account/delete", status_code=204)
    def delete_account(body: AccountDelete, request: Request, response: Response, user: Me):
        hub.delete_account(user, body.password, body.confirm_username, client=client_of(request))
        response.delete_cookie(SESSION_COOKIE, path="/")

    # --- campaigns ---------------------------------------------------------------------------

    @private.get("/campaigns")
    def campaigns(user: Me):
        return [asdict(c) for c in hub.campaigns(user)]

    @private.post("/campaigns", status_code=201)
    def create_campaign(body: CampaignIn, user: Me):
        return asdict(hub.create_campaign(user, body.name))

    @private.get("/campaigns/{cid}")
    def campaign(cid: str, user: Me):
        return asdict(hub.campaign(user, cid))

    @private.delete("/campaigns/{cid}", status_code=204)
    def delete_campaign(cid: str, user: Me):
        hub.delete_campaign(user, cid)

    @private.post("/campaigns/{cid}/invites", status_code=201)
    def create_invite(cid: str, user: Me):
        return asdict(hub.create_invite(user, cid))

    # Invite codes travel in bodies, never URLs, so they don't end up in logs (F6).
    @private.post("/campaigns/{cid}/invites/revoke", status_code=204)
    def revoke_invite(cid: str, body: InviteCode, user: Me):
        hub.revoke_invite(user, cid, body.code)

    @private.post("/invites/accept")
    def accept_invite(body: InviteCode, user: Me):
        return asdict(hub.accept_invite(user, body.code))

    @private.get("/campaigns/{cid}/members")
    def members(cid: str, user: Me):
        return [asdict(m) for m in hub.members(user, cid)]

    @private.delete("/campaigns/{cid}/members/{uid}", status_code=204)
    def remove_member(cid: str, uid: str, user: Me):
        hub.remove_member(user, cid, uid)

    # --- characters --------------------------------------------------------------------------

    @private.get("/campaigns/{cid}/characters")
    def characters(cid: str, user: Me):
        return [asdict(c) for c in hub.characters(user, cid)]

    @private.post("/campaigns/{cid}/characters", status_code=201)
    def create_character(cid: str, body: CharacterIn, user: Me):
        return asdict(hub.create_character(user, cid, body.name, body.summary))

    @private.patch("/campaigns/{cid}/characters/{chid}")
    def update_character(cid: str, chid: int, body: CharacterPatch, user: Me):
        return asdict(hub.update_character(user, cid, chid, **body.model_dump(exclude_unset=True)))

    @private.delete("/campaigns/{cid}/characters/{chid}", status_code=204)
    def delete_character(cid: str, chid: int, user: Me):
        hub.delete_character(user, cid, chid)

    # --- entries -----------------------------------------------------------------------------

    @private.get("/campaigns/{cid}/entries")
    def entries(
        cid: str,
        user: Me,
        board: str | None = None,
        character_id: int | None = None,
        session: int | None = None,
        q: str | None = None,
    ):
        found = hub.entries(user, cid, board=board, character_id=character_id, session=session, q=q)
        return [asdict(e) for e in found]

    @private.post("/campaigns/{cid}/entries", status_code=201)
    def create_entry(cid: str, body: EntryIn, user: Me):
        return asdict(hub.create_entry(user, cid, **body.model_dump()))

    @private.get("/campaigns/{cid}/entries/{eid}")
    def entry(cid: str, eid: int, user: Me):
        return asdict(hub.entry(user, cid, eid))

    @private.patch("/campaigns/{cid}/entries/{eid}")
    def update_entry(cid: str, eid: int, body: EntryPatch, user: Me):
        return asdict(hub.update_entry(user, cid, eid, **body.model_dump(exclude_unset=True)))

    @private.delete("/campaigns/{cid}/entries/{eid}", status_code=204)
    def delete_entry(cid: str, eid: int, user: Me):
        hub.delete_entry(user, cid, eid)

    @private.get("/campaigns/{cid}/search")
    def search(cid: str, q: str, user: Me, k: int = 8):
        return [asdict(h) for h in hub.search(user, cid, q, k)]

    # --- chats -------------------------------------------------------------------------------

    @private.get("/campaigns/{cid}/chats")
    def threads(cid: str, user: Me):
        return [asdict(t) for t in hub.threads(user, cid)]

    @private.post("/campaigns/{cid}/chats", status_code=201)
    def create_thread(cid: str, body: ThreadIn, user: Me):
        return asdict(hub.create_thread(user, cid, body.title))

    @private.get("/campaigns/{cid}/chats/{tid}")
    def thread(cid: str, tid: int, user: Me):
        return {
            "thread": asdict(hub.thread(user, cid, tid)),
            "messages": [asdict(m) for m in hub.messages(user, cid, tid)],
        }

    @private.patch("/campaigns/{cid}/chats/{tid}")
    def rename_thread(cid: str, tid: int, body: ThreadPatch, user: Me):
        return asdict(hub.rename_thread(user, cid, tid, body.title))

    @private.delete("/campaigns/{cid}/chats/{tid}", status_code=204)
    def delete_thread(cid: str, tid: int, user: Me):
        hub.delete_thread(user, cid, tid)

    @private.post("/campaigns/{cid}/chats/{tid}/messages")
    def ask(cid: str, tid: int, body: MessageIn, user: Me):
        return asdict(hub.ask(user, cid, tid, body.text))

    app.include_router(public)
    app.include_router(private)

    # --- the web UI (UI1): files from web/dist, index.html for every app route ---------------

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not found."}, 404)
        if web_dist is None or not (web_dist / "index.html").is_file():
            return HTMLResponse(NO_BUILD_PAGE)
        root = web_dist.resolve()
        if _servable(path):
            try:
                candidate = (root / path).resolve()
            except (ValueError, OSError):  # e.g. a NUL byte in the path (W1)
                candidate = root
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
