"""Dashboard accounts: passwords, sessions, cookies, CSRF, roles, invites, reset
(issues #38, #40, #41, #43, ADR 0028).

Queries live here rather than in the Journal writer: the dashboard is the only
reader and writer of `web.accounts` / `web.sessions` / `web.account_tokens`.
The connection is the Journal's, because the tables sit in the same postgres
(ADR 0015).
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
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

import journal

# Stock PasswordHasher defaults are RFC 9106's second recommended option
# (argon2id, t=3, 64 MiB, p=4). Do not hand-tune; see the research note.
_HASHER = PasswordHasher()
_DUMMY_HASH = None

IDLE = "30 minutes"
ABSOLUTE = "12 hours"
INVITE_TTL = "72 hours"
RESET_TTL = "1 hour"
TOKEN_BYTES = 32
ROLES = ("admin", "reader")

COOKIE_SECURE_NAME = "__Host-session"
COOKIE_INSECURE_NAME = "session"
SIGN_IN_ERROR = "That email or password is wrong."


class CSRFDenied(Exception):
    """A state-changing request arrived without a matching synchronizer token."""


class NotAuthorised(Exception):
    """The session's role cannot use this control."""


def refuse() -> HTMLResponse:
    """The one refusal the dashboard returns for a control the caller cannot use."""
    return HTMLResponse("not authorised", status_code=403)


