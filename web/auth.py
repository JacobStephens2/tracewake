"""Window accounts: passwords, sessions, cookies, CSRF (issue #38, ADR 0027).

Queries live here rather than in the Journal writer: the window is the only
reader and writer of `web.accounts` / `web.sessions`. The connection is
the Journal's, because the tables sit in the same postgres (ADR 0015).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

import journal

# Stock PasswordHasher defaults are RFC 9106's second recommended option
# (argon2id, t=3, 64 MiB, p=4). Do not hand-tune; see the research note.
_HASHER = PasswordHasher()
_DUMMY_HASH = None

IDLE = "30 minutes"
ABSOLUTE = "12 hours"
TOKEN_BYTES = 32

COOKIE_SECURE_NAME = "__Host-session"
COOKIE_INSECURE_NAME = "session"
LOGIN_ERROR = "That email or password is wrong."


class CSRFDenied(Exception):
    """A state-changing request arrived without a matching synchronizer token."""


class AccountExists(Exception):
    """The seeding command was pointed at an email that is already an account."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(email)


@dataclass(frozen=True)
class Account:
    id: int
    email: str
    role: str


@dataclass(frozen=True)
class Session:
    token_hash: str
    csrf_token: str
    account: Optional[Account]


def cookie_secure() -> bool:
    """Secure (and therefore `__Host-`) unless the Attended Preview toggle says no."""
    raw = os.environ.get("WINDOW_COOKIE_SECURE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def cookie_name() -> str:
    # `__Host-` requires Secure; a preview on plain HTTP cannot set one.
    return COOKIE_SECURE_NAME if cookie_secure() else COOKIE_INSECURE_NAME


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, hashed: str) -> tuple[bool, Optional[str]]:
    """Argon2's documented login: verify, then rehash if parameters moved."""
    try:
        _HASHER.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    new_hash = None
    if _HASHER.check_needs_rehash(hashed):
        new_hash = _HASHER.hash(password)
    return True, new_hash


def mint_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def session_token_from_request(request: Request) -> Optional[str]:
    return request.cookies.get(cookie_name())


def safe_next(value: Optional[str]) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "://" in value:
        return "/"
    return value


def login_location(request: Request, next_url: str = "/") -> str:
    root = request.scope.get("root_path", "") or ""
    return f"{root}/login?{urlencode({'next': safe_next(next_url)}, safe='/')}"


def is_public(path: str) -> bool:
    return path == "/healthz" or path == "/login" or path.startswith("/static/")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=cookie_name(),
        value=token,
        max_age=12 * 60 * 60,
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=cookie_name(),
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def create_admin(email: str, password: str) -> int:
    """Insert the first admin. Raises AccountExists if that email is taken."""
    email = email.strip().lower()
    hashed = hash_password(password)
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "INSERT INTO web.accounts (email, password_hash, role)"
                " VALUES (%s, %s, 'admin') RETURNING id",
                (email, hashed),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise AccountExists(email) from exc
    return row[0]


def _insert_session(conn: psycopg.Connection, account_id: Optional[int]) -> str:
    raw = mint_token()
    conn.execute(
        "INSERT INTO web.sessions (token_hash, account_id, csrf_token)"
        " VALUES (%s, %s, %s)",
        (hash_token(raw), account_id, mint_token()),
    )
    return raw


def new_anonymous_session() -> str:
    with journal.connect() as conn:
        _sweep(conn)
        return _insert_session(conn, None)


def create_session(account_id: int, *, replacing: Optional[str] = None) -> str:
    """Mint a session for `account_id`. A presented token's row is deleted."""
    with journal.connect() as conn:
        _sweep(conn)
        if replacing:
            conn.execute(
                "DELETE FROM web.sessions WHERE token_hash = %s",
                (hash_token(replacing),),
            )
        return _insert_session(conn, account_id)


def destroy_session(token_hash: str) -> None:
    with journal.connect() as conn:
        conn.execute(
            "DELETE FROM web.sessions WHERE token_hash = %s",
            (token_hash,),
        )


def _sweep(conn: psycopg.Connection) -> None:
    conn.execute(
        "DELETE FROM web.sessions"
        " WHERE created_at <= now() - %s::interval"
        "    OR last_seen_at <= now() - %s::interval",
        (ABSOLUTE, IDLE),
    )


def load_session(token: str) -> Optional[Session]:
    digest = hash_token(token)
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT s.token_hash, s.csrf_token, s.account_id,"
                "       a.email, a.role"
                "  FROM web.sessions s"
                "  LEFT JOIN web.accounts a ON a.id = s.account_id"
                " WHERE s.token_hash = %s"
                "   AND s.created_at > now() - %s::interval"
                "   AND s.last_seen_at > now() - %s::interval",
                (digest, ABSOLUTE, IDLE),
            ).fetchone()
    except psycopg.Error:
        return None
    if row is None:
        return None
    token_hash, csrf_token, account_id, email, role = row
    account = None
    if account_id is not None and email is not None:
        account = Account(id=account_id, email=email, role=role)
    return Session(token_hash=token_hash, csrf_token=csrf_token, account=account)


def touch_session(token_hash: str) -> None:
    try:
        with journal.connect() as conn:
            _sweep(conn)
            conn.execute(
                "UPDATE web.sessions SET last_seen_at = now()"
                " WHERE token_hash = %s",
                (token_hash,),
            )
    except psycopg.Error:
        return


def authenticate(email: str, password: str) -> Optional[Account]:
    """Verify credentials. Same refusal for unknown email and wrong password."""
    email = email.strip().lower()
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT id, email, role, password_hash"
                "  FROM web.accounts WHERE email = %s",
                (email,),
            ).fetchone()
    except psycopg.Error:
        return None
    if row is None:
        # Dummy verify so an unknown email costs the same as a wrong password.
        global _DUMMY_HASH
        if _DUMMY_HASH is None:
            _DUMMY_HASH = _HASHER.hash("not-a-real-user-dummy-hash")
        try:
            _HASHER.verify(_DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return None
    account_id, stored_email, role, hashed = row
    ok, new_hash = verify_password(password, hashed)
    if not ok:
        return None
    if new_hash is not None:
        with journal.connect() as conn:
            conn.execute(
                "UPDATE web.accounts SET password_hash = %s WHERE id = %s",
                (new_hash, account_id),
            )
    return Account(id=account_id, email=stored_email, role=role)


def csrf_ok(request: Request, offered: Optional[str]) -> bool:
    session = getattr(request.state, "session", None)
    if session is None or not offered:
        return False
    return secrets.compare_digest(offered, session.csrf_token)


class RequireSignIn:
    """ASGI middleware: every page except health, sign-in, and static needs a session.

    Not BaseHTTPMiddleware: that wrapper buffers the body and would hold the
    Journal's SSE stream until it ended.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        token = session_token_from_request(request)
        session = None
        if token:
            session = await run_in_threadpool(load_session, token)
        request.state.session = session
        request.state.account = None if session is None else session.account
        path = scope.get("path", "")
        if is_public(path):
            await self.app(scope, receive, send)
            return
        if session is None or session.account is None:
            next_url = path
            query = scope.get("query_string") or b""
            if query:
                next_url = f"{next_url}?{query.decode()}"
            response = RedirectResponse(
                login_location(request, next_url), status_code=303
            )
            await response(scope, receive, send)
            return
        await run_in_threadpool(touch_session, session.token_hash)
        await self.app(scope, receive, send)
