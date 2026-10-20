# SPDX-License-Identifier: AGPL-3.0-or-later
"""File shape of the user-keyed assets file.

The assets file is a JSON object keyed by **user**::

    {
      "default": {
        "equity_portfolio": [ ...positions... ],
        "bond_portfolio": [ ...positions... ]
      }
    }

This module owns that shape: strict reading, atomic writing, and the
detection of the legacy flat format (a single portfolio without a user
key). Legacy files are *rejected* with a pointer to the one-time
migration script (``scripts/migrate_assets_user.py``) — the shape
transition is always deliberate, never implicit.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_USER_NAME",
    "LegacyAssetsFormat",
    "UnknownUser",
    "is_legacy_root",
    "read_assets_root",
    "require_user",
    "user_buckets",
    "write_assets_root",
]

logger = logging.getLogger(__name__)

#: User name used when nothing else is configured.
DEFAULT_USER_NAME = "default"

_MIGRATION_HINT = (
    "migrate it once with: python scripts/migrate_assets_user.py "
    "<assets.json> <user>"
)


class LegacyAssetsFormat(ValueError):
    """The assets file still uses the pre-user flat format."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(
            f"assets file {path} uses the legacy flat format (no user key); "
            f"{_MIGRATION_HINT}"
        )
        self.path = Path(path)


class UnknownUser(LookupError):
    """The requested user has no portfolio in the assets file."""

    def __init__(self, user: str) -> None:
        super().__init__(f"unknown user {user!r}: no portfolio stored for it")
        self.user = str(user)


def is_legacy_root(raw: Any) -> bool:
    """True when ``raw`` is a pre-user flat portfolio (``{bucket: [...]}``)."""
    if not isinstance(raw, dict) or not raw:
        return False
    return all(isinstance(value, list) for value in raw.values())


def _validate_buckets(buckets: Any, *, where: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(buckets, dict):
        raise ValueError(f"{where} must be a JSON object")
    out: dict[str, list[dict[str, Any]]] = {}
    for bucket, positions in buckets.items():
        if not isinstance(positions, list):
            raise ValueError(f"{where}[{bucket!r}] must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise ValueError(f"{where}[{bucket!r}][{i}] must be a JSON object")
        out[str(bucket)] = positions
    return out


def read_assets_root(path: str | Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Read the whole assets file as ``{user: {bucket: [positions]}}``.

    Strict: a malformed file raises instead of silently loading partial
    data (the pipeline relies on that). Legacy flat files raise
    :class:`LegacyAssetsFormat`.
    """
    target = Path(path)
    with target.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("assets root must be a JSON object")
    if is_legacy_root(raw):
        raise LegacyAssetsFormat(target)
    users: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for user, buckets in raw.items():
        users[str(user)] = _validate_buckets(
            buckets, where=f"assets[{user!r}]"
        )
    return users


def user_buckets(
    root: dict[str, dict[str, list[dict[str, Any]]]], user: str
) -> dict[str, list[dict[str, Any]]]:
    """One user's buckets from a root map (empty when the user is new)."""
    return root.get(str(user), {})


def require_user(
    root: dict[str, dict[str, list[dict[str, Any]]]], user: str
) -> dict[str, list[dict[str, Any]]]:
    """One user's buckets, or :class:`UnknownUser` when there are none."""
    try:
        return root[str(user)]
    except KeyError:
        raise UnknownUser(user) from None


def write_assets_root(
    path: str | Path, users: dict[str, dict[str, list[dict[str, Any]]]]
) -> None:
    """Write the whole assets file atomically (temp file + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, target)
