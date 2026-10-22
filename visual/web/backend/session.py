# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser sessions for the visualizer server.

A session is identified by a cookie (``asalloc_sid``) and persisted in
the server workspace, so it survives restarts but not idling: a session
expires 30 minutes after it was created.

The store is a normal backend-agnostic one: the JSON backend keeps
the session in ``<workspace>/.sessions.json``, the SQLite backend
keeps it in the ``sessions`` table of the database file. Both hold
the same rows (``user``, ``incognito``, ``expires_at``) — the backend
is chosen by the application (see :mod:`storage.wiring`).

The session carries the *user* whose holdings and charts are served
(there is no per-request user parameter any more) and the incognito
state the user last chose.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

from storage.repositories import SessionsRepository
from storage.wiring import (
    BACKEND_JSON,
    DEFAULT_POSTGRES_DSN,
    sessions_backend,
)

logger = logging.getLogger(__name__)

#: Cookie carrying the session id.
COOKIE_NAME = "asalloc_sid"
#: How long a session stays valid: 30 minutes.
SESSION_TTL_SECONDS = 30 * 60
#: The cookie dies with the session it points at.
COOKIE_MAX_AGE = SESSION_TTL_SECONDS
#: Store file inside the served workspace (JSON backend).
STORE_FILENAME = ".sessions.json"


@dataclass(frozen=True)
class Session:
    """One browser session: the logged-in user and the incognito state."""

    user: str
    incognito: bool = False
    expires_at: float = 0.0


def store_path(workspace: str | Path) -> Path:
    """Path of the session store inside the server workspace."""
    return Path(workspace) / STORE_FILENAME


def new_session_id() -> str:
    """A fresh, unguessable session id."""
    return secrets.token_urlsafe(32)


def cookie_header(session_id: str) -> str:
    """``Set-Cookie`` value for the given session id."""
    return (
        f"{COOKIE_NAME}={session_id}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
        "SameSite=Lax; HttpOnly"
    )


def clear_cookie_header() -> str:
    """``Set-Cookie`` value that removes the session cookie."""
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly"


def session_id_from_cookie(header: str | None) -> str | None:
    """The session id carried by a ``Cookie`` header, if any."""
    if not header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(header)
    except Exception:  # malformed cookie header: treat as no session
        return None
    morsel = cookie.get(COOKIE_NAME)
    if morsel is None:
        return None
    value = (morsel.value or "").strip()
    return value or None


def _repo(
    workspace: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> SessionsRepository:
    """The session repository for this workspace.

    The JSON backend keeps the sessions in ``<workspace>/.sessions.json``;
    the SQLite backend keeps them in the ``sessions`` table of the
    database file.
    """
    return SessionsRepository(
        sessions_backend(
            store_path(workspace), backend, postgres_dsn, db_file
        )
    )


def read_session(
    workspace: str | Path,
    cookie_header_value: str | None,
    *,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> Session | None:
    """The session for the given cookie, or None when there is none.

    An expired session counts as no session: the record is dropped and
    the caller sends the browser to the login page. A session row that
    cannot be read at all (a damaged row) counts as no session too: it
    is dropped, so the browser gets a fresh login instead of a broken
    request.
    """
    session_id = session_id_from_cookie(cookie_header_value)
    if not session_id:
        return None
    repo = _repo(workspace, backend, postgres_dsn, db_file)
    repo.open()
    try:
        record = repo.get(session_id)
    except Exception as exc:
        # A damaged row must never take a request down: the session is
        # gone, the browser logs in again.
        logger.warning("session %s unreadable (%s)", session_id[:8], exc)
        record = None
    if record is None:
        # Either there is no session or it expired: drop the stale row
        # so the store does not keep it around.
        if repo.drop(session_id):
            repo.persist()
            logger.info("dropped the session %s (expired or unknown)", session_id[:8])
        return None
    return Session(
        user=record.user,
        incognito=record.incognito,
        expires_at=record.expires_at,
    )


def login(
    workspace: str | Path,
    session_id: str,
    user: str,
    *,
    incognito: bool = False,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> Session:
    """Persist a session for ``session_id`` and return it.

    The session expires ``SESSION_TTL_SECONDS`` after this call.
    """
    repo = _repo(workspace, backend, postgres_dsn, db_file)
    repo.open()
    record = repo.start(
        session_id,
        user,
        incognito=incognito,
        ttl_seconds=SESSION_TTL_SECONDS,
    )
    repo.persist()
    return Session(
        user=record.user,
        incognito=record.incognito,
        expires_at=record.expires_at,
    )


def logout(
    workspace: str | Path,
    session_id: str,
    *,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> None:
    """Drop the session for ``session_id`` (no-op when unknown)."""
    repo = _repo(workspace, backend, postgres_dsn, db_file)
    repo.open()
    if repo.drop(session_id):
        repo.persist()


def clear_sessions(
    workspace: str | Path,
    *,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
    expired_only: bool = False,
) -> int:
    """Drop sessions; returns how many were removed.

    Without ``expired_only`` every session goes (everybody is logged
    out); with it only the sessions past their expiry are dropped.
    Works on every backend: the JSON store loses its
    ``<workspace>/.sessions.json`` rows, the SQLite backend its
    ``sessions`` table rows.
    """
    repo = _repo(workspace, backend, postgres_dsn, db_file)
    repo.open()
    if expired_only:
        return repo.purge_expired()
    keys = list(repo.snapshot().keys())
    for key in keys:
        repo.drop(key)
    repo.persist()
    return len(keys)