class AccountExists(Exception):
    """The seeding command was pointed at an email that is already an account."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(email)


class InvalidRole(Exception):
    """A role other than admin or reader was offered."""

    def __init__(self, role: str):
        self.role = role
        super().__init__(role)


class InviteInvalid(Exception):
    """The invite token is missing, used, or past its expiry."""


class ResetInvalid(Exception):
    """The reset token is missing, used, or past its expiry."""


class PasswordInvalid(Exception):
    """The password change failed validation."""


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


class RequireRole:
    """FastAPI dependency: the session must carry this role.

    Callable-instance, attached router-wide so a control added to that
    router is gated without a per-route reminder (issue #40).
    """

    @property
    def __globals__(self):
        return self.__call__.__globals__

    def __init__(self, role: str):
        self.role = role

    def __call__(self, request: Request) -> Account:
        account = getattr(request.state, "account", None)
        if account is None or account.role != self.role:
            raise NotAuthorised()
        return account


require_admin = RequireRole("admin")


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
    """Argon2's documented verify-then-rehash: verify, then rehash if parameters moved."""
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


def sign_in_location(request: Request, next_url: str = "/") -> str:
    root = request.scope.get("root_path", "") or ""
    return f"{root}/sign-in?{urlencode({'next': safe_next(next_url)}, safe='/')}"


def is_public(path: str) -> bool:
    return (
        path == "/healthz"
        or path == "/favicon.ico"
        or path == "/sign-in"
        or path == "/forgot"
        or path.startswith("/static/")
        or path.startswith("/invite/")
        or path.startswith("/reset/")
    )


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


def _dummy_verify(password: str) -> None:
    """Spend the same argon2 cost as a real verify, discarding the result."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _HASHER.hash("not-a-real-user-dummy-hash")
    try:
        _HASHER.verify(_DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


def authenticate(email: str, password: str) -> Optional[Account]:
    """Verify credentials. Same refusal for unknown email and wrong password."""
    email = email.strip().lower()
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT id, email, role, password_hash, deactivated_at"
                "  FROM web.accounts WHERE email = %s",
                (email,),
            ).fetchone()
    except psycopg.Error:
        return None
    if row is None:
        _dummy_verify(password)
        return None
    account_id, stored_email, role, hashed, deactivated_at = row
    if hashed is None:
        _dummy_verify(password)
        return None
    if deactivated_at is not None:
        _dummy_verify(password)
        return None
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


def _tx() -> psycopg.Connection:
    """A Journal connection that commits as one transaction.

    `journal.connect` is autocommit so a NOTIFY fires per append. Invite
    consume, deactivation, and invite issuance each need several writes to
    land together or not at all.
    """
    return psycopg.connect(journal.dsn())


def _normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise InvalidRole(role)
    return role


def create_invite(email: str, role: str) -> tuple[int, str]:
    """Insert an invited account (or rotate its unused invite) and return the raw token.

    The raw token is returned so the caller can mail it; it is never stored.
    An already-activated email is AccountExists. A never-activated email is
    re-invited: one live token per purpose.
    """
    email = email.strip().lower()
    role = _normalize_role(role)
    raw = mint_token()
    with _tx() as conn:
        existing = conn.execute(
            "SELECT id, password_hash FROM web.accounts WHERE email = %s",
            (email,),
        ).fetchone()
        if existing is None:
            account_id = conn.execute(
                "INSERT INTO web.accounts (email, password_hash, role)"
                " VALUES (%s, NULL, %s) RETURNING id",
                (email, role),
            ).fetchone()[0]
        else:
            account_id, hashed = existing
            if hashed is not None:
                raise AccountExists(email)
            conn.execute(
                "UPDATE web.accounts SET role = %s WHERE id = %s",
                (role, account_id),
            )
        conn.execute(
            "UPDATE web.account_tokens SET used_at = now()"
            " WHERE account_id = %s AND purpose = 'invite' AND used_at IS NULL",
            (account_id,),
        )
        conn.execute(
            "INSERT INTO web.account_tokens"
            " (token_hash, account_id, purpose, expires_at)"
            " VALUES (%s, %s, 'invite', now() + %s::interval)",
            (hash_token(raw), account_id, INVITE_TTL),
        )
    return account_id, raw


def invite_is_live(token: str) -> bool:
    digest = hash_token(token)
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM web.account_tokens"
                " WHERE token_hash = %s AND purpose = 'invite'"
                "   AND used_at IS NULL AND expires_at > now()",
                (digest,),
            ).fetchone()
    except psycopg.Error:
        return False
    return row is not None


def consume_invite(token: str, password: str) -> int:
    """Set the password and stamp used_at in one transaction. Raises InviteInvalid."""
    if not password:
        raise InviteInvalid()
    digest = hash_token(token)
    hashed = hash_password(password)
    with _tx() as conn:
        row = conn.execute(
            "UPDATE web.account_tokens SET used_at = now()"
            " WHERE token_hash = %s AND purpose = 'invite'"
            "   AND used_at IS NULL AND expires_at > now()"
            " RETURNING account_id",
            (digest,),
        ).fetchone()
        if row is None:
            raise InviteInvalid()
        account_id = row[0]
        conn.execute(
            "UPDATE web.accounts SET password_hash = %s WHERE id = %s",
            (hashed, account_id),
        )
    return account_id


def create_reset(email: str) -> Optional[str]:
    """Mint a reset token for an activated, live account. None otherwise.

    The raw token is returned so the caller can mail it; it is never stored.
    Unknown, never-activated, and deactivated addresses return None, so the
    public form cannot tell them apart.
    """
    email = email.strip().lower()
    if not email:
        return None
    raw = mint_token()
    with _tx() as conn:
        existing = conn.execute(
            "SELECT id, password_hash, deactivated_at"
            "  FROM web.accounts WHERE email = %s",
            (email,),
        ).fetchone()
        if existing is None:
            return None
        account_id, hashed, deactivated_at = existing
        if hashed is None or deactivated_at is not None:
            return None
        conn.execute(
            "UPDATE web.account_tokens SET used_at = now()"
            " WHERE account_id = %s AND purpose = 'reset' AND used_at IS NULL",
            (account_id,),
        )
        conn.execute(
            "INSERT INTO web.account_tokens"
            " (token_hash, account_id, purpose, expires_at)"
            " VALUES (%s, %s, 'reset', now() + %s::interval)",
            (hash_token(raw), account_id, RESET_TTL),
        )
    return raw


def reset_is_live(token: str) -> bool:
    digest = hash_token(token)
    try:
        with journal.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM web.account_tokens"
                " WHERE token_hash = %s AND purpose = 'reset'"
                "   AND used_at IS NULL AND expires_at > now()",
                (digest,),
            ).fetchone()
    except psycopg.Error:
        return False
    return row is not None


def consume_reset(token: str, password: str) -> int:
    """Set the password, stamp used_at, and drop sessions in one transaction."""
    if not password:
        raise ResetInvalid()
    digest = hash_token(token)
    hashed = hash_password(password)
    with _tx() as conn:
        row = conn.execute(
            "UPDATE web.account_tokens SET used_at = now()"
            " WHERE token_hash = %s AND purpose = 'reset'"
            "   AND used_at IS NULL AND expires_at > now()"
            " RETURNING account_id",
            (digest,),
        ).fetchone()
        if row is None:
            raise ResetInvalid()
        account_id = row[0]
        conn.execute(
            "UPDATE web.accounts SET password_hash = %s WHERE id = %s",
            (hashed, account_id),
        )
        conn.execute(
            "DELETE FROM web.sessions"
            " WHERE account_id = %s",
            (account_id,),
        )
    return account_id


def change_password(
    account_id: int,
    current_password: str,
    new_password: str,
    current_token_hash: Optional[str] = None,
) -> None:
    """Change an account's password.

    Verifies current_password against the stored hash.
    Refuses empty new_password.
    Updates web.accounts with the new argon2id hash.
    Deletes other sessions for this account.
    """
    if not new_password:
        raise PasswordInvalid("A new password is required.")
    with _tx() as conn:
        existing = conn.execute(
            "SELECT password_hash FROM web.accounts WHERE id = %s",
            (account_id,),
        ).fetchone()
        if existing is None or existing[0] is None:
            raise PasswordInvalid("Account not found or has no password.")
        stored_hash = existing[0]
        ok, _ = verify_password(current_password, stored_hash)
        if not ok:
            raise PasswordInvalid("Current password is incorrect.")
        new_hash = hash_password(new_password)
        conn.execute(
            "UPDATE web.accounts SET password_hash = %s WHERE id = %s",
            (new_hash, account_id),
        )
        if current_token_hash:
            conn.execute(
                "DELETE FROM web.sessions"
                " WHERE account_id = %s AND token_hash != %s",
                (account_id, current_token_hash),
            )
        else:
            conn.execute(
                "DELETE FROM web.sessions"
                " WHERE account_id = %s",
                (account_id,),
            )


def list_accounts() -> list[dict]:
    with journal.connect() as conn:
        rows = conn.execute(
            "SELECT id, email, role,"
            "       password_hash IS NOT NULL,"
            "       deactivated_at IS NOT NULL"
            "  FROM web.accounts ORDER BY id"
        ).fetchall()
    return [
        {
            "id": row[0],
            "email": row[1],
            "role": row[2],
            "activated": row[3],
            "deactivated": row[4],
        }
        for row in rows
    ]


def set_role(account_id: int, role: str) -> None:
    role = _normalize_role(role)
    with journal.connect() as conn:
        conn.execute(
            "UPDATE web.accounts SET role = %s WHERE id = %s",
            (role, account_id),
        )


def load_section_order(account_id: int) -> list[str] | None:
    """The Dashboard Account's headed-section order on `/`, or None for the default."""
    with journal.connect() as conn:
        row = conn.execute(
            "SELECT section_order FROM web.accounts WHERE id = %s",
            (account_id,),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return list(row[0])


def save_section_order(account_id: int, keys: list[str]) -> None:
    with journal.connect() as conn:
        conn.execute(
            "UPDATE web.accounts SET section_order = %s WHERE id = %s",
            (keys, account_id),
        )


def deactivate(account_id: int) -> None:
    """Stamp deactivated_at, drop sessions, and consume unused tokens in one transaction."""
    with _tx() as conn:
        conn.execute(
            "UPDATE web.accounts SET deactivated_at = now() WHERE id = %s",
            (account_id,),
        )
        conn.execute(
            "DELETE FROM web.sessions WHERE account_id = %s",
            (account_id,),
        )
        conn.execute(
            "UPDATE web.account_tokens SET used_at = now()"
            " WHERE account_id = %s AND used_at IS NULL",
            (account_id,),
        )


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
            # A control POST must not 303 into sign-in with the control as
            # `next`: after a successful sign-in that would be a redirect
            # into a success. Refuse instead. Pages still 303.
            if scope.get("method", "GET") not in ("GET", "HEAD"):
                response = refuse()
                await response(scope, receive, send)
                return
            next_url = path
            query = scope.get("query_string") or b""
            if query:
                next_url = f"{next_url}?{query.decode()}"
            response = RedirectResponse(
                sign_in_location(request, next_url), status_code=303
            )
            await response(scope, receive, send)
            return
        await run_in_threadpool(touch_session, session.token_hash)
        await self.app(scope, receive, send)
