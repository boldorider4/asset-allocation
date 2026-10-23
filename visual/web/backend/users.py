# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user accounts store as the web backend sees it.

The accounts live in the ``users`` table of the SQLite database, keyed
by user name: the web login asks *this* store who may log in, not the
positions store.
"""

from __future__ import annotations

from pathlib import Path

from storage.repositories import UsersRepository
from storage.wiring import users_backend

__all__ = ["users_credentials_ok", "users_repo", "reset_user_password"]


def users_repo(db_file: str | Path) -> UsersRepository:
    """Repository over the user accounts, scoped to no single user."""
    return UsersRepository(users_backend(db_file))


def users_credentials_ok(
    db_file: str | Path,
    user: str,
    password: str = "",
) -> bool:
    """True when the account exists and the password matches.

    An unknown account and a wrong password are the same answer, so
    the login cannot be used to probe for user names. Empty passwords
    are allowed.
    """
    repo = users_repo(db_file)
    repo.open()
    return repo.check_credentials(user, password)


def reset_user_password(
    db_file: str | Path,
    user: str,
    old_password: str,
    new_password: str,
) -> bool:
    """Set a new password after checking the old one.

    Returns False when the old password is wrong (or the account does
    not exist) — the two are the same answer, so the reset cannot be
    used to probe for user names. An empty new password is allowed.
    """
    repo = users_repo(db_file)
    repo.open()
    if not repo.check_credentials(user, old_password):
        return False
    repo.set_password(user, new_password)
    repo.persist()
    return True
