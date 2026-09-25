# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils import save_position_in_cache
from cli.common import (
    BROKER,
    DMEM,
    DMEM_OTHER,
    NAME,
    PRICE,
    SHARES,
    SHORT_NAME,
    USAVN,
    VALUE,
)
from position.amundi_position import AmundiPosition, amundi_product_url_exists
from position.blackrock_position import (
    BlackRockPosition,
    _ISHARES_PRODUCT_IDS,
    ishares_product_url_exists,
)
from position.invesco_position import InvescoPosition, invesco_product_url_exists
from position.justetf_position import JustETFPosition
from position.l_and_g_position import LAndGPosition, landg_product_url_exists
from position.state_street_position import (
    StateStreetPosition,
    ssga_product_url_exists,
)
from position.ubs_position import UBSPosition, ubs_product_url_exists
from position.xtrackers_position import XtrackersPosition, dws_product_url_exists
from position.yfinance_position import YFinancePosition
from scrape.oskar import _OSKAR as OSKAR
from scrape.scalable import _SCALABLE as SCALABLE
from scrape.traderepublic import _TRADEREPUBLIC as TRADEREPUBLIC
from cli.logger import attach_color_stderr_handler_for_module

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

# Valid ``AppConfig.position_source`` values.
YFINANCE = "yfinance"
JUSTETF = "justetf"


class UpdateCancelled(Exception):
    """Cooperative cancellation of an endpoint-triggered update run.

    Deliberately *not* a ``RuntimeError``/``OSError`` so the resilient
    price/geosplit/sector fallbacks never swallow it.
    """


def _name_looks_like_ubs(name: str | None) -> bool:
    return bool(name) and "ubs" in name.casefold()


def _name_looks_like_invesco(name: str | None) -> bool:
    return bool(name) and "invesco" in name.casefold()


def _name_looks_like_landg(name: str | None) -> bool:
    if not name:
        return False
    folded = name.casefold()
    return any(
        token in folded
        for token in (
            "l&g",
            "l & g",
            "lgim",
            "landg",
            "legal & general",
            "legal and general",
        )
    )


def _scrape_holdings_value_prevails(
    broker: str | None, value: float | None, ctx: RuntimeContext
) -> bool:
    if value is None:
        return False
    if broker == OSKAR:
        fresh_scrape = ctx.config.fetch_oskar
    elif broker == SCALABLE:
        fresh_scrape = ctx.config.fetch_scalable
    elif broker == TRADEREPUBLIC:
        fresh_scrape = ctx.config.fetch_traderepublic
    else:
        return False
    # A live ``--fetch-<broker>`` scrape always wins over shares × quote.
    # Without ``--fetch-prices``, an earlier scrape (or a previous
    # ``--fetch-prices`` write) in the assets file stays authoritative.
    return fresh_scrape or not ctx.config.fetch_prices


