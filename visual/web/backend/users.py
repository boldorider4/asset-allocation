# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user accounts store (users.json) as the web backend sees it.

The accounts live in their own repository (``users.json``), keyed by
user name: the web login asks *this* store who may log in, not the
assets store. Which backend holds the accounts is the app's choice
(JSON file today, Postgres when it lands) — see ``storage.wiring``.
"""

from __future__ import annotations

from pathlib import Path

from storage.repositories import UsersRepository
from storage.wiring import BACKEND_JSON, DEFAULT_POSTGRES_DSN, users_backend

__all__ = ["users_user_exists", "users_repo"]


def users_repo(
    users_path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> UsersRepository:
    """Repository over the user accounts, scoped to no single user."""
    return UsersRepository(users_backend(users_path, backend, postgres_dsn))


def users_user_exists(
    users_path: str | Path,
    user: str,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> bool:
    """True when the users store holds an account for ``user``."""
    repo = users_repo(users_path, backend, postgres_dsn)
    repo.open()
    return repo.exists(user)
