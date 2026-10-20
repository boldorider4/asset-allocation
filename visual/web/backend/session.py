# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser sessions for the visualizer server.

A session is identified by a cookie (``asalloc_sid``) and persisted in
the server workspace, so it survives restarts and lasts "forever" (the
cookie has a long max-age, the server never expires it):

    <workspace>/.sessions.json  ->  {"<sid>": {"user": ..., "incognito": ...}}

The session carries the *user* whose holdings and charts are served
(there is no per-request user parameter any more) and the incognito
state the user last chose.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Cookie carrying the session id.
COOKIE_NAME = "asalloc_sid"
#: Ten years: the session is meant to last "forever".
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600
#: Store file inside the served workspace.
STORE_FILENAME = ".sessions.json"

_lock = threading.Lock()


@dataclass(frozen=True)
class Session:
    """One browser session: the logged-in user and the incognito state."""

    user: str
    incognito: bool = False


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


def _read_store(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("session store %s unreadable (%s); starting empty", path, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("session store %s must be an object; starting empty", path)
        return {}
    return raw


def _write_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)


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


def read_session(
    workspace: str | Path, cookie_header_value: str | None
) -> Session | None:
    """The session for the given cookie, or None when there is none."""
    session_id = session_id_from_cookie(cookie_header_value)
    if not session_id:
        return None
    path = store_path(workspace)
    with _lock:
        store = _read_store(path)
    entry = store.get(session_id)
    if not isinstance(entry, dict):
        return None
    user = str(entry.get("user") or "").strip()
    if not user:
        return None
    return Session(user=user, incognito=bool(entry.get("incognito")))


def login(
    workspace: str | Path,
    session_id: str,
    user: str,
    *,
    incognito: bool = False,
) -> Session:
    """Persist a session for ``session_id`` and return it."""
    session = Session(user=user, incognito=bool(incognito))
    path = store_path(workspace)
    with _lock:
        store = _read_store(path)
        store[session_id] = {
            "user": session.user,
            "incognito": session.incognito,
        }
        _write_store(path, store)
    return session


def logout(workspace: str | Path, session_id: str) -> None:
    """Drop the session for ``session_id`` (no-op when unknown)."""
    path = store_path(workspace)
    with _lock:
        store = _read_store(path)
        if store.pop(session_id, None) is None:
            return
        _write_store(path, store)