def factory(
    isin: str,
    name: str | None = None,
    short_name: str | None = None,
    shares: float | None = None,
    value: float | None = None,
    broker: str | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
    dmem_other: float | None = None,
    *,
    ctx: RuntimeContext,
    price: float | None = None,
) -> JustETFPosition | YFinancePosition:
    if ctx.cancel_event is not None and ctx.cancel_event.is_set():
        raise UpdateCancelled(f"cancelled before building position {isin}")
    # Validated read path: the plain-dict cache is synced into the store
    # by ``ensure_cache_loaded``; ``parsed()`` coerces like the former
    # ``parse_cache_entry`` over the raw dict.
    ctx.ensure_cache_loaded()
    cached_price, cached_countries, cached_sectors = ctx.cache_repo.parsed(isin)
    fetch_prices = ctx.config.fetch_prices
    fetch_geosplit = ctx.config.fetch_geosplit
    fetch_sectorsplit = ctx.config.fetch_sectorsplit
    position_source = ctx.config.position_source
    use_broker_quote = broker == SCALABLE or broker == TRADEREPUBLIC
    prefer_scrape_value = _scrape_holdings_value_prevails(broker, value, ctx)
    logger.info("Factory: prefer scrape value from broker %s for position %s: %s", broker, name, prefer_scrape_value)

    # ``ctor_price``/``countries_arg``/``sectors_arg`` are the only cache-vs-network switches: a value
    # means "use this", ``None`` lets the Position fetch it from its own source.
    if use_broker_quote:
        ctor_price = price if price is not None else cached_price
    else:
        # Cached/asset quote is used as-is without ``--fetch-prices``, and only
        # as fallback when ``--fetch-prices`` scrapes JustETF/Yahoo.
        ctor_price = cached_price if cached_price is not None else price
    if ctor_price is None and not fetch_prices:
        logger.warning(
            "Factory: no cached price for %s; Position will fetch it live",
            isin,
        )

    scrape_geosplit = fetch_geosplit and not (
        position_source == YFINANCE and not use_broker_quote
    )
    if scrape_geosplit:
        countries_arg: dict[str, float] | None = None
    else:
        countries_arg = cached_countries if cached_countries is not None else {}

    scrape_sectorsplit = fetch_sectorsplit and not (
        position_source == YFINANCE and not use_broker_quote
    )
    if scrape_sectorsplit:
        sectors_arg: dict[str, float] | None = None
    else:
        sectors_arg = cached_sectors if cached_sectors is not None else {}

    ctor_kwargs = {
        NAME: name,
        SHORT_NAME: short_name,
        SHARES: shares,
        VALUE: value,
        BROKER: broker,
        DMEM: dmem,
        USAVN: usavn,
        DMEM_OTHER: dmem_other,
        "cached_countries": countries_arg,
        "cached_sectors": sectors_arg,
        PRICE: ctor_price,
        "prefer_scrape_value": prefer_scrape_value,
        "ctx": ctx,
    }
    position: JustETFPosition | YFinancePosition

    if (
        fetch_geosplit
        and isin in XtrackersPosition.ISINS
        and dws_product_url_exists(isin)
    ):
        logger.info("Factory: using XtrackersPosition for %s", isin)
        position = XtrackersPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and isin in _ISHARES_PRODUCT_IDS
        and ishares_product_url_exists(isin)
    ):
        logger.info("Factory: using BlackRockPosition for %s", isin)
        position = BlackRockPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and isin in AmundiPosition.ISINS
        and amundi_product_url_exists(isin)
    ):
        logger.info("Factory: using AmundiPosition for %s", isin)
        position = AmundiPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and isin in StateStreetPosition.ISINS
        and ssga_product_url_exists(isin)
    ):
        logger.info("Factory: using StateStreetPosition for %s", isin)
        position = StateStreetPosition(isin, **ctor_kwargs)
    elif fetch_geosplit and isin in UBSPosition.ISINS:
        logger.info("Factory: using UBSPosition for %s", isin)
        position = UBSPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and isin in InvescoPosition.ISINS
        and invesco_product_url_exists(isin)
    ):
        logger.info("Factory: using InvescoPosition for %s", isin)
        position = InvescoPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and isin in LAndGPosition.ISINS
        and landg_product_url_exists(isin)
    ):
        logger.info("Factory: using LAndGPosition for %s", isin)
        position = LAndGPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and _name_looks_like_invesco(name)
        and invesco_product_url_exists(isin)
    ):
        logger.info("Factory: using InvescoPosition for %s (dng-api)", isin)
        position = InvescoPosition(isin, **ctor_kwargs)
    elif fetch_geosplit and _name_looks_like_ubs(name) and ubs_product_url_exists(isin):
        # Allowlist is the no-probe path. Other UBS-named ETFs still have HA4
        # constituents when etfinstidfromisin returns an instId.
        logger.info("Factory: using UBSPosition for %s (HA4 instId)", isin)
        position = UBSPosition(isin, **ctor_kwargs)
    elif (
        fetch_geosplit
        and _name_looks_like_landg(name)
        and landg_product_url_exists(isin)
    ):
        logger.info("Factory: using LAndGPosition for %s (fund-centre)", isin)
        position = LAndGPosition(isin, **ctor_kwargs)
    elif position_source == YFINANCE:
        position = YFinancePosition(isin, **ctor_kwargs)
    elif position_source == JUSTETF or use_broker_quote:
        position = JustETFPosition(isin, **ctor_kwargs)
    else:
        raise ValueError(f"Unknown position_source: {position_source!r}")

    # A fetched quote is always written back to the cache (even without
    # ``--fetch-prices``) when the row had no cached price, so the cost of a
    # live fetch is paid once; the assets file never stores a price.
    # Staged in-memory; flushed once at end of run.
    update_price = (
        (fetch_prices or cached_price is None)
        and isin is not None
        and position.price is not None
    )
    update_countries = (
        scrape_geosplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    update_sectors = (
        scrape_sectorsplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    if update_price or update_countries or update_sectors:
        save_position_in_cache(
            ctx,
            isin,
            price=position.price,
            countries=position.countries() if update_countries else None,
            sectors=position.sectors() if update_sectors else None,
            update_price=update_price,
            update_countries=update_countries,
            update_sectors=update_sectors,
        )

    # OSKAR cockpit has no share count or unit price. On runs with both
    # --fetch-oskar and --fetch-prices, estimate shares from the fresh
    # holdings value / the fresh quote and queue them for batch persistence
    # after all Position objects exist.
    if prefer_scrape_value and position.price is not None and broker == OSKAR and isin:
        estimated_shares: float | None = None
        if (
            value is not None
            and position.price
            and ctx.config.fetch_oskar
            and ctx.config.fetch_prices
        ):
            estimated_shares = float(value) / float(position.price)
            position._shares = estimated_shares
            logger.info(
                "Factory: estimated OSKAR shares for %s: %s (value=%s / price=%s)",
                isin,
                estimated_shares,
                value,
                position.price,
            )
            ctx.pending_oskar_shares[(isin, float(value))] = estimated_shares
    return position
