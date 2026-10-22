# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the stores live, and the rules for it.

Every store (the assets, the cache, the ISIN registry, the users) lives
either in **one SQLite database** (``--db-file``) or in **its own JSON
file** (``--assets-file`` and friends). The backend named in
``config.ini`` decides which of the two is in play.

The location of a store is either its command-line flag or the ini key
that names it. The built-in defaults do *not* count: they point into
the repository rather than into the deployment, so a store that is only
"configured" by a default is a store with no location.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from storage.wiring import BACKEND_SQLITE

__all__ = [
    "StoreLocationMissing",
    "StoreSpec",
    "STORES",
    "db_file",
    "required_stores",
    "resolve_store_locations",
]


@dataclass(frozen=True)
class StoreSpec:
    """One store: the ini key and the flag that locate it."""

    name: str
    ini_section: str
    ini_option: str
    flag: str

    @property
    def ini_key(self) -> str:
        return f"[{self.ini_section}] {self.ini_option}"


#: The stores, with the ini key and the CLI flag that locate them.
STORES: dict[str, StoreSpec] = {
    "assets": StoreSpec("assets", "update", "assets_file", "--assets-file"),
    "cache": StoreSpec("cache", "update", "cache_file", "--cache-file"),
    "users": StoreSpec("users", "update", "users_file", "--users-file"),
    "isin": StoreSpec("isin", "update", "isin_file", "--isin-file"),
}

#: The stores each command needs, per backend. The sqlite backend
#: keeps every store in the one database, so it needs no per-store
#: location at all.
REQUIRED: dict[str, dict[str, tuple[str, ...]]] = {
    "json": {
        "serve": ("assets", "cache", "users"),
        "update": ("assets", "cache", "isin"),
    },
    "sqlite": {"serve": (), "update": ()},
}

#: The SQLite database: one file, one table per store.
DB_SPEC = StoreSpec("database", "server", "db_file", "--db-file")


class StoreLocationMissing(RuntimeError):
    """A store has no location: neither a flag nor an ini key names it."""

    def __init__(self, spec: StoreSpec) -> None:
        super().__init__(
            f"the {spec.name} store has no location: pass {spec.flag} or set "
            f"{spec.ini_key} in config.ini"
        )
        self.spec = spec


def _ini_value(
    section: str, option: str, ini_path: Path | None
) -> str | None:
    """The raw value of an ini key, or None when the ini does not set it."""
    try:
        parser = configparser.ConfigParser()
        path = Path(ini_path) if ini_path else None
        if path is None:
            from cli.context import AppConfig

            path = AppConfig.config_path(None)
        if not path.is_file():
            return None
        parser.read(path, encoding="utf-8")
    except Exception:
        return None
    if not parser.has_option(section, option):
        return None
    raw = parser.get(section, option, fallback=None)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _ini_path(
    section: str, option: str, ini_path: Path | None
) -> Path | None:
    """The ini value of a path key, resolved against the ini's folder."""
    raw = _ini_value(section, option, ini_path)
    if raw is None:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = Path(ini_path) if ini_path else None
        if base is None:
            from cli.context import AppConfig

            base = AppConfig.config_path(None)
        path = (base.parent / path).resolve()
    return path


def required_stores(backend: str, command: str) -> tuple[str, ...]:
    """The stores ``command`` needs with ``backend``."""
    return REQUIRED.get(backend, REQUIRED["json"]).get(command, ())


def resolve_store_locations(
    backend: str,
    command: str,
    *,
    ini_path: Path | None = None,
    flags: dict[str, str | None] | None = None,
) -> dict[str, Path]:
    """Where the stores of ``command`` live; raises when one has no home.

    The location of a store is its command-line flag or the ini key
    that names it. The built-in defaults do not count.
    """
    flags = flags or {}
    located: dict[str, Path] = {}
    for store in required_stores(backend, command):
        spec = STORES[store]
        raw = flags.get(spec.flag.lstrip("-").replace("-", "_"))
        if raw:
            located[store] = Path(raw).expanduser()
            continue
        value = _ini_path(spec.ini_section, spec.ini_option, ini_path)
        if value is not None:
            located[store] = value
            continue
        raise StoreLocationMissing(spec)
    return located


def db_file(
    backend: str,
    *,
    ini_path: Path | None = None,
    flag_value: str | None = None,
    required: bool = True,
) -> Path | None:
    """The SQLite database file (mandatory for the sqlite backend)."""
    if backend != BACKEND_SQLITE:
        return None
    if flag_value:
        return Path(flag_value).expanduser()
    value = _ini_path(DB_SPEC.ini_section, DB_SPEC.ini_option, ini_path)
    if value is None:
        if required:
            raise StoreLocationMissing(DB_SPEC)
        return None
    return value