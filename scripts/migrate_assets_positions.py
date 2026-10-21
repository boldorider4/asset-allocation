#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time migration of the assets file to the current per-user format.

The positions used to sit directly under the user::

    {"default": {"equity_portfolio": [...]}}

They now live under a name and a "positions" key::

    {"default": {"portfolio_name": "Portfolio",
                 "positions": {"equity_portfolio": [...]}}}

Usage::

    python scripts/migrate_assets_positions.py <assets.json> [--portfolio-name NAME]

The script touches **only** the file you pass (and its sibling backup):
it never reads ``config.ini`` and never looks anywhere else. The
original is backed up before anything is written, and the write itself
is atomic (temp file + rename), so a failure leaves the original
untouched. A file that is already in the current format is left alone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.records import DEFAULT_PORTFOLIO_NAME  # noqa: E402

#: The key the positions live under in the current format.
POSITIONS_KEY = "positions"
#: The key the portfolio name lives under in the current format.
PORTFOLIO_NAME_KEY = "portfolio_name"


def _is_current(payload: Any) -> bool:
    """True when a user payload is already in the current format."""
    return isinstance(payload, dict) and (
        POSITIONS_KEY in payload or PORTFOLIO_NAME_KEY in payload
    )


def _backup_path(target: Path) -> Path:
    """First free backup path: ``<file>.bak``, then ``.bak.1``, ``.bak.2``...

    Existing backups are never overwritten.
    """
    candidate = target.with_name(target.name + ".bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = target.with_name(f"{target.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def migrate(target: Path, portfolio_name: str = DEFAULT_PORTFOLIO_NAME) -> tuple[Path, int]:
    """Migrate *target* to the current format; returns (backup path, users moved)."""
    if not target.is_file():
        raise FileNotFoundError(f"assets file not found: {target}")
    with target.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("assets root must be a JSON object")

    migrated: dict[str, Any] = {}
    moved = 0
    for user, payload in raw.items():
        if _is_current(payload):
            migrated[str(user)] = payload
            continue
        if not isinstance(payload, dict):
            raise ValueError(f"user {user!r} must hold a JSON object")
        migrated[str(user)] = {
            PORTFOLIO_NAME_KEY: portfolio_name,
            POSITIONS_KEY: payload,
        }
        moved += 1

    backup = _backup_path(target)
    shutil.copy2(target, backup)

    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, target)

    # Verify the written file before reporting success.
    with target.open(encoding="utf-8") as f:
        written = json.load(f)
    for user, payload in written.items():
        if not isinstance(payload, dict) or POSITIONS_KEY not in payload:
            raise RuntimeError(f"verification failed: {target} lost {user!r}")
    return backup, moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the assets file to the current per-user format."
    )
    parser.add_argument("assets_file", type=Path, help="Assets JSON file to migrate.")
    parser.add_argument(
        "--portfolio-name",
        default=DEFAULT_PORTFOLIO_NAME,
        help=(
            "Name to give the migrated portfolios "
            f"(default: {DEFAULT_PORTFOLIO_NAME})."
        ),
    )
    args = parser.parse_args(argv)

    try:
        backup, moved = migrate(args.assets_file, args.portfolio_name)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"migrated {args.assets_file} ({moved} user(s))")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
