# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backend-agnostic row types for the :mod:`storage.storage` abstractions.

All rows serialise via ``to_dict()`` / ``from_dict()`` and validate via
``merge()`` / ``validate()``, so any backend (JSON file, Postgres,
in-memory) can persist them without knowing their fields:

* :class:`DictRow` — generic dict-backed row with an explicit
  ``ALLOWED_KEYS`` allow-list (strict-on-write: unknown keys raise).
* :class:`CacheEntry` — one per-ISIN cache row: ``price``,
  ``countries`` and ``sectors`` fractions of 1. Mirrors the semantics of
  ``utils.parse_cache_entry`` / ``utils.save_position_in_cache``.
* :class:`AssetBucket` — one portfolio bucket: a list of position
  dicts. Note ``to_dict()`` returns a *list*, not a dict — backends
  must accept any JSON-encodable payload (JSON object values, JSONB
  columns, ...).
* :class:`IsinRecord` — one issuer allow-list entry consolidating the
  ``ISINS`` frozensets / product-id maps in ``position/*_position.py``.

Nothing here touches I/O; loading leniency (skip bad rows) is a
backend concern, while these classes are strict (bad keys/values raise).
"""

from __future__ import annotations

import math
from typing import Any

from storage.storage import StorageObject

__all__ = [
    "DictRow",
    "CacheEntry",
    "AssetBucket",
    "IsinRecord",
    "DEFAULT_ISIN_RECORDS",
]


class DictRow(StorageObject):
    """Dict-backed row with an explicit key allow-list."""

    ALLOWED_KEYS: frozenset[str] = frozenset()

    def __init__(self, key: str, data: dict[str, Any] | None = None) -> None:
        self._key = str(key)
        self._data: dict[str, Any] = dict(data) if data else {}
        self.validate()

    @property
    def key(self) -> str:
        return self._key

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> DictRow:
        if not isinstance(raw, dict):
            raise TypeError(f"row {key!r} must be a JSON object, got {type(raw).__name__}")
        unknown = set(raw) - set(cls.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {key!r} has unknown keys: {sorted(unknown)}")
        return cls(str(key), dict(raw))

    def merge(self, partial: dict[str, Any]) -> None:
        if not isinstance(partial, dict):
            raise TypeError(f"partial update for {self._key!r} must be a dict")
        unknown = set(partial) - set(type(self).ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {self._key!r} has unknown keys: {sorted(unknown)}")
        merged = dict(self._data)
        merged.update(partial)
        old = self._data
        self._data = merged
        try:
            self.validate()
        except Exception:
            self._data = old
            raise

    def validate(self) -> None:
        unknown = set(self._data) - set(type(self).ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {self._key!r} has unknown keys: {sorted(unknown)}")

    def clear_fields(self, *names: str) -> bool:
        """Remove ``names`` from this row; True when anything changed."""
        changed = False
        for name in names:
            if name not in type(self).ALLOWED_KEYS:
                raise KeyError(f"row {self._key!r} has unknown key: {name!r}")
            if name in self._data:
                del self._data[name]
                changed = True
        return changed

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DictRow)
            and type(self) is type(other)
            and self._key == other._key
            and self._data == other._data
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key={self._key!r}, data={self._data!r})"


class CacheEntry(DictRow):
    """One per-ISIN cache row; split weights are fractions of 1."""

    PRICE = "price"
    COUNTRIES = "countries"
    SECTORS = "sectors"
    ALLOWED_KEYS: frozenset[str] = frozenset({PRICE, COUNTRIES, SECTORS})

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        price: float | None = None,
        countries: dict[str, float] | None = None,
        sectors: dict[str, float] | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if price is not None:
            payload[self.PRICE] = price
        if countries is not None:
            payload[self.COUNTRIES] = countries
        if sectors is not None:
            payload[self.SECTORS] = sectors
        super().__init__(key, self._coerce(payload, key=str(key)))

    @staticmethod
    def _coerce_split(value: Any, *, field: str, key: str) -> dict[str, float]:
        # Strict: split rows are normalized at assembly (see
        # ``position.normalize_split_rows``), so anything outside [0, 1]
        # here is a genuine error, not source rounding.
        if not isinstance(value, dict):
            raise TypeError(f"row {key!r} field {field!r} must be an object")
        out: dict[str, float] = {}
        for name, weight in value.items():
            try:
                w = float(weight)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"row {key!r} field {field!r} weight for {name!r} is not a number"
                ) from exc
            if not math.isfinite(w) or not 0.0 <= w <= 1.0:
                raise ValueError(
                    f"row {key!r} field {field!r} weight for {name!r} "
                    f"must be a fraction in [0, 1], got {weight!r}"
                )
            out[str(name)] = w
        return out

    @classmethod
    def _coerce(cls, payload: dict[str, Any], *, key: str) -> dict[str, Any]:
        coerced: dict[str, Any] = {}
        if cls.PRICE in payload and payload[cls.PRICE] is not None:
            try:
                price = float(payload[cls.PRICE])  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"row {key!r} field 'price' is not a number") from exc
            if not math.isfinite(price) or price < 0:
                raise ValueError(f"row {key!r} field 'price' must be >= 0")
            coerced[cls.PRICE] = price
        for field in (cls.COUNTRIES, cls.SECTORS):
            if field in payload and payload[field] is not None:
                coerced[field] = cls._coerce_split(payload[field], field=field, key=key)
        return coerced

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> CacheEntry:
        if not isinstance(raw, dict):
            raise TypeError(f"row {key!r} must be a JSON object")
        unknown = set(raw) - set(cls.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {key!r} has unknown keys: {sorted(unknown)}")
        return cls(str(key), raw)

    def validate(self) -> None:
        super().validate()
        # Re-coerce in place so e.g. int prices normalise to float on merge.
        self._data = self._coerce(dict(self._data), key=self._key)

    # -- typed accessors ------------------------------------------------
    @property
    def price(self) -> float | None:
        value = self._data.get(self.PRICE)
        return None if value is None else float(value)

    @price.setter
    def price(self, value: float | None) -> None:
        if value is None:
            self._data.pop(self.PRICE, None)
            return
        self.merge({self.PRICE: value})

    @property
    def countries(self) -> dict[str, float] | None:
        value = self._data.get(self.COUNTRIES)
        return None if value is None else dict(value)

    @countries.setter
    def countries(self, value: dict[str, float] | None) -> None:
        if value is None:
            self._data.pop(self.COUNTRIES, None)
            return
        self.merge({self.COUNTRIES: value})

    @property
    def sectors(self) -> dict[str, float] | None:
        value = self._data.get(self.SECTORS)
        return None if value is None else dict(value)

    @sectors.setter
    def sectors(self, value: dict[str, float] | None) -> None:
        if value is None:
            self._data.pop(self.SECTORS, None)
            return
        self.merge({self.SECTORS: value})

    def parsed(self) -> tuple[float | None, dict[str, float] | None, dict[str, float] | None]:
        """``(price, countries, sectors)``; mirrors ``utils.parse_cache_entry``."""
        return self.price, self.countries, self.sectors

    # -- Position-row converters (weight_pct <-> fraction) --------------
    @staticmethod
    def rows_to_fractions(rows: list[dict[str, Any]] | None) -> dict[str, float]:
        """``[{"name", "weight_pct"}]`` (0-100) → ``{name: fraction}`` (0-1)."""
        if not rows:
            return {}
        return {str(r["name"]): float(r["weight_pct"]) / 100.0 for r in rows}  # type: ignore[index]

    @staticmethod
    def fractions_to_rows(fractions: dict[str, float] | None) -> list[dict[str, Any]] | None:
        """``{name: fraction}`` (0-1) → ``[{"name", "weight_pct"}]`` (0-100)."""
        if fractions is None:
            return None
        return [{"name": name, "weight_pct": float(w) * 100.0} for name, w in fractions.items()]


class AssetBucket(DictRow):
    """One portfolio bucket; serialised as a bare list of position dicts."""

    # No fixed dict keys: the payload *is* the positions list.
    ALLOWED_KEYS: frozenset[str] = frozenset()

    def __init__(self, key: str, positions: list[dict[str, Any]] | None = None) -> None:
        self._positions: list[dict[str, Any]] = []
        super().__init__(key)
        self.set_positions(positions if positions is not None else [])

    @property
    def positions(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._positions]

    def set_positions(self, positions: list[dict[str, Any]]) -> None:
        if not isinstance(positions, list):
            raise TypeError(f"bucket {self._key!r} must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise TypeError(f"{self._key!r}[{i}] must be a JSON object")
        self._positions = [dict(p) for p in positions]

    def to_dict(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._positions]

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> AssetBucket:
        return cls(str(key), raw)  # __init__ validates list-of-dicts

    def merge(self, partial: dict[str, Any]) -> None:
        # Buckets replace wholesale (a "partial position" has no meaning);
        # accept {"positions": [...]} for interface conformance.
        if not isinstance(partial, dict) or set(partial) != {"positions"}:
            raise KeyError(
                f"bucket {self._key!r} merges only {{'positions': [...]}}"
            )
        self.set_positions(partial["positions"])

    def validate(self) -> None:
        # _data is unused for buckets; validate the positions list instead.
        for i, pos in enumerate(self._positions):
            if not isinstance(pos, dict):
                raise TypeError(f"{self._key!r}[{i}] must be a JSON object")


class IsinRecord(DictRow):
    """One allow-list entry: which issuer handler owns an ISIN (+ vendor ref)."""

    ISSUER = "issuer"
    PRODUCT_REF = "product_ref"
    ALLOWED_KEYS: frozenset[str] = frozenset({ISSUER, PRODUCT_REF})

    #: Canonical issuer slugs (module of origin in ``position/``).
    ISSUERS: frozenset[str] = frozenset(
        {"amundi", "ishares", "ssga", "dws", "ubs", "invesco", "landg"}
    )

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        issuer: str | None = None,
        product_ref: str | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if issuer is not None:
            payload[self.ISSUER] = issuer
        if product_ref is not None:
            payload[self.PRODUCT_REF] = product_ref
        super().__init__(key, payload)

    def validate(self) -> None:
        super().validate()
        issuer = self._data.get(self.ISSUER)
        if not isinstance(issuer, str) or issuer not in self.ISSUERS:
            raise ValueError(
                f"row {self._key!r} field 'issuer' must be one of "
                f"{sorted(self.ISSUERS)}, got {issuer!r}"
            )
        ref = self._data.get(self.PRODUCT_REF)
        if ref is not None and not isinstance(ref, str):
            raise TypeError(f"row {self._key!r} field 'product_ref' must be a string or null")

    @property
    def issuer(self) -> str:
        return str(self._data[self.ISSUER])

    @property
    def product_ref(self) -> str | None:
        ref = self._data.get(self.PRODUCT_REF)
        return None if ref is None else str(ref)


#: Snapshot of the ``position/*_position.py`` allow-lists, as
#: ``{ISIN: {"issuer": ..., "product_ref": ...}}`` kwargs for
#: :class:`IsinRecord`. ``product_ref`` is the iShares product id / SSGA
#: slug where one exists, else ``None``. Kept in sync manually until the
#: factory is wired to the registry (pinned follow-up).
DEFAULT_ISIN_RECORDS: dict[str, dict[str, Any]] = {
    # AmundiPosition.ISINS
    "IE000BI8OT95": {"issuer": "amundi", "product_ref": None},
    "LU2233156582": {"issuer": "amundi", "product_ref": None},
    "LU2300294316": {"issuer": "amundi", "product_ref": None},
    # _ISHARES_PRODUCT_IDS (BlackRock allow-list)
    "IE00BKM4GZ66": {"issuer": "ishares", "product_ref": "264659"},
    "IE00BD1F4M44": {"issuer": "ishares", "product_ref": "285207"},
    "IE00BHZPJ239": {"issuer": "ishares", "product_ref": "307659"},
    "IE00BF4RFH31": {"issuer": "ishares", "product_ref": "296576"},
    "IE00BFNM3D14": {"issuer": "ishares", "product_ref": "305363"},
    "IE00BL6K8C82": {"issuer": "ishares", "product_ref": "318925"},
    "IE00BFNM3L97": {"issuer": "ishares", "product_ref": "305412"},
    "IE00BFNM3P36": {"issuer": "ishares", "product_ref": "305397"},
    "IE000APK27S2": {"issuer": "ishares", "product_ref": "320169"},
    "IE00BKPT2S34": {"issuer": "ishares", "product_ref": "313317"},
    # _SSGA_PRODUCT_SLUGS (StateStreet allow-list)
    "IE00B4YBJ215": {
        "issuer": "ssga",
        "product_ref": "state-street-spdr-sp-400-us-mid-cap-ucits-etf-acc-spy4-gy",
    },
    # XtrackersPosition.ISINS
    "IE00BTJRMP35": {"issuer": "dws", "product_ref": None},
    "IE0006WW1TQ4": {"issuer": "dws", "product_ref": None},
    "IE00BLNMYC90": {"issuer": "dws", "product_ref": None},
    # UBSPosition.ISINS
    "IE00BD4TXV59": {"issuer": "ubs", "product_ref": None},
    "IE00BKSCBX74": {"issuer": "ubs", "product_ref": None},
    # InvescoPosition.ISINS
    "IE00BKS7L097": {"issuer": "invesco", "product_ref": None},
    "IE000PJL7R74": {"issuer": "invesco", "product_ref": None},
    # LAndGPosition.ISINS
    "IE000Z9UVQ99": {"issuer": "landg", "product_ref": None},
    "IE00BFXR5W90": {"issuer": "landg", "product_ref": None},
}
