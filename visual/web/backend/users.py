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

__all__ = ["users_credentials_ok", "users_repo", "reset_user_password"]


def users_repo(
    users_path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> UsersRepository:
    """Repository over the user accounts, scoped to no single user."""
    return UsersRepository(users_backend(users_path, backend, postgres_dsn))


def users_credentials_ok(
    users_path: str | Path,
    user: str,
    password: str = "",
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> bool:
    """True when the account exists and the password matches.

    An unknown account and a wrong password are the same answer, so
    the login cannot be used to probe for user names. Empty passwords
    are allowed.
    """
    repo = users_repo(users_path, backend, postgres_dsn)
    repo.open()
    return repo.check_credentials(user, password)


def reset_user_password(
    users_path: str | Path,
    user: str,
    old_password: str,
    new_password: str,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> bool:
    """Set a new password after checking the old one.

    Returns False when the old password is wrong (or the account does
    not exist) — the two are the same answer, so the reset cannot be
    used to probe for user names. An empty new password is allowed.
    """
    repo = users_repo(users_path, backend, postgres_dsn)
    repo.open()
    if not repo.check_credentials(user, old_password):
        return False
    repo.set_password(user, new_password)
    repo.persist()
    return True
