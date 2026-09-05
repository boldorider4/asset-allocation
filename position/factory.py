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
from position.justetf_position import JustETFPosition
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
        return get_fetch_oskar()
    if broker == SCALABLE:
        return get_fetch_scalable()
    if broker == TRADEREPUBLIC:
        return get_fetch_traderepublic()
    return False


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
    elif fetch_prices:
        ctor_price = None
    else:
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

    if POSITION_SOURCE == YFINANCE:
        position = YFinancePosition(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            dmem,
            usavn,
            dmem_other,
            cached_countries=countries_arg,
            value_scale=value_scale,
            price=ctor_price,
            prefer_scrape_value=prefer_scrape_value,
        )
    elif POSITION_SOURCE == JUSTETF or use_broker_quote:
        position = JustETFPosition(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            dmem,
            usavn,
            dmem_other,
            cached_countries=countries_arg,
            value_scale=value_scale,
            price=ctor_price,
            prefer_scrape_value=prefer_scrape_value,
        )
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")

    update_countries = (
        scrape_geosplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    if update_countries:
        save_position_in_cache(
            cache,
            isin,
            countries=position.countries(),
            update_countries=True,
        )

    # OSKAR cockpit has no share count or unit price. After a live scrape
    # (``prefer_scrape_value``) and a fresh ``--fetch-prices`` quote (not cache),
    # queue an estimate from holdings value / price for batch persistence after
    # all portfolio Position objects have been constructed.
    if prefer_scrape_value and fetch_prices and position.price is not None and broker == OSKAR and isin:
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
