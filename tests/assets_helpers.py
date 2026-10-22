# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test helper: read/write one user's portfolio in the legacy JSON store.

The production path is the normalized ``user_positions`` table. These
helpers stay JSON-only because most fixtures write an ``assets.json``
document; the JSON backend is kept as a harness for a later
compatibility pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.records import DEFAULT_PORTFOLIO_NAME, AssetBucket

__all__ = ["read_user_buckets", "write_user_buckets", "user_payload"]


def user_payload(
    positions: dict[str, list[dict[str, Any]]],
    *,
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> dict[str, Any]:
    """A user row in the legacy assets format."""
    return {
        "portfolio_name": portfolio_name,
        "positions": positions,
    }


def _read_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return raw


def read_user_buckets(
    path: Path, user: str = "default"
) -> dict[str, list[dict[str, Any]]]:
    """One user's buckets as stored."""
    raw = _read_document(path)
    user_data = raw.get(user)
    if not isinstance(user_data, dict):
        return {}
    positions = user_data.get("positions")
    if not isinstance(positions, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for bucket, rows in positions.items():
        out[str(bucket)] = AssetBucket.from_dict(str(bucket), rows).positions
    return out


def write_user_buckets(
    path: Path, data: dict[str, list[dict[str, Any]]], user: str = "default"
) -> None:
    """Replace one user's buckets (other users are preserved)."""
    raw = _read_document(path)
    existing = raw.get(user)
    name = (
        existing.get("portfolio_name")
        if isinstance(existing, dict)
        else DEFAULT_PORTFOLIO_NAME
    )
    raw[str(user)] = {
        "portfolio_name": name or DEFAULT_PORTFOLIO_NAME,
        "positions": data,
    }
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
