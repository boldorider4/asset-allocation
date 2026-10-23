#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-probe ISINs and upgrade generic registry issuers.

Usage::

    python scripts/rescan_isin_issuers.py --db-file asalloc.db IE00... IE00...

``factory`` deliberately treats ``justetf``/``yfinance`` as decided and
does not probe them again. This maintenance script therefore calls the
existing probe helper from :mod:`position.factory` directly, then updates
only rows that are still generic (or fills a missing vendor product ref).
An already-specialized issuer is never replaced by a conflicting probe.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from position.factory import _probe_issuer_for_isin  # noqa: E402
from storage.repositories import IsinRegistry  # noqa: E402
from storage.wiring import isin_backend  # noqa: E402

Probe = Callable[[str, str | None, Any], Any]
_GENERIC_ISSUERS = frozenset({"justetf", "yfinance"})


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _require_registry_table(db_file: Path) -> None:
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    conn = sqlite3.connect(_read_only_uri(db_file), uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'isin_registry'"
        ).fetchone()
    finally:
        conn.close()
    if table is None:
        raise ValueError(f"table 'isin_registry' not found in {db_file}")


def _names_by_isin(db_file: Path) -> dict[str, str]:
    """Best-effort position names, used by the UBS name fallback."""
    conn = sqlite3.connect(_read_only_uri(db_file), uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'user_positions'"
        ).fetchone()
        if table is None:
            return {}
        rows = conn.execute(
            "SELECT isin, name FROM user_positions WHERE isin IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    names: dict[str, str] = {}
    for isin, name in rows:
        if isinstance(isin, str) and isin and isinstance(name, str) and name:
            names.setdefault(isin.upper(), name)
    return names


def _normalize_isins(values: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip().upper() for value in values if value.strip()
        )
    )


def rescan_isins(
    db_file: Path,
    isins: Sequence[str],
    *,
    probe: Probe = _probe_issuer_for_isin,
    emit: Callable[[str], None] = print,
) -> tuple[int, int, int]:
    """Re-probe *isins* and persist upgrades.

    Returns ``(updated, unchanged, errors)``. An existing specialized
    issuer wins over a conflicting probe result; a missing ``product_ref``
    is filled when the probe agrees with the stored issuer.
    """
    _require_registry_table(db_file)
    names = _names_by_isin(db_file)
    registry = IsinRegistry(isin_backend(db_file))
    registry.open()
    updated = unchanged = errors = 0
    try:
        for isin in isins:
            try:
                before = registry.get_issuer_for_isin(isin)
                before_ref = registry.get_product_ref_for_isin(isin)
                inferred = probe(isin, names.get(isin), registry)
                if inferred is None:
                    emit(f"{isin}: unchanged ({before or 'missing'})")
                    unchanged += 1
                    continue
                if (
                    before not in (None, *_GENERIC_ISSUERS)
                    and before != inferred.issuer
                ):
                    emit(
                        f"{isin}: kept {before} "
                        f"(probe suggested {inferred.issuer})"
                    )
                    unchanged += 1
                    continue

                changed = False
                if before != inferred.issuer:
                    registry.set_issuer_for_isin(isin, inferred.issuer)
                    changed = True
                if inferred.product_ref is not None and before_ref is None:
                    registry.register_isin(
                        isin, product_ref=inferred.product_ref
                    )
                    changed = True
                if not changed:
                    suffix = (
                        f" (product_ref={before_ref})"
                        if before_ref is not None
                        else ""
                    )
                    emit(f"{isin}: unchanged ({before}{suffix})")
                    unchanged += 1
                    continue

                registry.persist()
                product_ref = registry.get_product_ref_for_isin(isin)
                suffix = (
                    f" (product_ref={product_ref})"
                    if product_ref is not None
                    else ""
                )
                emit(
                    f"{isin}: {before or 'missing'} -> "
                    f"{inferred.issuer}{suffix}"
                )
                updated += 1
            except Exception as exc:  # noqa: BLE001 - keep scanning
                emit(f"{isin}: error: {exc}")
                errors += 1
    finally:
        registry.close()
    return updated, unchanged, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-probe ISINs and upgrade generic issuer entries in "
            "isin_registry."
        )
    )
    parser.add_argument(
        "--db-file",
        required=True,
        type=Path,
        help="SQLite database file.",
    )
    parser.add_argument(
        "isins",
        nargs="+",
        metavar="ISIN",
        help="ISINs to re-probe.",
    )
    args = parser.parse_args(argv)

    isins = _normalize_isins(args.isins)
    if not isins:
        print("error: at least one non-empty ISIN is required", file=sys.stderr)
        return 2

    try:
        updated, unchanged, errors = rescan_isins(args.db_file, isins)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"updated {updated}, unchanged {unchanged}, errors {errors}",
        file=sys.stderr,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
