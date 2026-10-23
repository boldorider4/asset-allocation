# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the SQLite database lives, and the rules for it.

The runtime keeps every store (assets, cache, ISIN registry, users and
sessions) in one SQLite database. Its location is either ``--db-file``
or the ``[server] db_file`` ini key. The built-in default does *not*
count: it points into the repository rather than into the deployment,
so a database that is only "configured" by a default has no location.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "StoreLocationMissing",
    "StoreSpec",
    "DB_SPEC",
    "db_file",
]


@dataclass(frozen=True)
class StoreSpec:
    """The database store: its ini key and command-line flag."""

    name: str
    ini_section: str
    ini_option: str
    flag: str

    @property
    def ini_key(self) -> str:
        return f"[{self.ini_section}] {self.ini_option}"


#: The SQLite database: one file, one table per store.
DB_SPEC = StoreSpec("database", "server", "db_file", "--db-file")


class StoreLocationMissing(RuntimeError):
    """The database has no location: neither a flag nor an ini key names it."""

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


def db_file(
    *,
    ini_path: Path | None = None,
    flag_value: str | None = None,
    required: bool = True,
) -> Path | None:
    """The SQLite database file (mandatory unless ``required`` is false)."""
    if flag_value:
        return Path(flag_value).expanduser()
    value = _ini_path(DB_SPEC.ini_section, DB_SPEC.ini_option, ini_path)
    if value is None:
        if required:
            raise StoreLocationMissing(DB_SPEC)
        return None
    return value
