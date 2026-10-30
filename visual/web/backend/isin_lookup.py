# SPDX-License-Identifier: AGPL-3.0-or-later
"""ISIN metadata and price lookup for the constituents draft flow.

The draft UI needs a name before the row exists, but the price resolver
must run after ``add_constituent`` has registered the selected bucket.
Keeping those two steps here keeps ``constituents.py`` free of scrape and
``RuntimeContext`` dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from position import justetf_position

from .constituents import _cached_price_for, cache_lookup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IsinLookup:
    """Result of a draft ISIN check."""

    exists: bool
    name: str | None
    price: float | None


def lookup_isin(db_file: str | Path, isin: str) -> IsinLookup:
    """Resolve name, cached price, and existence for ``isin``.

    Name parsing doubles as a product probe when it succeeds; the chart
    probe is only the fallback. A cached price alone is also enough to
    treat the ISIN as known, so the draft can fill it without network.
    """
    cleaned = (isin or "").strip().upper()
    if not cleaned:
        return IsinLookup(exists=False, name=None, price=None)

    cache = cache_lookup(str(db_file)) if db_file else {}
    price = _cached_price_for(cache, cleaned)

    try:
        name = justetf_position.just_etf_name(cleaned)
    except Exception as exc:
        logger.warning("JustETF name lookup failed for %s (%s)", cleaned, exc)
        name = None

    exists = bool(name) or price is not None
    if not exists:
        try:
            exists = justetf_position.just_etf_product_url_exists(cleaned)
        except Exception as exc:
            logger.warning("JustETF product check failed for %s (%s)", cleaned, exc)
            exists = False
    return IsinLookup(exists=bool(exists), name=name, price=price)


def fetch_price_for_isin(
    db_file: str | Path,
    isin: str,
    user: str,
) -> float | None:
    """Fetch and cache a missing JustETF price after the row is added.

    Runs the full ``factory`` dispatch so issuer-specific price handling is
    preserved, but disables both split fetches: geo/sector data is curated
    asynchronously and cached rows are reused without another request.
    Failures are non-fatal; the caller still returns the added row.
    """
    cleaned = (isin or "").strip().upper()
    if not cleaned or not db_file:
        return None

    from cli.context import AppConfig, RuntimeContext, ServerConfig
    from position.factory import factory

    db_path = Path(db_file)
    ctx = RuntimeContext(
        config=AppConfig(
            fetch_prices=True,
            fetch_geosplit=False,
            fetch_sectorsplit=False,
            position_source="justetf",
            user=user,
            server=ServerConfig(
                port=0,
                address="localhost",
                directory=db_path.parent,
                db_file=db_path,
            ),
        )
    )
    try:
        position = factory(cleaned, ctx=ctx, write_cache=True)
        return position.price
    except Exception as exc:
        logger.warning("JustETF price lookup failed for %s (%s)", cleaned, exc)
        return None
    finally:
        try:
            ctx.flush_cache()
        except Exception as exc:
            logger.warning("cache flush after price lookup failed (%s)", exc)
        try:
            ctx.flush_isin_registry()
        except Exception as exc:
            logger.warning("registry flush after price lookup failed (%s)", exc)
