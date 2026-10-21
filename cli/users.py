# SPDX-License-Identifier: AGPL-3.0-or-later
"""Managing the user accounts (the users store).

The accounts live in their own repository (``users.json`` today, a
Postgres table when that backend lands). This module is the single
place that writes them, so the script (``scripts/add_users.py``) and
the CLI command (``asalloc add-user``) behave identically.
"""

from __future__ import annotations

import logging
from pathlib import Path

from storage.repositories import UsersRepository
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_POSTGRES,
    DEFAULT_POSTGRES_DSN,
    POSTGRES_USERS_TABLE,
    normalize_backend,
    users_backend,
)

__all__ = ["add_user_account", "add_user_postgres"]

logger = logging.getLogger(__name__)


def add_user_postgres(
    dsn: str,
    user: str,
    password: str | None = None,
    table: str = POSTGRES_USERS_TABLE,
) -> bool:
    """Stub: writing accounts to Postgres is not implemented yet.

    The intended statement, once the Postgres backend lands::

        INSERT INTO users (key, payload) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload

    with ``key`` = the user name and ``payload`` = ``{"password": ...}``.
    """
    raise NotImplementedError(
        "adding a user to Postgres is not implemented yet; the intended "
        f"statement is: INSERT INTO {table} (key, payload) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload "
        f"(dsn={dsn})"
    )


def add_user_account(
    users_file: str | Path,
    user: str,
    password: str | None = None,
    *,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
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
    if normalize_backend(backend) == BACKEND_POSTGRES:
        return add_user_postgres(postgres_dsn, name, password)

    repo = UsersRepository(users_backend(users_file, BACKEND_JSON, postgres_dsn))
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
