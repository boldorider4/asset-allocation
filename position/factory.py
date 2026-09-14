import logging

from common import PENDING_OSKAR_SHARES
from utils import (
    load_cache,
    parse_cache_entry,
    save_position_in_cache,
    get_fetch_geosplit,
    get_fetch_oskar,
    get_fetch_prices,
    get_fetch_scalable,
    get_fetch_traderepublic,
    get_incognito_value_factor,
)
from position.amundi_position import AmundiPosition, amundi_product_url_exists
from position.blackrock_position import (
    BlackRockPosition,
    _ISHARES_PRODUCT_IDS,
    ishares_product_url_exists,
)
from position.justetf_position import JustETFPosition
from position.ubs_position import UBSPosition, ubs_product_url_exists
from position.xtrackers_position import XtrackersPosition, dws_product_url_exists
from position.yfinance_position import YFinancePosition
from scrape.oskar import _OSKAR as OSKAR
from scrape.scalable import _SCALABLE as SCALABLE
from scrape.traderepublic import _TRADEREPUBLIC as TRADEREPUBLIC
from logger import attach_color_stderr_handler_for_module

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF


def _scrape_holdings_value_prevails(broker: str | None, value: float | None) -> bool:
    if value is None:
        return False
    if broker == OSKAR:
        fresh_scrape = get_fetch_oskar()
    elif broker == SCALABLE:
        fresh_scrape = get_fetch_scalable()
    elif broker == TRADEREPUBLIC:
        fresh_scrape = get_fetch_traderepublic()
    else:
        return False
    # A live ``--fetch-<broker>`` scrape always wins over shares × quote.
    # Without ``--fetch-prices``, an earlier scrape (or a previous
    # ``--fetch-prices`` write) in the assets file stays authoritative.
    return fresh_scrape or not get_fetch_prices()


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
    value_scale: float | None = None,
    price: float | None = None,
) -> JustETFPosition | YFinancePosition:
    if value_scale is None:
        logger.info("Factory: no value scale provided, using default value")
        value_scale = get_incognito_value_factor()
    cache = load_cache()
    cached_price, cached_countries = parse_cache_entry(cache.get(isin))
    fetch_prices = get_fetch_prices()
    fetch_geosplit = get_fetch_geosplit()
    use_broker_quote = broker == SCALABLE or broker == TRADEREPUBLIC
    prefer_scrape_value = _scrape_holdings_value_prevails(broker, value)
    logger.info("Factory: prefer scrape value from broker %s for position %s: %s", broker, name, prefer_scrape_value)

    # ``ctor_price``/``countries_arg`` are the only cache-vs-network switches: a value
    # means "use this", ``None`` lets the Position fetch it from its own source.
    # Scalable / Trade Republic quotes live in cache.json (never the assets file).
    if use_broker_quote:
        ctor_price = price if price is not None else cached_price
    else:
        # Cached/asset quote is used as-is without ``--fetch-prices``, and only
        # as fallback when ``--fetch-prices`` scrapes JustETF/Yahoo.
        ctor_price = cached_price if cached_price is not None else price
    if ctor_price is None and not fetch_prices:
        logger.warning(
            "Factory: no cached price for %s; Position will fetch it (not cached without --fetch-prices)",
            isin,
        )

    scrape_geosplit = fetch_geosplit and not (
        POSITION_SOURCE == YFINANCE and not use_broker_quote
    )
    if scrape_geosplit:
        countries_arg: dict[str, float] | None = None
    else:
        countries_arg = cached_countries if cached_countries is not None else {}

    ctor_kwargs = {
        "name": name,
        "short_name": short_name,
        "shares": shares,
        "value": value,
        "broker": broker,
        "dmem": dmem,
        "usavn": usavn,
        "dmem_other": dmem_other,
        "cached_countries": countries_arg,
        "value_scale": value_scale,
        "price": ctor_price,
        "prefer_scrape_value": prefer_scrape_value,
    }
    position: JustETFPosition | YFinancePosition
    # DWS reachability GET and holdings scrape are only needed when refreshing
    # country weights. Cached geosplit uses JustETF/YFinance like any other ETF.
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
        and isin in UBSPosition.ISINS
        and ubs_product_url_exists(isin)
    ):
        logger.info("Factory: using UBSPosition for %s", isin)
        position = UBSPosition(isin, **ctor_kwargs)
    elif POSITION_SOURCE == YFINANCE:
        position = YFinancePosition(isin, **ctor_kwargs)
    elif POSITION_SOURCE == JUSTETF or use_broker_quote:
        position = JustETFPosition(isin, **ctor_kwargs)
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")

    # ``--fetch-prices`` always refreshes the cached quote; the assets file
    # never stores a price.
    update_price = fetch_prices and isin is not None and position.price is not None
    update_countries = (
        scrape_geosplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    if update_price or update_countries:
        save_position_in_cache(
            cache,
            isin,
            price=position.price,
            countries=position.countries() if update_countries else None,
            update_price=update_price,
            update_countries=update_countries,
        )

    # OSKAR cockpit has no share count or unit price. After a live scrape,
    # estimate shares from holdings value / the available quote (fresh or cached)
    # and queue them for batch persistence after all Position objects exist.
    if prefer_scrape_value and position.price is not None and broker == OSKAR and isin:
        estimated_shares: float | None = None
        if shares is None and value is not None and position.price:
            estimated_shares = float(value) / float(position.price)
            position._shares = estimated_shares
            logger.info(
                "Factory: estimated OSKAR shares for %s: %s (value=%s / price=%s)",
                isin,
                estimated_shares,
                value,
                position.price,
            )
            PENDING_OSKAR_SHARES[(isin, float(value))] = estimated_shares
    return position
