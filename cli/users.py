# SPDX-License-Identifier: AGPL-3.0-or-later
"""Managing the user accounts in the SQLite database.

The accounts live in the ``users`` table, and every account gets a
matching ``user_portfolios`` row. This module is the single place that
writes them, so the script (``scripts/add_users.py``) and the CLI
command (``asalloc add-user``) behave identically.
"""

from __future__ import annotations

import logging
from pathlib import Path

from storage.repositories import UserPortfolioRepository, UsersRepository
from storage.wiring import user_portfolios_backend, users_backend

__all__ = ["add_user_account"]

logger = logging.getLogger(__name__)


def add_user_account(
    db_file: str | Path,
    user: str,
    password: str | None = None,
) -> bool:
    """Add the account ``user`` to the database.

    Returns True when the account was created, False when it already
    existed. An existing account keeps its password unless a new one is
    given (``password=None`` means "leave it alone"); a new account
    gets an empty password when none is given.

    The matching ``user_portfolios`` row is also created when missing.
    """
    name = str(user).strip()
    if not name:
        raise ValueError("the user name must not be empty")

    repo = UsersRepository(users_backend(db_file))
    repo.open()
    created = not repo.exists(name)
    if created:
        repo.add_user(name, password or "")
    elif password is not None:
        repo.set_password(name, password)
    repo.persist()
    if _ensure_portfolio_row(name, db_file):
        logger.info("created a portfolio row for user %r", name)
    logger.info(
        "%s user %r in %s", "added" if created else "updated", name, db_file
    )
    return created


def _ensure_portfolio_row(user: str, db_file: str | Path) -> bool:
    """Create the ``user_portfolios`` row for ``user`` when missing."""
    repo = UserPortfolioRepository(user_portfolios_backend(db_file))
    repo.open()
    return repo.ensure_exists(user)
