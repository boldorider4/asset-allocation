# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test helper: read/write one user's portfolio through the repository.

The assets store is keyed by user and reached through
``storage.repositories.AssetRepository`` over the backend chosen in
``storage.wiring`` — the same path production uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.repositories import AssetRepository
from storage.wiring import assets_backend

__all__ = ["read_user_buckets", "write_user_buckets"]


def read_user_buckets(
    path: Path, user: str = "default"
) -> dict[str, list[dict[str, Any]]]:
    """One user's buckets as stored."""
    # Backend pinned to json: a local config.ini must not change what
    # the tests use.
    repo = AssetRepository(user, assets_backend(path, "json"))
    repo.open()
    return repo.snapshot()


def write_user_buckets(
    path: Path, data: dict[str, list[dict[str, Any]]], user: str = "default"
) -> None:
    """Replace one user's buckets (other users are preserved)."""
    repo = AssetRepository(user, assets_backend(path, "json"))
    repo.open()
    repo.restore(data)
    repo.persist()
