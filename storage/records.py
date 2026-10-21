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
* :class:`UserAssets` — one user's portfolio: the name of the portfolio
  plus its positions, grouped by asset class.
* :class:`IsinRecord` — one registry row: which issuer handler owns an
  ISIN, which portfolio bucket it lives in, plus an optional vendor
  product ref (iShares product id / SSGA slug).
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
    "UserAssets",
    "IsinRecord",
    "UserRecord",
    "SessionRecord",
]

#: Name a user's portfolio carries until they rename it.
DEFAULT_PORTFOLIO_NAME = "Portfolio"


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


class UserAssets(StorageObject):
    """One user's portfolio: a name and its positions.

    Keyed by user name; the payload is that user's portfolio::

        {"portfolio_name": "Portfolio", "positions": {"<class>": [rows]}}

    The positions are grouped by asset class (the keys the composition
    and the web views use) and validated through :class:`AssetBucket`,
    keeping the per-class rules in one place.

    The payload is strict: unknown keys raise, so a file in another
    shape fails loudly instead of silently loading as empty.
    """

    PORTFOLIO_NAME = "portfolio_name"
    POSITIONS = "positions"
    ALLOWED_KEYS: frozenset[str] = frozenset({PORTFOLIO_NAME, POSITIONS})

    def __init__(
        self, key: str, data: dict[str, Any] | None = None
    ) -> None:
        self._key = str(key)
        # _coerce defines what a user payload may be (None = a user listed
        # without any assets yet); one place, so every path agrees.
        self._portfolio_name, self._positions = self._coerce(data, key=self._key)
        self.validate()

    @staticmethod
    def _coerce_positions(
        data: Any, *, key: str
    ) -> dict[str, list[dict[str, Any]]]:
        """The position map: ``{asset class: [rows]}`` (validated)."""
        if not isinstance(data, dict):
            raise TypeError(f"user {key!r} field 'positions' must be a JSON object")
        out: dict[str, list[dict[str, Any]]] = {}
        for asset_class, positions in data.items():
            row = AssetBucket.from_dict(str(asset_class), positions)
            out[str(asset_class)] = row.positions
        return out

    @staticmethod
    def _coerce(data: Any, *, key: str) -> tuple[str, dict[str, list[dict[str, Any]]]]:
        if data is None:
            # A user listed without any assets yet: an empty portfolio.
            return DEFAULT_PORTFOLIO_NAME, {}
        if isinstance(data, list):
            # A pre-user file stored the buckets directly at the top
            # level, so a list payload means the file predates users.
            raise ValueError(
                f"row {key!r} holds a bucket list, not a user portfolio; "
                "migrate the file once with: python "
                "scripts/migrate_assets_user.py <assets.json> <user>"
            )
        if not isinstance(data, dict):
            raise TypeError(f"user {key!r} assets must be a JSON object")
        unknown = set(data) - set(UserAssets.ALLOWED_KEYS)
        if unknown:
            raise KeyError(
                f"user {key!r} assets has unknown keys: {sorted(unknown)}"
            )
        name = data.get(UserAssets.PORTFOLIO_NAME)
        if name is None:
            name = DEFAULT_PORTFOLIO_NAME
        if not isinstance(name, str):
            raise TypeError(
                f"user {key!r} field 'portfolio_name' must be a string"
            )
        positions = data.get(UserAssets.POSITIONS)
        if positions is None:
            positions = {}
        return name, UserAssets._coerce_positions(positions, key=key)

    @property
    def key(self) -> str:
        return self._key

    @property
    def portfolio_name(self) -> str:
        """The name of the user's portfolio."""
        return self._portfolio_name

    def set_portfolio_name(self, name: str) -> None:
        """Rename the portfolio."""
        if not isinstance(name, str):
            raise TypeError(
                f"user {self._key!r} field 'portfolio_name' must be a string"
            )
        self._portfolio_name = name

    def positions(self) -> dict[str, list[dict[str, Any]]]:
        """The user's positions as plain ``{asset class: [rows]}``."""
        return {
            asset_class: [dict(p) for p in rows]
            for asset_class, rows in self._positions.items()
        }

    def set_positions(
        self, asset_class: str, positions: list[dict[str, Any]]
    ) -> None:
        """Replace one asset class's positions wholesale (validated)."""
        row = AssetBucket.from_dict(str(asset_class), positions)
        self._positions[str(asset_class)] = row.positions

    def remove_positions(self, asset_class: str) -> bool:
        """Drop one asset class; True when anything was removed."""
        return self._positions.pop(str(asset_class), None) is not None

    def replace_all(
        self, positions: dict[str, Any], *, portfolio_name: str | None = None
    ) -> None:
        """Replace the whole position map (the user's complete portfolio).

        The portfolio name is kept unless ``portfolio_name`` is given.
        """
        self._positions = self._coerce_positions(positions, key=self._key)
        if portfolio_name is not None:
            self.set_portfolio_name(portfolio_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            self.PORTFOLIO_NAME: self._portfolio_name,
            self.POSITIONS: self.positions(),
        }

    @classmethod
    def from_dict(cls, key: str, raw: Any) -> UserAssets:
        return cls(str(key), raw)

    def merge(self, partial: dict[str, Any]) -> None:
        """Apply a partial update to the portfolio.

        ``"positions"`` replaces the asset classes it names (the others
        are kept), ``"portfolio_name"`` renames the portfolio. A bucket
        is replaced wholesale, never merged row by row.
        """
        if not isinstance(partial, dict):
            raise TypeError(
                f"partial update for {self._key!r} must be a dict"
            )
        unknown = set(partial) - set(self.ALLOWED_KEYS)
        if unknown:
            raise KeyError(
                f"partial update for {self._key!r} has unknown keys: "
                f"{sorted(unknown)}"
            )
        old_name, old_positions = self._portfolio_name, self._positions
        try:
            if self.PORTFOLIO_NAME in partial:
                self.set_portfolio_name(partial[self.PORTFOLIO_NAME])
            if self.POSITIONS in partial:
                coerced = self._coerce_positions(partial[self.POSITIONS], key=self._key)
                self._positions.update(coerced)
        except Exception:
            self._portfolio_name, self._positions = old_name, old_positions
            raise

    def validate(self) -> None:
        if not self._key.strip():
            raise ValueError("user key must be a non-empty string")
        if not isinstance(self._portfolio_name, str):
            raise TypeError(
                f"user {self._key!r} field 'portfolio_name' must be a string"
            )
        for asset_class, positions in self._positions.items():
            AssetBucket.from_dict(asset_class, positions)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, UserAssets)
            and other._key == self._key
            and other._portfolio_name == self._portfolio_name
            and other._positions == self._positions
        )

    def __repr__(self) -> str:
        return (
            f"UserAssets(key={self._key!r}, "
            f"portfolio_name={self._portfolio_name!r}, "
            f"positions={sorted(self._positions)!r})"
        )


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
    """One registry row: issuer handler, portfolio bucket, vendor ref.

    The key is the ISIN itself (non-negotiable: future primary key).
    ``issuer`` is a slug from :attr:`ISSUERS` (``justetf``/``yfinance``
    mark decided-generic rows); ``bucket`` is a portfolio bucket key;
    ``product_ref`` is the iShares product id / SSGA slug where one
    exists. All three fields are nullable.
    """

    ISSUER = "issuer"
    BUCKET = "bucket"
    PRODUCT_REF = "product_ref"
    ALLOWED_KEYS: frozenset[str] = frozenset({ISSUER, BUCKET, PRODUCT_REF})

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
    ) -> None:
        payload: dict[str, Any] = dict(data) if data else {}
        if issuer is not None:
            payload[self.ISSUER] = issuer
        if bucket is not None:
            payload[self.BUCKET] = bucket
        if product_ref is not None:
            payload[self.PRODUCT_REF] = product_ref
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
