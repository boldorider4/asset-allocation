#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-probe ISINs and upgrade generic registry issuers.

Usage::

    python scripts/rescan_isin_issuers.py --db-file asalloc.db 'IE00...,IE00...'

``factory`` deliberately treats ``justetf``/``yfinance`` as decided and
does not probe them again. This maintenance script therefore calls the
existing probe helper from :mod:`position.factory` directly, then updates
only rows that are still generic (or fills a missing vendor product ref).
An already-specialized issuer is never replaced by a conflicting probe.

When no specialized issuer matches and the row has no issuer yet, the
script verifies the ISIN against JustETF. That check runs after every
specialized probe. A valid JustETF product is recorded as the generic
``justetf`` issuer with the default portfolio bucket; a non-product stays
unresolved. Newly resolved rows also get the default bucket.

Every actual scan increments ``isin_registry.visits``. A previously
visited ISIN is skipped unless ``--force`` is given. Failed probes still
count as visits, so retrying a failed ISIN also requires ``--force``.
Blacklisted ISINs are always skipped, even with ``--force``.
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

from cli.common import DEFAULT_ISIN_PORTFOLIO_BUCKET  # noqa: E402
from position.factory import (  # noqa: E402
    _IssuerProbe,
    _probe_issuer_for_isin,
)
from position.justetf_position import just_etf_product_url_exists  # noqa: E402
from storage.repositories import IsinRegistry  # noqa: E402
from storage.wiring import isin_backend  # noqa: E402

Probe = Callable[[str, str | None, Any], Any]
_GENERIC_ISSUERS = frozenset({"justetf", "yfinance"})
_ISIN_BLACKLIST = frozenset({"LU2903252349"})


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
        if table is None:
            raise ValueError(f"table 'isin_registry' not found in {db_file}")
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(isin_registry)").fetchall()
        }
    finally:
        conn.close()
    if "visits" not in columns:
        raise ValueError(
            "table 'isin_registry' is missing the 'visits' column; run "
            "scripts/v3.0.0_add_isin_registry_visits.py first"
        )


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


def _normalize_isins(raw: str) -> list[str]:
    """Split a comma-separated list, normalizing and deduplicating it."""
    return list(
        dict.fromkeys(
            value.strip().upper() for value in raw.split(",") if value.strip()
        )
    )


def rescan_isins(
    db_file: Path,
    isins: Sequence[str],
    *,
    force: bool = False,
    probe: Probe = _probe_issuer_for_isin,
    justetf_exists: Callable[[str], bool] = just_etf_product_url_exists,
    emit: Callable[[str], None] = print,
) -> tuple[int, int, int, int]:
    """Re-probe *isins* and persist upgrades.

    Returns ``(updated, skipped, unchanged, errors)``. An existing
    specialized issuer wins over a conflicting probe result; a missing
    ``product_ref`` is filled when the probe agrees with the stored issuer.
    A row with no issuer is verified against JustETF only after all
    specialized probes miss. Previously visited ISINs are skipped unless
    ``force`` is true.
    """
    _require_registry_table(db_file)
    names = _names_by_isin(db_file)
    registry = IsinRegistry(isin_backend(db_file))
    registry.open()
    updated = skipped = unchanged = errors = 0
    try:
        for isin in isins:
            try:
                if isin.strip().upper() in _ISIN_BLACKLIST:
                    emit(f"{isin}: skipped (blacklisted)")
                    skipped += 1
                    continue

                visits = registry.get_visits_for_isin(isin)
                if visits and not force:
                    emit(
                        f"{isin}: skipped (visited {visits}x; use --force "
                        "to rescan)"
                    )
                    skipped += 1
                    continue

                registry.increment_visits_for_isin(isin)
                registry.persist()
                before = registry.get_issuer_for_isin(isin)
                before_ref = registry.get_product_ref_for_isin(isin)
                inferred = probe(isin, names.get(isin), registry)
                if inferred is None and before is None:
                    if justetf_exists(isin):
                        inferred = _IssuerProbe("justetf")
                    else:
                        emit(
                            f"{isin}: unchanged "
                            "(missing; not found on JustETF)"
                        )
                        unchanged += 1
                        continue
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
                if before is None:
                    registry.register_isin(
                        isin, bucket=DEFAULT_ISIN_PORTFOLIO_BUCKET
                    )
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
    return updated, skipped, unchanged, errors


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
        "--force",
        action="store_true",
        help="Rescan ISINs that already have a non-zero visits counter.",
    )
    parser.add_argument(
        "isins",
        metavar="ISINS",
        help="Comma-separated ISINs to re-probe.",
    )
    args = parser.parse_args(argv)

    isins = _normalize_isins(args.isins)
    if not isins:
        print("error: at least one non-empty ISIN is required", file=sys.stderr)
        return 2

    try:
        updated, skipped, unchanged, errors = rescan_isins(
            args.db_file, isins, force=args.force
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"updated {updated}, skipped {skipped}, "
        f"unchanged {unchanged}, errors {errors}",
        file=sys.stderr,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
