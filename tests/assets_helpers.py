# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test helper: read/write one user's portfolio through the repository.

The assets store is keyed by user and reached through
``storage.repositories.AssetRepository`` over the backend chosen in
``storage.wiring`` — the same path production uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.records import DEFAULT_PORTFOLIO_NAME
from storage.repositories import AssetRepository
from storage.wiring import assets_backend

__all__ = ["read_user_buckets", "write_user_buckets", "user_payload"]


def user_payload(
    positions: dict[str, list[dict[str, Any]]],
    *,
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> dict[str, Any]:
    """A user row in the current assets format.

    The store holds ``{user: {"portfolio_name": ..., "positions": {...}}}``;
    this builds the payload of one user from its positions.
    """
    return {
        "portfolio_name": portfolio_name,
        "positions": positions,
    }


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
