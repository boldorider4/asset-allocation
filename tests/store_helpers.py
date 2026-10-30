# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite store helpers for tests.

The runtime has exactly one store: the SQLite database named by
``[server] db_file``. These helpers create a fresh database in a test's
temp directory, seed it through the same repositories the app uses, and
read it back without touching any JSON files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cli.context import AppConfig, RuntimeContext, ServerConfig
from storage.records import DEFAULT_PORTFOLIO_NAME, IsinRecord
from storage.repositories import (
    AssetRepository,
    CacheRepository,
    IsinRegistry,
    PositionRepository,
    UserPortfolioRepository,
    UsersRepository,
)
from storage.wiring import (
    cache_backend,
    isin_backend,
    positions_backend,
    user_portfolios_backend,
    users_backend,
)

__all__ = [
    "add_user",
    "db_path",
    "make_config",
    "make_context",
    "make_server",
    "read_cache",
    "read_isin",
    "read_positions",
    "read_users",
    "write_cache",
    "write_isin",
    "write_positions",
    "write_users",
]


def db_path(tmp: str | Path) -> Path:
    """The canonical database path for a test temp directory."""
    return Path(tmp) / "asalloc.db"


def make_server(tmp: str | Path, **overrides: Any) -> ServerConfig:
    """A server config pointing every path into ``tmp``."""
    base = Path(tmp)
    values: dict[str, Any] = {
        "port": 0,
        "address": "localhost",
        "directory": base / "visualizer",
        "db_file": db_path(base),
    }
    values.update(overrides)
    return ServerConfig(**values)


def make_config(
    tmp: str | Path,
    *,
    user: str = "default",
    server: ServerConfig | None = None,
    **overrides: Any,
) -> AppConfig:
    """An :class:`AppConfig` whose database lives under ``tmp``."""
    return AppConfig(
        user=user,
        server=server if server is not None else make_server(tmp),
        **overrides,
    )


def _asset_repo(db_file: str | Path, user: str) -> AssetRepository:
    return AssetRepository(
        user,
        position_repo=PositionRepository(positions_backend(db_file)),
        portfolio_repo=UserPortfolioRepository(user_portfolios_backend(db_file)),
    )


def write_positions(
    db_file: str | Path,
    positions: Mapping[str, list[dict[str, Any]]],
    *,
    user: str = "default",
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> None:
    """Replace one user's positions (and create the portfolio row)."""
    repo = _asset_repo(db_file, user)
    repo.open()
    repo.restore(dict(positions), portfolio_name=portfolio_name)
    repo.persist()
    repo.close()


def read_positions(
    db_file: str | Path, *, user: str = "default"
) -> dict[str, list[dict[str, Any]]]:
    """One user's positions grouped by asset class."""
    repo = _asset_repo(db_file, user)
    repo.open()
    out = repo.snapshot()
    repo.close()
    return out


def write_cache(db_file: str | Path, cache: Mapping[str, Any]) -> None:
    """Replace the whole per-ISIN cache."""
    repo = CacheRepository(cache_backend(db_file))
    repo.open()
    repo.restore(dict(cache))
    repo.persist()
    repo.close()


def read_cache(db_file: str | Path) -> dict[str, Any]:
    """The whole per-ISIN cache as plain dicts."""
    repo = CacheRepository(cache_backend(db_file))
    repo.open()
    out = repo.snapshot()
    repo.close()
    return out


def write_users(
    db_file: str | Path, users: Mapping[str, str]
) -> None:
    """Create/replace user accounts (name -> password)."""
    repo = UsersRepository(users_backend(db_file))
    repo.open()
    for name, password in users.items():
        repo.set_password(str(name), str(password))
    repo.persist()
    repo.close()


def add_user(db_file: str | Path, user: str, password: str = "") -> None:
    """Create one account (idempotent)."""
    repo = UsersRepository(users_backend(db_file))
    repo.open()
    repo.add_user(str(user), str(password))
    repo.persist()
    repo.close()


def read_users(db_file: str | Path) -> dict[str, Any]:
    """All accounts as plain dicts."""
    repo = UsersRepository(users_backend(db_file))
    repo.open()
    out = repo.snapshot()
    repo.close()
    return out


def write_isin(
    db_file: str | Path,
    rows: Mapping[str, Mapping[str, Any] | None],
) -> None:
    """Seed the ISIN registry (``{isin: {issuer, bucket, product_ref}}``)."""
    repo = IsinRegistry(isin_backend(db_file))
    repo.open()
    for isin, fields in rows.items():
        if fields is None:
            repo.register_isin(str(isin))
            continue
        record = repo.register_isin(
            str(isin),
            issuer=fields.get("issuer"),
            bucket=fields.get("bucket"),
            product_ref=fields.get("product_ref"),
        )
        if "visits" in fields:
            record.merge({IsinRecord.VISITS: int(fields["visits"])})
            repo.backend.put(record)
    repo.persist()
    repo.close()


def read_isin(db_file: str | Path) -> dict[str, Any]:
    """The ISIN registry as plain dicts."""
    repo = IsinRegistry(isin_backend(db_file))
    repo.open()
    out = repo.snapshot()
    repo.close()
    return out


def make_context(
    tmp: str | Path,
    *,
    user: str = "default",
    portfolio: Mapping[str, list[dict[str, Any]]] | None = None,
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
    cache: Mapping[str, Any] | None = None,
    users: Mapping[str, str] | None = None,
    isin: Mapping[str, Mapping[str, Any] | None] | None = None,
    server: ServerConfig | None = None,
    **config_overrides: Any,
) -> RuntimeContext:
    """Build a context with an isolated database, optionally seeded."""
    config = make_config(
        tmp, user=user, server=server, **config_overrides
    )
    db = config.server.db_file
    if portfolio is not None:
        write_positions(
            db, portfolio, user=user, portfolio_name=portfolio_name
        )
    if cache is not None:
        write_cache(db, cache)
    if users is not None:
        write_users(db, users)
    if isin is not None:
        write_isin(db, isin)
    return RuntimeContext(config=config)
