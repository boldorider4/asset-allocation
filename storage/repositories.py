# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain repositories over any :mod:`storage.storage` backend.

Composition, not inheritance: each repository wraps a ``Storage[T]``
and adds domain logic. Swapping backends (JSON file <-> Postgres <->
in-memory) never touches this code -- only the backend instance passed
to the constructor changes.

Repositories use *only* ABC methods (point access plus the
backend-neutral lifecycle ``open``/``snapshot``/``persist``/``close``),
so every method here works on every backend. Backend-specific nouns
(file ``load``/``save``, DB ``connect``/``commit``) never appear here --
or in callers, which drive durability through ``persist()`` alone.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    IsinRecord,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.storage import Storage

__all__ = [
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "UsersRepository",
    "SessionsRepository",
    "PositionRepository",
    "UserPortfolioRepository",
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


class PositionRepository:
    """Per-position operations over the normalized ``user_positions`` table.

    A position is identified by the composite key
    ``(username, bucket, position_idx)``; the app-facing dict uses
    ``position_index`` for the last part.
    """

    def __init__(self, position_backend: Storage[PositionRecord]) -> None:
        self._positions = position_backend

    @property
    def position_backend(self) -> Storage[PositionRecord]:
        return self._positions

    # -- Position CRUD --

    def get_for_user(self, username: str) -> dict[str, list[dict[str, Any]]]:
        """Return all positions for a user grouped by bucket: ``{bucket: [positions]}``.

        ``username="all"`` returns every user's positions.
        """
        self._positions.open()
        rows = self._positions.snapshot()
        buckets: dict[str, list[dict[str, Any]]] = {}
        for key, raw in rows.items():
            try:
                record = PositionRecord.from_dict(str(key), raw)
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("skipping bad position row %r (%s)", key, exc)
                continue
            if username == "all" or record.username == username:
                pos = self._record_to_dict(record)
                buckets.setdefault(record.bucket, []).append(pos)
        for bucket_positions in buckets.values():
            bucket_positions.sort(key=lambda p: p["position_index"])
        return buckets

    def get_for_bucket(self, username: str, bucket: str) -> list[dict[str, Any]]:
        """Return one user's positions in a bucket, ordered by position_idx."""
        self._positions.open()
        return self.get_for_user(username).get(bucket, [])

    def get_position(
        self, username: str, bucket: str, position_idx: int
    ) -> dict[str, Any] | None:
        """Get one position by its composite key."""
        self._positions.open()
        key = PositionRecord.encode_key(username, bucket, position_idx)
        record = self._positions.get(key)
        if record is None:
            return None
        return self._record_to_dict(record)

    def upsert_position(
        self,
        username: str,
        bucket: str,
        position_idx: int,
        name: str,
        short_name: str | None = None,
        isin: str | None = None,
        value: float | None = None,
        shares: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        dmem_other: float | None = None,
        usavn: float | None = None,
    ) -> dict[str, Any]:
        """Insert or update a single position."""
        self._positions.open()
        record = PositionRecord(
            username=username,
            bucket=bucket,
            position_idx=position_idx,
            name=name,
            short_name=short_name,
            isin=isin,
            value=value,
            shares=shares,
            broker=broker,
            dmem=dmem,
            dmem_other=dmem_other,
            usavn=usavn,
        )
        self._positions.put(record)
        self._positions.persist()
        return self._record_to_dict(record)

    def delete_position(
        self, username: str, bucket: str, position_idx: int
    ) -> bool:
        """Delete one position by its composite key. Returns True if deleted."""
        self._positions.open()
        key = PositionRecord.encode_key(username, bucket, position_idx)
        if key not in self._positions:
            return False
        self._positions.remove(key)
        self._positions.persist()
        return True

    def delete_bucket(self, username: str, bucket: str) -> int:
        """Delete every position in one user's bucket; returns the count."""
        self._positions.open()
        positions = self.get_for_bucket(username, bucket)
        for pos in positions:
            self._positions.remove(
                PositionRecord.encode_key(
                    username, bucket, pos["position_index"]
                )
            )
        self._positions.persist()
        return len(positions)

    def reorder_positions(
        self, username: str, bucket: str, new_order: list[int]
    ) -> None:
        """Reorder one bucket.

        ``new_order`` lists the *current* ``position_idx`` values in their
        new order (an exact permutation). Rows are rewritten because
        shifting indices in place could collide on the primary key.
        """
        positions = self.get_for_bucket(username, bucket)
        if sorted(new_order) != list(range(len(positions))):
            raise ValueError(
                f"new_order must be a permutation of 0..{len(positions) - 1}, "
                f"got {new_order!r}"
            )
        ordered = [positions[index] for index in new_order]
        self.delete_bucket(username, bucket)
        for new_index, pos in enumerate(ordered):
            self._positions.put(
                PositionRecord(
                    username=username,
                    bucket=bucket,
                    position_idx=new_index,
                    name=pos.get("name", ""),
                    short_name=pos.get("short_name"),
                    isin=pos.get("ISIN") or pos.get("isin"),
                    value=pos.get("value"),
                    shares=pos.get("shares"),
                    broker=pos.get("broker"),
                    dmem=pos.get("dmem"),
                    dmem_other=pos.get("dmem_other"),
                    usavn=pos.get("usavn"),
                )
            )
        self._positions.persist()

    def batch_upsert_positions(
        self, username: str, bucket: str, positions: list[dict[str, Any]]
    ) -> None:
        """Replace all positions in a bucket for a user with the given list."""
        validated = AssetBucket.from_dict(str(bucket), positions)
        self._positions.open()
        self.delete_bucket(username, bucket)
        for index, pos in enumerate(validated.positions):
            record = PositionRecord(
                username=username,
                bucket=bucket,
                position_idx=index,
                name=pos.get("name", ""),
                short_name=pos.get("short_name") or pos.get("group"),
                isin=pos.get("ISIN") or pos.get("isin"),
                value=pos.get("value"),
                shares=pos.get("shares"),
                broker=pos.get("broker"),
                dmem=pos.get("dmem"),
                dmem_other=pos.get("dmem_other"),
                usavn=pos.get("usavn"),
            )
            self._positions.put(record)
        self._positions.persist()

    # -- Internal helpers --

    def _record_to_dict(self, record: PositionRecord) -> dict[str, Any]:
        """Convert a PositionRecord to the app-facing position dict.

        ISIN is uppercase (the app-wide key), ``short_name`` keeps the
        DB's null when it was never set, and there is no ``id``: lookups
        use ``(username, bucket, position_index)``.
        """
        data = record.to_dict()
        return {
            "username": record.username,
            "bucket": record.bucket,
            "position_index": record.position_idx,
            "name": record.name,
            "short_name": data.get(PositionRecord.SHORT_NAME),
            "ISIN": data.get(PositionRecord.ISIN),
            "value": record.value,
            "shares": record.shares,
            "broker": record.broker,
            "dmem": record.dmem,
            "dmem_other": record.dmem_other,
            "usavn": record.usavn,
        }

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> PositionRepository:
        self._positions.open()
        return self

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """All positions for all users grouped by bucket."""
        return self.get_for_user("all")

    def persist(self) -> None:
        self._positions.persist()

    def close(self) -> None:
        self._positions.close()


class UserPortfolioRepository:
    """User portfolio metadata (name only)."""

    def __init__(self, backend: Storage[UserPortfolioRecord]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[UserPortfolioRecord]:
        return self._backend

    def get_name(self, username: str) -> str:
        self._backend.open()
        record = self._backend.get(username)
        return record.portfolio_name if record else DEFAULT_PORTFOLIO_NAME

    def set_name(self, username: str, name: str) -> None:
        self._backend.open()
        record = self._backend.get_or_create(username)
        record.portfolio_name = name
        self._backend.put(record)
        self._backend.persist()

    def ensure_exists(self, username: str) -> bool:
        """Create an empty portfolio row if it doesn't exist. Returns True if created."""
        self._backend.open()
        if username not in self._backend:
            self._backend.put(UserPortfolioRecord(key=username, portfolio_name=DEFAULT_PORTFOLIO_NAME))
            self._backend.persist()
            return True
        return False

    def open(self) -> UserPortfolioRepository:
        self._backend.open()
        return self

    def persist(self) -> None:
        self._backend.persist()

    def close(self) -> None:
        self._backend.close()


class AssetRepository:
    """One user's portfolio (name + positions) over any backend.

    Portfolio metadata lives in ``user_portfolios`` and positions in
    ``user_positions``. This repository is scoped to a single user and
    speaks in that user's positions (``{asset class: [positions]}``)
    plus the name of the portfolio. Other users' rows are never read or
    written.
    """

    def __init__(
        self,
        user: str,
        position_repo: PositionRepository,
        portfolio_repo: UserPortfolioRepository,
    ) -> None:
        self._user = str(user)
        self._position_repo = position_repo
        self._portfolio_repo = portfolio_repo

    @property
    def user(self) -> str:
        """The user this repository is scoped to."""
        return self._user

    @property
    def position_repo(self) -> PositionRepository:
        return self._position_repo

    @property
    def portfolio_repo(self) -> UserPortfolioRepository:
        return self._portfolio_repo

    def exists(self) -> bool:
        """True when the store holds a portfolio for this user."""
        self._portfolio_repo.open()
        return self._user in self._portfolio_repo.backend

    def store_is_empty(self) -> bool:
        """True when the store holds no portfolios at all.

        Bulk read (a full scan on DB backends): callers use it on the
        error path to tell "no store yet" (misconfiguration) from
        "no portfolio for this user".
        """
        self._portfolio_repo.open()
        return not self._portfolio_repo.backend.snapshot()

    def require(self) -> UserPortfolioRecord:
        """This user's portfolio row, or :class:`UnknownUser` when there is none."""
        row = self._portfolio_repo.backend.get(self._user)
        if row is None:
            raise UnknownUser(self._user)
        return row

    @property
    def portfolio_name(self) -> str:
        """The name of this user's portfolio."""
        return self._portfolio_repo.get_name(self._user)

    def set_portfolio_name(self, name: str) -> None:
        """Rename this user's portfolio (creates the row when missing)."""
        self._portfolio_repo.set_name(self._user, name)

    def get(self, asset_class: str) -> list[dict[str, Any]] | None:
        """One asset class's positions, or None when the user has none."""
        return self._position_repo.get_for_user(self._user).get(asset_class)

    def __contains__(self, asset_class: object) -> bool:
        return asset_class in self._position_repo.get_for_user(self._user)

    def get_positions(self, asset_class: str) -> list[dict[str, Any]] | None:
        return self.get(asset_class)

    def set_positions(
        self, asset_class: str, positions: list[dict[str, Any]]
    ) -> None:
        """Replace an asset class wholesale (validated list-of-dicts)."""
        self._position_repo.batch_upsert_positions(self._user, asset_class, positions)

    def remove_positions(self, asset_class: str) -> None:
        """Drop one asset class of this user (no-op when absent)."""
        self._position_repo.delete_bucket(self._user, asset_class)

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> AssetRepository:
        self._position_repo.open()
        self._portfolio_repo.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """This user's positions as plain ``{asset class: [positions]}``."""
        return self._position_repo.get_for_user(self._user)

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
        existing = set(self._position_repo.get_for_user(self._user))
        for bucket in existing - set(good):
            self._position_repo.delete_bucket(self._user, bucket)
        for bucket, positions in good.items():
            self._position_repo.batch_upsert_positions(self._user, bucket, positions)
        if portfolio_name is not None:
            self.set_portfolio_name(portfolio_name)
        return len(good)

    def persist(self) -> None:
        self._position_repo.persist()
        self._portfolio_repo.persist()

    def close(self) -> None:
        self._position_repo.close()
        self._portfolio_repo.close()


class UsersRepository:
    """User accounts (name + password) over any backend.

    The backend holds one row per account; the key is the user name
    (the primary key of the ``users`` table) and the value is the
    password. Passwords are stored as given (plaintext) -- hashing
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
        :meth:`set_password` to change it) -- adding is a no-op then.
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


class SessionsRepository:
    """Browser sessions over any backend.

    The backend holds one row per session id (the value of the
    ``asalloc_sid`` cookie); the row carries the user, the incognito
    state and the expiry. An expired session reads as *no* session, so
    the caller never has to think about the timeout.
    """

    def __init__(self, backend: Storage[SessionRecord]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[SessionRecord]:
        return self._backend

    def get(self, session_id: str) -> SessionRecord | None:
        """The live session for ``session_id``, or None when there is none."""
        record = self._backend.get(str(session_id))
        if record is None or record.expired():
            return None
        return record

    def start(
        self,
        session_id: str,
        user: str,
        *,
        incognito: bool = False,
        ttl_seconds: float = 0.0,
    ) -> SessionRecord:
        """Start (or replace) the session for ``session_id``.

        ``ttl_seconds`` of 0 (or less) means "expires now" -- the
        timeout is the caller's business.
        """
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0
        record = SessionRecord(
            str(session_id),
            {
                SessionRecord.USER: str(user),
                SessionRecord.INCOGNITO: bool(incognito),
                SessionRecord.EXPIRES_AT: expires_at,
            },
        )
        self._backend.put(record)
        return record

    def drop(self, session_id: str) -> bool:
        """Drop the session for ``session_id``; True when one was removed."""
        key = str(session_id)
        if key not in self._backend:
            return False
        self._backend.remove(key)
        return True

    def purge_expired(self) -> int:
        """Remove the expired sessions; returns how many were dropped."""
        expired = [
            key
            for key, record in self._backend.snapshot().items()
            if isinstance(record, dict)
            and SessionRecord.from_dict(str(key), record).expired()
        ]
        for key in expired:
            self._backend.remove(key)
        return len(expired)

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> SessionsRepository:
        """Prepare the backend for use (idempotent)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Whole store as plain ``{session id: row}`` (bulk-only)."""
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

        Never overwrites decided fields -- use :meth:`set_issuer_for_isin`
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


if __name__ == "__main__":
    unittest.main()
