#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time migration of a legacy assets file to the user-keyed format.

The assets file used to be a flat portfolio::

    {"equity_portfolio": [...], "bond_portfolio": [...]}

It is now keyed by user::

    {"<user>": {"equity_portfolio": [...], "bond_portfolio": [...]}}

Usage::

    python scripts/migrate_assets_user.py <assets.json> <user> [--force]

The script touches **only** the file you pass (and its sibling backup):
it never reads ``config.ini`` and never looks anywhere else. The
original is backed up before anything is written, and the write itself
is atomic (temp file + rename), so a failure leaves the original
untouched.
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


def _is_legacy_root(raw: Any) -> bool:
    """True when ``raw`` is a pre-user flat portfolio (``{bucket: [...]}``)."""
    if not isinstance(raw, dict) or not raw:
        return False
    return all(isinstance(value, list) for value in raw.values())


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


def _validate_buckets(buckets: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(buckets, dict):
        raise ValueError("assets root must be a JSON object")
    for bucket, positions in buckets.items():
        if not isinstance(positions, list):
            raise ValueError(f"{bucket!r} must be a JSON array")
        for i, position in enumerate(positions):
            if not isinstance(position, dict):
                raise ValueError(f"{bucket!r}[{i}] must be a JSON object")
    return buckets


def migrate(target: Path, user: str, *, force: bool = False) -> Path:
    """Migrate *target* to the user-keyed format; return the backup path."""
    if not target.is_file():
        raise FileNotFoundError(f"assets file not found: {target}")
    with target.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("assets root must be a JSON object")
    if not _is_legacy_root(raw):
        if not force:
            raise ValueError(
                f"{target} is not in the legacy flat format (values must all "
                "be arrays); refusing to touch it (use --force to override)"
            )
    buckets = _validate_buckets(raw)

    backup = _backup_path(target)
    shutil.copy2(target, backup)

    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump({user: buckets}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, target)

    # Verify the written file before reporting success.
    with target.open(encoding="utf-8") as f:
        written = json.load(f)
    if user not in written or not isinstance(written[user], dict):
        raise RuntimeError(f"verification failed: {target} lost the user key")
    if len(written[user]) != len(buckets):
        raise RuntimeError(f"verification failed: {target} lost buckets")
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy assets file to the user-keyed format."
    )
    parser.add_argument("assets_file", type=Path, help="Assets JSON file to migrate.")
    parser.add_argument("user", help="User key to file the portfolio under.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Migrate even when the file does not look legacy.",
    )
    args = parser.parse_args(argv)

    user = args.user.strip()
    if not user:
        print("error: the user name must not be empty", file=sys.stderr)
        return 2
    try:
        backup = migrate(args.assets_file, user, force=args.force)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"migrated {args.assets_file} -> user {user!r}")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
