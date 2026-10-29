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
* :class:`PositionRecord` — one position row, keyed by the composite
  ``(username, bucket, position_idx)``.
* :class:`UserPortfolioRecord` — one user's portfolio name.
* :class:`IsinRecord` — one registry row: which issuer handler owns an
  ISIN, which portfolio bucket it lives in, plus an optional vendor
  product ref (Vanguard PortId / iShares product id / SSGA slug).
* :class:`UserRecord` — one user account: the account key *is* the
  user name (the future primary key), the payload holds the password.
* :class:`SessionRecord` — one browser session: the user, the
  incognito state and the expiry.

Nothing here touches I/O; loading leniency (skip bad rows) is a
backend concern, while these classes are strict (bad keys/values raise).
"""

from __future__ import annotations

import math
import time
from typing import Any

from storage.storage import StorageObject

__all__ = [
    "DEFAULT_PORTFOLIO_NAME",
    "DictRow",
    "CacheEntry",
    "AssetBucket",
    "IsinRecord",
    "UserRecord",
    "SessionRecord",
    "PositionRecord",
    "UserPortfolioRecord",
]

#: Name a user's portfolio carries until they rename it.
DEFAULT_PORTFOLIO_NAME = "Portfolio"

# Split fractions are normalized at assembly, but float dust can land a
# hair outside [0, 1] or leave a full split at 0.9999999999999999. Accept
# that dust, clamp it, and snap complete splits (within 1pp) to exactly 1
# so value-weighted portfolio merges do not accumulate drift.
_SPLIT_WEIGHT_TOLERANCE = 1e-9
_SPLIT_TOTAL_TOLERANCE = 0.01


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
        # Split rows are normalized at assembly (see
        # ``position.normalize_split_rows``). Still tolerate tiny float
        # dust at the boundary, then snap near-complete splits to 1 so
        # value-weighted portfolio merges do not accumulate drift.
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
            if (
                not math.isfinite(w)
                or w < -_SPLIT_WEIGHT_TOLERANCE
                or w > 1.0 + _SPLIT_WEIGHT_TOLERANCE
            ):
                raise ValueError(
                    f"row {key!r} field {field!r} weight for {name!r} "
                    f"must be a fraction in [0, 1], got {weight!r}"
                )
            out[str(name)] = min(1.0, max(0.0, w))
        total = math.fsum(out.values())
        if out and total > 0.0 and abs(total - 1.0) <= _SPLIT_TOTAL_TOLERANCE:
            scaled = {name: weight / total for name, weight in out.items()}
            residual = 1.0 - math.fsum(scaled.values())
            if residual:
                largest = max(scaled, key=scaled.get)
                scaled[largest] += residual
            out = scaled
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


class AssetBucket:
    """One portfolio bucket: a list of position dicts.

    In the SQLite backend positions live in the normalized
    ``user_positions`` table. This class is a domain helper for the JSON
    harness and the UI layer; it does not persist directly.
    """

    def __init__(self, key: str, positions: list[dict[str, Any]] | None = None) -> None:
        self._key = str(key)
        self._positions: list[dict[str, Any]] = []
        self.set_positions(positions if positions is not None else [])

    @property
    def key(self) -> str:
        return self._key

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
        if not isinstance(raw, list):
            raise TypeError(f"bucket {key!r} must be a JSON array")
        return cls(str(key), raw)

    def validate(self) -> None:
        for i, pos in enumerate(self._positions):
            if not isinstance(pos, dict):
                raise TypeError(f"{self._key!r}[{i}] must be a JSON object")

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, AssetBucket)
            and self._key == other._key
            and self._positions == other._positions
        )

    def __repr__(self) -> str:
        return f"AssetBucket(key={self._key!r}, positions={self._positions!r})"


class IsinRecord(DictRow):
    """One registry row: issuer handler, portfolio bucket, vendor ref.

    The key is the ISIN itself (non-negotiable: future primary key).
    ``issuer`` is a slug from :attr:`ISSUERS` (``justetf``/``yfinance``
    mark decided-generic rows); ``bucket`` is a portfolio bucket key;
    ``product_ref`` is the Vanguard PortId / iShares product id / SSGA
    slug where one exists. ``visits`` counts maintenance-script scans.
    The first three fields are nullable; ``visits`` is a non-negative
    integer.
    """

    ISSUER = "issuer"
    BUCKET = "bucket"
    PRODUCT_REF = "product_ref"
    VISITS = "visits"
    ALLOWED_KEYS: frozenset[str] = frozenset(
        {ISSUER, BUCKET, PRODUCT_REF, VISITS}
    )

    #: Canonical issuer slugs (module of origin in ``position/``),
    #: plus the generic fallbacks.
    ISSUERS: frozenset[str] = frozenset(
        {
            "amundi",
            "ishares",
            "ssga",
            "dws",
            "ubs",
            "invesco",
            "landg",
            "vanguard",
            "justetf",
            "yfinance",
        }
    )

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        issuer: str | None = None,
        bucket: str | None = None,
        product_ref: str | None = None,
        visits: int | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if issuer is not None:
            payload[self.ISSUER] = issuer
        if bucket is not None:
            payload[self.BUCKET] = bucket
        if product_ref is not None:
            payload[self.PRODUCT_REF] = product_ref
        if visits is not None:
            payload[self.VISITS] = visits
        payload.setdefault(self.VISITS, 0)
        if payload[self.VISITS] is None:
            payload[self.VISITS] = 0
        super().__init__(key, payload)

    def validate(self) -> None:
        super().validate()
        if not str(self._key).strip():
            raise ValueError("registry row key (ISIN) must be a non-empty string")
        issuer = self._data.get(self.ISSUER)
        if issuer is not None and (
            not isinstance(issuer, str) or issuer not in self.ISSUERS
        ):
            raise ValueError(
                f"row {self._key!r} field 'issuer' must be one of "
                f"{sorted(self.ISSUERS)} or null, got {issuer!r}"
            )
        bucket = self._data.get(self.BUCKET)
        if bucket is not None and not isinstance(bucket, str):
            raise TypeError(
                f"row {self._key!r} field 'bucket' must be a string or null"
            )
        ref = self._data.get(self.PRODUCT_REF)
        if ref is not None and not isinstance(ref, str):
            raise TypeError(f"row {self._key!r} field 'product_ref' must be a string or null")
        visits = self._data.get(self.VISITS)
        if isinstance(visits, bool) or not isinstance(visits, int) or visits < 0:
            raise ValueError(
                f"row {self._key!r} field 'visits' must be a non-negative "
                f"integer, got {visits!r}"
            )

    @property
    def issuer(self) -> str | None:
        issuer = self._data.get(self.ISSUER)
        return None if issuer is None else str(issuer)

    @property
    def bucket(self) -> str | None:
        bucket = self._data.get(self.BUCKET)
        return None if bucket is None else str(bucket)

    @property
    def product_ref(self) -> str | None:
        ref = self._data.get(self.PRODUCT_REF)
        return None if ref is None else str(ref)

    @property
    def visits(self) -> int:
        return int(self._data.get(self.VISITS, 0))


class UserRecord(DictRow):
    """One user account: the password (the key is the user name).

    The key is the user name — non-negotiable, it is the primary key a
    database table would carry. The payload holds only the password;
    an empty password is allowed (the account exists, it just has no
    secret yet).

    The password is stored as given (plaintext): password *login* is
    not implemented yet, and hashing belongs here when it is — this
    class is the single place that would change.
    """

    PASSWORD = "password"
    ALLOWED_KEYS: frozenset[str] = frozenset({PASSWORD})

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        password: str | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if password is not None:
            payload[self.PASSWORD] = password
        super().__init__(key, payload)

    def validate(self) -> None:
        super().validate()
        if not str(self._key).strip():
            raise ValueError("user key (the user name) must be a non-empty string")
        password = self._data.get(self.PASSWORD)
        if password is not None and not isinstance(password, str):
            raise TypeError(
                f"row {self._key!r} field 'password' must be a string"
            )

    @property
    def user(self) -> str:
        """The user name (the row key)."""
        return str(self._key)

    @property
    def password(self) -> str:
        """The account password ("" when the account has none)."""
        value = self._data.get(self.PASSWORD)
        return "" if value is None else str(value)

    @password.setter
    def password(self, value: str) -> None:
        self.merge({self.PASSWORD: str(value)})

    def to_dict(self) -> dict[str, Any]:
        # The user name stays in the key; the payload is the password.
        return {self.PASSWORD: self.password}


class SessionRecord(DictRow):
    """One browser session: the user, the incognito state, the expiry.

    The key is the session id (the value of the ``asalloc_sid``
    cookie); the payload carries who is logged in, the incognito state
    they last chose, and when the session dies (``expires_at``, a
    POSIX timestamp).

    Every field is optional: a half-written row (a session that was
    only started) is valid, the repository fills the rest.
    """

    USER = "user"
    INCOGNITO = "incognito"
    EXPIRES_AT = "expires_at"
    ALLOWED_KEYS: frozenset[str] = frozenset({USER, INCOGNITO, EXPIRES_AT})

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        user: str | None = None,
        incognito: bool | None = None,
        expires_at: float | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if user is not None:
            payload[self.USER] = user
        if incognito is not None:
            payload[self.INCOGNITO] = incognito
        if expires_at is not None:
            payload[self.EXPIRES_AT] = expires_at
        super().__init__(key, payload)

    def validate(self) -> None:
        super().validate()
        user = self._data.get(self.USER)
        if user is not None and not isinstance(user, str):
            raise TypeError(f"row {self._key!r} field 'user' must be a string")
        incognito = self._data.get(self.INCOGNITO)
        if incognito is not None and not isinstance(incognito, bool):
            raise TypeError(
                f"row {self._key!r} field 'incognito' must be a boolean"
            )
        expires_at = self._data.get(self.EXPIRES_AT)
        if expires_at is not None:
            if isinstance(expires_at, bool) or not isinstance(
                expires_at, (int, float)
            ):
                raise TypeError(
                    f"row {self._key!r} field 'expires_at' must be a number"
                )
            if not math.isfinite(float(expires_at)):
                raise ValueError(
                    f"row {self._key!r} field 'expires_at' must be finite"
                )

    @property
    def user(self) -> str:
        """The logged-in user ("" when the row carries none)."""
        value = self._data.get(self.USER)
        return "" if value is None else str(value)

    @property
    def incognito(self) -> bool:
        """Whether the user chose incognito mode."""
        return bool(self._data.get(self.INCOGNITO))

    @property
    def expires_at(self) -> float:
        """When the session dies (0.0 when it never expires)."""
        value = self._data.get(self.EXPIRES_AT)
        return 0.0 if value is None else float(value)

    def expired(self, now: float | None = None) -> bool:
        """True when the session is past its expiry.

        A row without an expiry counts as expired: the timeout is
        always set, so such a row predates it and must not linger.
        """
        expires_at = self.expires_at
        if expires_at <= 0.0:
            return True
        return expires_at <= (now if now is not None else time.time())


class PositionRecord(StorageObject):
    """One position row keyed by ``(username, bucket, position_idx)``.

    The SQLite table carries that composite key as its primary key; the
    string key encodes the three parts so the generic (string-keyed)
    :class:`~storage.storage.Storage` interface can address one row.
    """

    USERNAME = "username"
    BUCKET = "bucket"
    POSITION_IDX = "position_idx"
    NAME = "name"
    SHORT_NAME = "short_name"
    ISIN = "isin"
    VALUE = "value"
    SHARES = "shares"
    BROKER = "broker"
    DMEM = "dmem"
    DMEM_OTHER = "dmem_other"
    USAVN = "usavn"
    KEY_SEP = "\x1f"
    ALLOWED_KEYS: frozenset[str] = frozenset({
        USERNAME, BUCKET, POSITION_IDX, NAME, SHORT_NAME,
        ISIN, VALUE, SHARES, BROKER, DMEM, DMEM_OTHER, USAVN,
    })

    @classmethod
    def encode_key(cls, username: str, bucket: str, position_idx: int) -> str:
        """Encode the composite primary key as one string."""
        return f"{username}{cls.KEY_SEP}{bucket}{cls.KEY_SEP}{int(position_idx)}"

    @classmethod
    def decode_key(cls, key: str) -> tuple[str, str, int]:
        """Split a key produced by :meth:`encode_key`."""
        parts = str(key).split(cls.KEY_SEP)
        if len(parts) != 3:
            raise ValueError(
                f"position key must be username/bucket/index, got {key!r}"
            )
        try:
            index = int(parts[2])
        except ValueError as exc:
            raise ValueError(
                f"position key has a non-numeric index: {key!r}"
            ) from exc
        return parts[0], parts[1], index

    @classmethod
    def _normalize(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Accept legacy field spellings (``position_index``/``ISIN``/``group``)."""
        data = dict(raw)
        if cls.POSITION_IDX not in data and "position_index" in data:
            data[cls.POSITION_IDX] = data.pop("position_index")
        if cls.ISIN not in data and "ISIN" in data:
            data[cls.ISIN] = data.pop("ISIN")
        if cls.SHORT_NAME not in data and "group" in data:
            data[cls.SHORT_NAME] = data.pop("group")
        return data

    def __init__(
        self,
        key: str | None = None,
        data: dict[str, Any] | None = None,
        *,
        username: str | None = None,
        bucket: str | None = None,
        position_idx: int | None = None,
        position_index: int | None = None,
        name: str | None = None,
        short_name: str | None = None,
        isin: str | None = None,
        value: float | None = None,
        shares: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        dmem_other: float | None = None,
        usavn: float | None = None,
    ) -> None:
        payload: dict[str, Any] = self._normalize(data) if data else {}
        if position_index is not None and position_idx is None:
            position_idx = position_index
        if username is not None:
            payload[self.USERNAME] = username
        if bucket is not None:
            payload[self.BUCKET] = bucket
        if position_idx is not None:
            payload[self.POSITION_IDX] = position_idx
        if name is not None:
            payload[self.NAME] = name
        if short_name is not None:
            payload[self.SHORT_NAME] = short_name
        if isin is not None:
            payload[self.ISIN] = isin
        if value is not None:
            payload[self.VALUE] = value
        if shares is not None:
            payload[self.SHARES] = shares
        if broker is not None:
            payload[self.BROKER] = broker
        if dmem is not None:
            payload[self.DMEM] = dmem
        if dmem_other is not None:
            payload[self.DMEM_OTHER] = dmem_other
        if usavn is not None:
            payload[self.USAVN] = usavn
        # "0" was the old "let the DB assign an id" placeholder; with the
        # composite key it simply means "derive it from the fields".
        if key is None or str(key) in ("", "0"):
            key = self.encode_key(
                str(payload.get(self.USERNAME, "")),
                str(payload.get(self.BUCKET, "")),
                int(payload.get(self.POSITION_IDX, 0)),
            )
        self._key = str(key)
        self._data = payload
        self.validate()

    def validate(self) -> None:
        unknown = set(self._data) - set(self.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {self._key!r} has unknown keys: {sorted(unknown)}")
        username = self._data.get(self.USERNAME)
        if not isinstance(username, str) or not username.strip():
            raise ValueError(f"row {self._key!r} field 'username' must be a non-empty string")
        bucket = self._data.get(self.BUCKET)
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError(f"row {self._key!r} field 'bucket' must be a non-empty string")
        position_idx = self._data.get(self.POSITION_IDX)
        if isinstance(position_idx, bool) or not isinstance(position_idx, int):
            raise TypeError(f"row {self._key!r} field 'position_idx' must be an integer")
        if position_idx < 0:
            raise ValueError(f"row {self._key!r} field 'position_idx' must be >= 0")
        name = self._data.get(self.NAME)
        if name is not None and not isinstance(name, str):
            raise TypeError(f"row {self._key!r} field 'name' must be a string")
        short_name = self._data.get(self.SHORT_NAME)
        if short_name is not None and not isinstance(short_name, str):
            raise TypeError(f"row {self._key!r} field 'short_name' must be a string")
        isin = self._data.get(self.ISIN)
        if isin is not None and not isinstance(isin, str):
            raise TypeError(f"row {self._key!r} field 'isin' must be a string")
        for field in (self.VALUE, self.SHARES, self.DMEM, self.DMEM_OTHER, self.USAVN):
            value = self._data.get(field)
            if value is not None:
                try:
                    float(value)
                except (TypeError, ValueError):
                    raise TypeError(f"row {self._key!r} field {field!r} must be a number")
        broker = self._data.get(self.BROKER)
        if broker is not None and not isinstance(broker, str):
            raise TypeError(f"row {self._key!r} field 'broker' must be a string")

    @property
    def key(self) -> str:
        """The composite key, derived from the fields (never stale)."""
        return self.encode_key(self.username, self.bucket, self.position_idx)

    @property
    def username(self) -> str:
        value = self._data.get(self.USERNAME)
        return "" if value is None else str(value)

    @property
    def bucket(self) -> str:
        value = self._data.get(self.BUCKET)
        return "" if value is None else str(value)

    @property
    def position_idx(self) -> int:
        value = self._data.get(self.POSITION_IDX)
        return 0 if value is None else int(value)

    @property
    def position_index(self) -> int:
        """Alias for :attr:`position_idx` (app-facing name)."""
        return self.position_idx

    @property
    def name(self) -> str:
        value = self._data.get(self.NAME)
        return "" if value is None else str(value)

    @property
    def short_name(self) -> str:
        value = self._data.get(self.SHORT_NAME)
        return "" if value is None else str(value)

    @property
    def isin(self) -> str | None:
        value = self._data.get(self.ISIN)
        return None if value is None else str(value)

    @property
    def value(self) -> float | None:
        value = self._data.get(self.VALUE)
        return None if value is None else float(value)

    @property
    def shares(self) -> float | None:
        value = self._data.get(self.SHARES)
        return None if value is None else float(value)

    @property
    def broker(self) -> str | None:
        value = self._data.get(self.BROKER)
        return None if value is None else str(value)

    @property
    def dmem(self) -> float | None:
        value = self._data.get(self.DMEM)
        return None if value is None else float(value)

    @property
    def dmem_other(self) -> float | None:
        value = self._data.get(self.DMEM_OTHER)
        return None if value is None else float(value)

    @property
    def usavn(self) -> float | None:
        value = self._data.get(self.USAVN)
        return None if value is None else float(value)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def merge(self, partial: dict[str, Any]) -> None:
        if not isinstance(partial, dict):
            raise TypeError(f"partial update for {self._key!r} must be a dict")
        normalized = self._normalize(partial)
        unknown = set(normalized) - set(self.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {self._key!r} has unknown keys: {sorted(unknown)}")
        self._data.update(normalized)
        self.validate()

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> PositionRecord:
        if not isinstance(raw, dict):
            raise TypeError(f"row {key!r} must be a JSON object, got {type(raw).__name__}")
        data = cls._normalize(raw)
        unknown = set(data) - set(cls.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {key!r} has unknown keys: {sorted(unknown)}")
        return cls(str(key), data)


class UserPortfolioRecord(DictRow):
    """One user's portfolio metadata (name only, positions are in separate table).

    Keyed by username; the payload holds just the portfolio name.
    """

    USERNAME = "username"
    PORTFOLIO_NAME = "portfolio_name"
    ALLOWED_KEYS: frozenset[str] = frozenset({PORTFOLIO_NAME})

    def __init__(
        self,
        key: str,
        data: dict[str, Any] | None = None,
        *,
        portfolio_name: str | None = None,
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if portfolio_name is not None:
            payload[self.PORTFOLIO_NAME] = portfolio_name
        super().__init__(key, payload)

    def validate(self) -> None:
        super().validate()
        if not str(self._key).strip():
            raise ValueError("user name (username) must be a non-empty string")
        portfolio_name = self._data.get(self.PORTFOLIO_NAME)
        if portfolio_name is not None and not isinstance(portfolio_name, str):
            raise TypeError(
                f"row {self._key!r} field 'portfolio_name' must be a string"
            )

    @property
    def username(self) -> str:
        return str(self._key)

    @property
    def portfolio_name(self) -> str:
        value = self._data.get(self.PORTFOLIO_NAME)
        return DEFAULT_PORTFOLIO_NAME if value is None else str(value)

    @portfolio_name.setter
    def portfolio_name(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"row {self._key!r} field 'portfolio_name' must be a string")
        self._data[self.PORTFOLIO_NAME] = value

    def to_dict(self) -> dict[str, Any]:
        return {self.PORTFOLIO_NAME: self.portfolio_name}

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> UserPortfolioRecord:
        if not isinstance(raw, dict):
            raise TypeError(f"row {key!r} must be a JSON object, got {type(raw).__name__}")
        unknown = set(raw) - set(cls.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {key!r} has unknown keys: {sorted(unknown)}")
        return cls(str(key), dict(raw))

    @property
    def key(self) -> str:
        return str(self._key)

    def merge(self, partial: dict[str, Any]) -> None:
        if not isinstance(partial, dict):
            raise TypeError(f"partial update for {self._key!r} must be a dict")
        unknown = set(partial) - set(self.ALLOWED_KEYS)
        if unknown:
            raise KeyError(f"row {self._key!r} has unknown keys: {sorted(unknown)}")
        self._data.update(partial)
        self.validate()
