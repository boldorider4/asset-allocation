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
from typing import Any

from storage.memory_storage import MemoryStorage
from storage.records import AssetBucket, CacheEntry, IsinRecord
from storage.storage import Storage

__all__ = ["CacheRepository", "AssetRepository", "IsinRegistry"]

logger = logging.getLogger(__name__)


def _load_seed_records() -> dict[str, dict[str, Any]]:
    """Seed rows shipped with the package (one-time migration of the old maps).

    Raises if the seed file is missing — failing fast beats silently
    running with an empty registry (iShares/SSGA refs are undiscoverable
    by probing, so they would be lost for good).
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent / "seed_isin.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"registry seed {path} root must be a JSON object")
    return {str(k): dict(v) for k, v in raw.items()}


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
    """Portfolio buckets over any backend (separate store from the cache)."""

    def __init__(self, backend: Storage[AssetBucket]) -> None:
        self._backend = backend

    @property
    def backend(self) -> Storage[AssetBucket]:
        return self._backend

    def get(self, bucket: str) -> AssetBucket | None:
        return self._backend.get(bucket)

    def __contains__(self, bucket: object) -> bool:
        return bucket in self._backend

    def get_positions(self, bucket: str) -> list[dict[str, Any]] | None:
        obj = self._backend.get(bucket)
        return None if obj is None else obj.positions

    def set_positions(
        self, bucket: str, positions: list[dict[str, Any]]
    ) -> AssetBucket:
        """Replace a bucket wholesale (validated list-of-dicts)."""
        obj = AssetBucket(str(bucket), positions)
        self._backend.put(obj)
        return obj

    def remove_bucket(self, bucket: str) -> None:
        self._backend.remove(bucket)

    # -- backend-neutral lifecycle (delegated, works on every backend) --
    def open(self) -> AssetRepository:
        """Prepare the backend for use (idempotent)."""
        self._backend.open()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Whole store as plain ``{bucket: positions}`` (bulk-only, not hot-path)."""
        return self._backend.snapshot()

    def restore(self, rows: dict[str, Any]) -> int:
        """Bulk-load plain buckets with validation; skip bad ones with a warning.

        Returns the number of buckets restored. Used to push an externally
        seeded plain dict (e.g. ``ctx.portfolio``) into the backend.
        """
        count = 0
        for key, raw in rows.items():
            try:
                self._backend.put(AssetBucket.from_dict(str(key), raw))
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("skipping bad asset bucket %r (%s)", key, exc)
                continue
            count += 1
        return count

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

    @classmethod
    def seeded(cls, backend: Storage[IsinRecord] | None = None) -> IsinRegistry:
        """Registry pre-populated from the shipped seed file."""
        registry = cls(backend if backend is not None else MemoryStorage(IsinRecord))
        registry.open()
        return registry

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
    ) -> IsinRecord:
        """Ensure a row exists, filling only fields that are still null.

        Never overwrites a decided issuer — use :meth:`set_issuer_for_isin`
        for explicit (e.g. probe-inferred) corrections.
        """
        key = str(isin)
        existing = self._backend.get(key)
        if existing is None:
            record = IsinRecord(key, {"issuer": issuer, "bucket": bucket})
            self._backend.put(record)
            return record
        patch: dict[str, Any] = {}
        if existing.issuer is None and issuer is not None:
            patch[IsinRecord.ISSUER] = issuer
        if existing.bucket is None and bucket is not None:
            patch[IsinRecord.BUCKET] = bucket
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
        """Prepare the backend, seeding shipped rows wherever missing."""
        self._backend.open()
        for isin, spec in _load_seed_records().items():
            key = str(isin)
            existing = self._backend.get(key)
            if existing is None:
                self._backend.put(IsinRecord(key, dict(spec)))
                continue
            fill = {
                k: v
                for k, v in spec.items()
                if v is not None and existing.to_dict().get(k) is None
            }
            if fill:
                existing.merge(fill)
                self._backend.put(existing)
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
