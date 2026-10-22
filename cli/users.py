# SPDX-License-Identifier: AGPL-3.0-or-later
"""Managing the user accounts (the users store).

The accounts live in their own repository (``users.json`` today, a
table of the SQLite database when that backend is selected). This
module is the single place that writes them, so the script
(``scripts/add_users.py``) and the CLI command (``asalloc add-user``)
behave identically.
"""

from __future__ import annotations

import logging
from pathlib import Path

from storage.repositories import UsersRepository
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_SQLITE,
    normalize_backend,
    users_backend,
)

__all__ = ["add_user_account"]

logger = logging.getLogger(__name__)


def add_user_account(
    users_file: str | Path,
    user: str,
    password: str | None = None,
    *,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> bool:
    """Add the account ``user`` to the users store.

    Returns True when the account was created, False when it already
    existed. An existing account keeps its password unless a new one is
    given (``password=None`` means "leave it alone"); a new account
    gets an empty password when none is given.
    """
    name = str(user).strip()
    if not name:
        raise ValueError("the user name must not be empty")

    repo = UsersRepository(
        users_backend(users_file, backend, db_file)
    )
    repo.open()
    created = not repo.exists(name)
    if created:
        repo.add_user(name, password or "")
    elif password is not None:
        repo.set_password(name, password)
    repo.persist()
    logger.info(
        "%s user %r in %s", "added" if created else "updated", name, users_file
    )
    return created