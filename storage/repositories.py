# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain repositories over any :mod:`storage.storage` backend.

Composition, not inheritance: each repository wraps a ``Storage[T]``
and adds domain logic. Swapping backends (JSON file ↔ Postgres ↔
in-memory) never touches this code — only the backend instance passed
to the constructor changes.

Repositories use *only* ABC methods (point access plus the
backend-neutral lifecycle ``open``/``snapshot``/``persist``/``close``),
so every method here works on every backend. Backend-specific nouns
(file ``load``/``save``, DB ``connect``/``commit``) never appear here —
or in callers, which drive durability through ``persist()`` alone.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    IsinRecord,
    UserAssets,
    UserRecord,
)
from storage.storage import Storage

__all__ = [
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "UsersRepository",
]

logger = logging.getLogger(__name__)


class UnknownUser(LookupError):
    """The requested user has no portfolio in the assets store."""

    def __init__(self, user: str) -> None:
        super().__init__(f"unknown user {user!r}: no portfolio stored for it")
        self.user = str(user)


class CacheRepository:
    """Per-ISIN price / country / sector cache over any backend."""

    def __init__(self, backend: Storage[CacheEntry]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[CacheEntry]:
        return self._backend

    def get(self, isin: str) -> CacheEntry | None:
        return self._backend.get(isin)

    def __contains__(self, isin: object) -> bool:
        return isin in self._backend

    def parsed(
        self, isin: str
    ) -> tuple[float | None, dict[str, float] | None, dict[str, float] | None]:
        """``(price, countries, sectors)``; ``(None, None, None)`` when missing."""
        entry = self._backend.get(isin)
        if entry is None:
            return None, None, None
        return entry.parsed()

    def stage(
        self,
        isin: str,
        *,
        price: float | None = None,
        countries: dict[str, float] | list[dict[str, Any]] | None = None,
        sectors: dict[str, float] | list[dict[str, Any]] | None = None,
        update_price: bool = False,
        update_countries: bool = False,
        update_sectors: bool = False,
    ) -> CacheEntry | None:
        """Stage a partial cache update (mirrors ``utils.save_position_in_cache``).

        ``countries``/``sectors`` accept either fraction dicts or
        ``[{"name", "weight_pct"}]`` position rows (converted via
        :meth:`CacheEntry.rows_to_fractions`). No-op returning ``None``
        when no update flag is set.
        """
        if not update_price and not update_countries and not update_sectors:
            return None
        partial: dict[str, Any] = {}
        if update_price and price is not None:
            partial[CacheEntry.PRICE] = price
        if update_countries and countries is not None:
            partial[CacheEntry.COUNTRIES] = (
                CacheEntry.rows_to_fractions(countries)
                if isinstance(countries, list)
                else countries
            )
        if update_sectors and sectors is not None:
            partial[CacheEntry.SECTORS] = (
                CacheEntry.rows_to_fractions(sectors)
                if isinstance(sectors, list)
                else sectors
            )
        return self._backend.upsert(str(isin), partial)

    def stage_quotes(self, quotes: dict[str, float | None]) -> int:
        """Stage broker unit prices; skips ``None`` quotes. Returns count.

        No fetch-gating here (the caller owns config); mirrors the
        write half of ``utils.cache_broker_quotes``.
        """
        count = 0
        for isin, quote in quotes.items():
            if not isin or quote is None:
                continue
            self._backend.upsert(str(isin), {CacheEntry.PRICE: float(quote)})
            count += 1
        return count

    def clear_fields(self, isin: str, *fields: str) -> bool:
        """Remove ``fields`` from one row (e.g. stale splits); True if changed."""
        entry = self._backend.get(isin)
        if entry is None:
            return False
        if entry.clear_fields(*fields):
            # Re-put so non-caching backends (Postgres) persist the change.
            self._backend.put(entry)
            return True
        return False

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> CacheRepository:
        """Prepare the backend for use (idempotent)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Whole cache as plain ``{isin: row}`` (bulk-only, not hot-path)."""
        return self._backend.snapshot()

    def restore(self, rows: dict[str, Any]) -> int:
        """Bulk-load plain rows with validation; skip bad rows with a warning.

        Returns the number of rows restored. Used to push an externally
        seeded plain dict (e.g. ``ctx.cache``) into the backend.
        """
        count = 0
        for key, raw in rows.items():
            try:
                self._backend.put(CacheEntry.from_dict(str(key), raw))
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("skipping bad cache row %r (%s)", key, exc)
                continue
            count += 1
        return count

    def persist(self) -> None:
        """Make staged writes durable (save / commit / no-op by backend)."""
        self._backend.persist()

    def close(self) -> None:
        """Backend-defined teardown (see :meth:`Storage.close`)."""
        self._backend.close()


class AssetRepository:
    """One user's portfolio (name + positions) over any backend.

    The backend holds one row per user (a :class:`UserAssets` row); this
    repository is scoped to a single user and speaks in that user's
    positions (``{asset class: [positions]}``) plus the name of the
    portfolio. Other users' rows are never read or written.
    """

    def __init__(self, user: str, backend: Storage[UserAssets]) -> None:
        self._user = str(user)
        self._backend = backend

    @property
    def user(self) -> str:
        """The user this repository is scoped to."""
        return self._user

    @property
    def backend(self) -> Storage[UserAssets]:
        return self._backend

    def _row(self) -> UserAssets | None:
        return self._backend.get(self._user)

    def exists(self) -> bool:
        """True when the store holds a portfolio for this user."""
        return self._user in self._backend

    def store_is_empty(self) -> bool:
        """True when the store holds no portfolios at all.

        Bulk read (a full scan on DB backends): callers use it on the
        error path to tell "no store yet" (misconfiguration) from
        "no portfolio for this user".
        """
        return not self._backend.snapshot()

    def require(self) -> UserAssets:
        """This user's row, or :class:`UnknownUser` when there is none."""
        row = self._row()
        if row is None:
            raise UnknownUser(self._user)
        return row

    @property
    def portfolio_name(self) -> str:
        """The name of this user's portfolio."""
        row = self._row()
        return DEFAULT_PORTFOLIO_NAME if row is None else row.portfolio_name

    def set_portfolio_name(self, name: str) -> None:
        """Rename this user's portfolio (creates the row when missing)."""
        row = self._backend.get_or_create(self._user)
        row.set_portfolio_name(name)
        self._backend.put(row)

    def get(self, asset_class: str) -> list[dict[str, Any]] | None:
        """One asset class's positions, or None when the user has none."""
        row = self._row()
        return None if row is None else row.positions().get(str(asset_class))

    def __contains__(self, asset_class: object) -> bool:
        row = self._row()
        return row is not None and str(asset_class) in row.positions()

    def get_positions(self, asset_class: str) -> list[dict[str, Any]] | None:
        return self.get(asset_class)

    def set_positions(
        self, asset_class: str, positions: list[dict[str, Any]]
    ) -> UserAssets:
        """Replace an asset class wholesale (validated list-of-dicts)."""
        row = self._backend.get_or_create(self._user)
        row.set_positions(str(asset_class), positions)
        self._backend.put(row)
        return row

    def remove_positions(self, asset_class: str) -> None:
        """Drop one asset class of this user (no-op when absent)."""
        row = self._row()
        if row is None:
            return
        if row.remove_positions(str(asset_class)):
            self._backend.put(row)

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> AssetRepository:
        """Prepare the backend for use (idempotent)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """This user's positions as plain ``{asset class: [positions]}``."""
        row = self._row()
        return {} if row is None else row.positions()

    def restore(
        self, positions: dict[str, Any], *, portfolio_name: str | None = None
    ) -> int:
        """Replace this user's positions with ``positions`` (validated).

        Bad asset classes are skipped with a warning (the historical
        lenient behavior); the rest replace this user's positions
        wholesale. The portfolio name is kept unless ``portfolio_name``
        is given. Other users' rows stay untouched.

        Returns the number of asset classes restored.
        """
        good: dict[str, Any] = {}
        for key, raw in positions.items():
            try:
                AssetBucket.from_dict(str(key), raw)
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("skipping bad asset bucket %r (%s)", key, exc)
                continue
            good[str(key)] = raw
        row = self._backend.get_or_create(self._user)
        row.replace_all(good, portfolio_name=portfolio_name)
        self._backend.put(row)
        return len(good)

    def persist(self) -> None:
        """Make staged writes durable (save / commit / no-op by backend)."""
        self._backend.persist()

    def close(self) -> None:
        """Backend-defined teardown (see :meth:`Storage.close`)."""
        self._backend.close()


class UsersRepository:
    """User accounts (name + password) over any backend.

    The backend holds one row per account; the key is the user name
    (the primary key a database table would carry) and the payload is
    the password. Passwords are stored as given (plaintext) — hashing
    belongs in :class:`~storage.records.UserRecord` when it lands.
    """

    def __init__(self, backend: Storage[UserRecord]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[UserRecord]:
        return self._backend

    def get(self, user: str) -> UserRecord | None:
        """The account for ``user``, or None when there is none."""
        return self._backend.get(str(user))

    def __contains__(self, user: object) -> bool:
        return str(user) in self._backend

    def exists(self, user: str) -> bool:
        """True when the store holds an account for ``user``."""
        return str(user) in self._backend

    def list_users(self) -> list[str]:
        """Every account name in the store (bulk read, sorted).

        Bulk-only (a full scan on DB backends): callers use it on the
        error path or for listings, not on the hot path.
        """
        return sorted(str(key) for key in self._backend.snapshot())

    def check_credentials(self, user: str, password: str) -> bool:
        """True when the account exists and the password matches.

        The comparison is constant-time and always runs (an unknown
        user is compared against an empty password), so the answer
        never leaks whether the account exists. Empty passwords are
        allowed: an account with no password matches an empty one.
        """
        record = self.get(user)
        expected = record.password if record is not None else ""
        matched = secrets.compare_digest(
            expected.encode("utf-8"), str(password).encode("utf-8")
        )
        return record is not None and matched

    def add_user(self, user: str, password: str = "") -> UserRecord:
        """Create the account for ``user``; returns its record.

        An existing account keeps its password (use
        :meth:`set_password` to change it) — adding is a no-op then.
        """
        key = str(user)
        existing = self._backend.get(key)
        if existing is not None:
            return existing
        record = UserRecord(key, {UserRecord.PASSWORD: str(password)})
        self._backend.add(record)
        return record

    def set_password(self, user: str, password: str) -> UserRecord:
        """Set the password of ``user``; creates the account if needed."""
        key = str(user)
        record = self._backend.get(key)
        if record is None:
            record = UserRecord(key, {UserRecord.PASSWORD: str(password)})
            self._backend.add(record)
            return record
        if record.password != str(password):
            record.password = str(password)
            self._backend.put(record)
        return record

    def remove_user(self, user: str) -> bool:
        """Drop the account for ``user``; True when one was removed."""
        key = str(user)
        if key not in self._backend:
            return False
        self._backend.remove(key)
        return True

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> UsersRepository:
        """Prepare the backend for use (idempotent)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Whole store as plain ``{user: row}`` (bulk-only)."""
        return self._backend.snapshot()

    def persist(self) -> None:
        """Make staged writes durable (save / commit / no-op by backend)."""
        self._backend.persist()

    def close(self) -> None:
        """Backend-defined teardown (see :meth:`Storage.close`)."""
        self._backend.close()


class IsinRegistry:
    """ISIN registry over any backend (in-memory by default).

    Each row maps an ISIN to its issuer handler, portfolio bucket and an
    optional vendor product ref. ``None`` issuer means "unknown, probe on
    the next geosplit run"; ``justetf``/``yfinance`` mean "decided generic,
    never re-probe".
    """

    def __init__(self, backend: Storage[IsinRecord]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[IsinRecord]:
        return self._backend

    def get(self, isin: str) -> IsinRecord | None:
        return self._backend.get(isin)

    def __contains__(self, isin: object) -> bool:
        return isin in self._backend

    def get_issuer_for_isin(self, isin: str) -> str | None:
        record = self._backend.get(str(isin))
        return None if record is None else record.issuer

    def get_bucket_for_isin(self, isin: str) -> str | None:
        record = self._backend.get(str(isin))
        return None if record is None else record.bucket

    def get_product_ref_for_isin(self, isin: str) -> str | None:
        record = self._backend.get(str(isin))
        return None if record is None else record.product_ref

    def product_ref_for(self, isin: str) -> str | None:
        """Alias of :meth:`get_product_ref_for_isin` (kept for callers)."""
        return self.get_product_ref_for_isin(isin)

    def register_isin(
        self,
        isin: str,
        *,
        issuer: str | None = None,
        bucket: str | None = None,
        product_ref: str | None = None,
    ) -> IsinRecord:
        """Ensure a row exists, filling only fields that are still null.

        Never overwrites decided fields — use :meth:`set_issuer_for_isin`
        for explicit (e.g. probe-inferred) corrections.
        """
        key = str(isin)
        existing = self._backend.get(key)
        if existing is None:
            record = IsinRecord(
                key, {"issuer": issuer, "bucket": bucket, "product_ref": product_ref}
            )
            self._backend.put(record)
            return record
        patch: dict[str, Any] = {}
        if existing.issuer is None and issuer is not None:
            patch[IsinRecord.ISSUER] = issuer
        if existing.bucket is None and bucket is not None:
            patch[IsinRecord.BUCKET] = bucket
        if existing.product_ref is None and product_ref is not None:
            patch[IsinRecord.PRODUCT_REF] = product_ref
        if patch:
            existing.merge(patch)
            self._backend.put(existing)
        return existing

    def set_issuer_for_isin(self, isin: str, issuer: str) -> IsinRecord:
        """Overwrite the issuer for ``isin`` (probe-inferred corrections)."""
        key = str(isin)
        existing = self._backend.get(key)
        if existing is None:
            record = IsinRecord(key, {"issuer": issuer})
            self._backend.put(record)
            return record
        if existing.issuer != issuer:
            existing.merge({IsinRecord.ISSUER: issuer})
            self._backend.put(existing)
        return existing

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> IsinRegistry:
        """Open the underlying ``isin.json`` backend (read-only use)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Whole store as plain ``{isin: row}`` (bulk-only, not hot-path)."""
        return self._backend.snapshot()

    def persist(self) -> None:
        """Make staged writes durable (save / commit / no-op by backend)."""
        self._backend.persist()

    def close(self) -> None:
        """Backend-defined teardown (see :meth:`Storage.close`)."""
        self._backend.close()
