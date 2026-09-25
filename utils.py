# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import logging
import os
from pathlib import Path
from typing import Any

from cli.common import DEFAULT_ISIN_PORTFOLIO_BUCKET
from cli.logger import attach_color_stderr_handler_for_module
from storage.records import CacheEntry

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

# Per-ISIN value in the cache (written by ``save_position_in_cache``).
# Aliases of the ``CacheEntry`` field names; kept so existing importers
# (e.g. ``scrape.scalable``) are untouched.
_CACHE_PRICE = CacheEntry.PRICE
_CACHE_COUNTRIES = CacheEntry.COUNTRIES
_CACHE_SECTORS = CacheEntry.SECTORS


def parse_cache_entry(entry: Any) -> tuple[float | None, dict[str, float] | None, dict[str, float] | None]:
    """
    Returns ``(price, cached_countries, cached_sectors)``.
    Each element is ``None`` if the row has no stored value for it (fetch at use);
    a row written by ``--fetch-geosplit`` alone has ``countries`` but no ``price``,
    and a row written by ``--fetch-sectorsplit`` alone has ``sectors`` but no ``price``.
    Country/sector values in the file are fractions of 1 (e.g. ``0.89`` for 89%).
    """
    if not isinstance(entry, dict):
        return None, None, None
    raw_price = entry.get(_CACHE_PRICE)
    price = None if raw_price is None else float(raw_price)
    co = entry.get(_CACHE_COUNTRIES)
    cached_countries = None if co is None else {str(k): float(v) for k, v in co.items()}
    se = entry.get(_CACHE_SECTORS)
    cached_sectors = None if se is None else {str(k): float(v) for k, v in se.items()}
    return price, cached_countries, cached_sectors


def countries_to_cache_fractions(
    rows: list[dict[str, float | str]] | None,
) -> dict[str, float]:
    if not rows:
        return {}
    return {str(r["name"]): float(r["weight_pct"]) / 100.0 for r in rows}


def sectors_to_cache_fractions(
    rows: list[dict[str, float | str]] | None,
) -> dict[str, float]:
    if not rows:
        return {}
    return {str(r["name"]): float(r["weight_pct"]) / 100.0 for r in rows}


def save_position_in_cache(
    ctx: Any,
    isin: str,
    *,
    price: float | None = None,
    countries: list[dict[str, float | str]] | None = None,
    sectors: list[dict[str, float | str]] | None = None,
    update_price: bool = False,
    update_countries: bool = False,
    update_sectors: bool = False,
) -> None:
    """Stage a cache update in ``ctx.cache`` (in-memory; flushed at end of run).

    Validated write path: goes through ``ctx.cache_repo`` and mirrors the validated
    row back into the plain ``ctx.cache`` dict.
    """
    if not update_price and not update_countries and not update_sectors:
        return
    ctx.ensure_cache_loaded()
    # Exact parity with the former dict logic: a flagged-but-None split
    # stages as ``{}`` (``rows_to_fractions([])``), not as a missing key.
    if update_countries and countries is None:
        countries = []
    if update_sectors and sectors is None:
        sectors = []
    entry = ctx.cache_repo.stage(
        isin,
        price=price,
        countries=countries,
        sectors=sectors,
        update_price=update_price,
        update_countries=update_countries,
        update_sectors=update_sectors,
    )
    if entry is not None:
        ctx._mirror_cache_row(isin)
    ctx.mark_cache_dirty()


def cache_broker_quotes(ctx: Any, quotes: dict[str, float | None]) -> None:
    """Stage Scalable / Trade Republic unit prices in ``ctx.cache``.

    No-op unless ``--fetch-prices`` is set.
    """
    if not ctx.config.fetch_prices:
        return
    ctx.ensure_cache_loaded()
    count = ctx.cache_repo.stage_quotes(quotes)
    if not count:
        return
    for isin, price in quotes.items():
        if isin and price is not None:
            ctx._mirror_cache_row(str(isin))
    ctx.mark_cache_dirty()
    logger.info("staged %d broker quote(s) in cache", count)


def load_portfolio(path: Path) -> dict[str, list[dict]]:
    """Load portfolio buckets from a JSON file."""
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("assets root must be a JSON object")
    for key, positions in data.items():
        if not isinstance(positions, list):
            raise ValueError(f"{key!r} must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise ValueError(f"{key}[{i}] must be a JSON object")
    return data


def write_portfolio(path: Path, data: dict[str, list[dict]]) -> None:
    """Overwrite the assets JSON file at ``path`` with ``data``.

    Written atomically (temp file + rename) so a killed update never
    leaves a torn file behind.
    """
    assets_path = Path(path)
    tmp_path = assets_path.with_name(f".{assets_path.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, assets_path)


def persist_oskar_shares_in_portfolio(ctx: Any) -> None:
    """Apply all fresh OSKAR share estimates and write the assets file once.

    Only rows whose shares actually changed are written, so re-estimating
    every run never rewrites an untouched assets file.
    """
    if not ctx.pending_oskar_shares:
        return
    # Lazy import: ``scrape.oskar`` imports portfolio constants only.
    from scrape.oskar import _OSKAR as OSKAR

    updated_count = 0
    try:
        for positions in ctx.portfolio.values():
            for position in positions:
                pos_broker = position.get("broker") or position.get("Broker")
                pos_isin = position.get("ISIN") or position.get("isin")
                pos_value = position.get("value")
                if pos_broker != OSKAR or not pos_isin or pos_value is None:
                    continue
                shares = ctx.pending_oskar_shares.get(
                    (str(pos_isin), float(pos_value))
                )
                if shares is not None and position.get("shares") != shares:
                    position["shares"] = shares
                    updated_count += 1
        if updated_count:
            ctx.persist_portfolio()
            logger.info(
                "wrote %d OSKAR share estimate(s) to portfolio file",
                updated_count,
            )
    finally:
        ctx.pending_oskar_shares.clear()


def persist_fetched_values_in_portfolio(ctx: Any) -> None:
    """Write shares × quote into the assets file for unscraped broker rows."""
    if not ctx.pending_fetched_values:
        return

    updated_count = 0
    try:
        for positions in ctx.portfolio.values():
            for position in positions:
                pos_isin = position.get("ISIN") or position.get("isin")
                if not pos_isin:
                    continue
                pos_broker = position.get("broker") or position.get("Broker")
                new_value = ctx.pending_fetched_values.get(
                    (str(pos_isin), pos_broker)
                )
                if new_value is not None:
                    position["value"] = new_value
                    updated_count += 1
        if updated_count:
            ctx.persist_portfolio()
            logger.info(
                "wrote %d fetch-prices value(s) to portfolio file",
                updated_count,
            )
    finally:
        ctx.pending_fetched_values.clear()


def bucket_for_isin(ctx: Any, isin: str | None) -> str:
    """Map an ISIN to a portfolio bucket via the ISIN registry.

    Falls back to the default (equity) bucket with a warning when the
    ISIN has no registry row or the row carries no bucket.
    """
    bucket = ctx.isin_registry.get_bucket_for_isin(isin) if isin else None
    if not bucket:
        logger.warning(
            "unknown ISIN %s; using %r",
            isin,
            DEFAULT_ISIN_PORTFOLIO_BUCKET,
        )
        return DEFAULT_ISIN_PORTFOLIO_BUCKET
    return bucket
