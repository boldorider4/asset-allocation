# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils import save_position_in_cache
from cli.common import (
    BROKER,
    DEFAULT_ISIN_PORTFOLIO_BUCKET,
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


# Issuer slug -> specialized Position class. The ISIN registry (not the
# removed per-issuer allowlists) decides which slug an ISIN has.
_ISSUER_POSITION: dict[str, type[JustETFPosition]] = {
    "dws": XtrackersPosition,
    "ishares": BlackRockPosition,
    "amundi": AmundiPosition,
    "ssga": StateStreetPosition,
    "ubs": UBSPosition,
    "invesco": InvescoPosition,
    "landg": LAndGPosition,
}


def _position_has_data(position: JustETFPosition | YFinancePosition) -> bool:
    """True when a built position actually yielded split rows.

    Reads the resolved attributes (never the lazy accessors, which could
    re-trigger network fetches). An empty specialized build means the
    vendor source had nothing usable for this ISIN.
    """
    return bool(position._countries) or bool(position._sectors)


def _validate_data_for_flags(fetch_geosplit: bool, fetch_sectorsplit: bool) -> bool:
    """Whether an empty specialized build counts as failure.

    Only when BOTH split flags are on: with a single flag, routing tests
    (and real runs) legitimately leave the other split empty, so
    emptiness carries no failure signal there.
    """
    return bool(fetch_geosplit and fetch_sectorsplit)


def _probe_issuer_for_isin(isin: str, name: str | None, registry) -> str | None:  # type: ignore[no-untyped-def]
    """Infer the issuer slug for an unregistered ISIN via vendor probes.

    Ordered like the historical dispatch chain. First-run-only cost:
    hits are registered, so later runs take the DB path and skip probes
    entirely. ``ishares``/``ssga`` resolve without network (their URLs
    need a registry product ref, so unknown ISINs short-circuit). A
    UBS-ish name alone also suffices (HA4 fallback inside the class).
    """
    if dws_product_url_exists(isin):
        return "dws"
    if ishares_product_url_exists(isin, registry.get_product_ref_for_isin(isin)):
        return "ishares"
    if amundi_product_url_exists(isin):
        return "amundi"
    if ssga_product_url_exists(isin, registry.get_product_ref_for_isin(isin)):
        return "ssga"
    if ubs_product_url_exists(isin) or _name_looks_like_ubs(name):
        return "ubs"
    if invesco_product_url_exists(isin):
        return "invesco"
    if landg_product_url_exists(isin):
        return "landg"
    return None


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

    if fetch_geosplit:
        countries_arg: dict[str, float] | None = None
    else:
        countries_arg = cached_countries if cached_countries is not None else {}

    if fetch_sectorsplit:
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

    # Issuer-driven dispatch in two passes. Pass 1 consults the ISIN
    # registry and constructs a DB hit directly — no URL probes. Pass 2
    # (unknown issuer, or a DB-hit build that yielded no data) runs the
    # vendor probes once via the helper and registers the outcome.
    # Anything unresolved falls back to the generic JustETF/YFinance
    # constructors below. DWS reachability GETs and holdings scrapes are
    # only needed when refreshing splits; cached splits use generic
    # positions like any other ETF.
    fetch_splits = fetch_geosplit or fetch_sectorsplit
    registry = ctx.isin_registry
    issuer = registry.get_issuer_for_isin(isin) if isin else None
    if isin and isin not in registry:
        registry.register_isin(isin, bucket=DEFAULT_ISIN_PORTFOLIO_BUCKET)

    def _build_specialized(
        position_cls: type[JustETFPosition], *, validate_data: bool
    ) -> JustETFPosition | None:
        """Construct, returning None when the build is unusable.

        Hard failures (RuntimeError/OSError) always fall back. An empty
        build (no countries AND no sectors) falls back only when both
        split flags are on — with a single flag the other split is
        legitimately empty, so emptiness carries no failure signal there.
        """
        try:
            candidate = position_cls(isin, **ctor_kwargs)
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "Factory: %s failed for %s (%s); falling back",
                position_cls.__name__,
                isin,
                exc,
            )
            return None
        if validate_data and not _position_has_data(candidate):
            logger.warning(
                "Factory: %s returned no data for %s; falling back",
                position_cls.__name__,
                isin,
            )
            return None
        return candidate

    validate_data = _validate_data_for_flags(fetch_geosplit, fetch_sectorsplit)
    position_cls: type[JustETFPosition] | None = None
    if fetch_splits and issuer in _ISSUER_POSITION:
        logger.info(
            "Factory: using %s for %s (registry)",
            _ISSUER_POSITION[issuer].__name__,
            isin,
        )
        built = _build_specialized(_ISSUER_POSITION[issuer], validate_data=validate_data)
        position_cls = _ISSUER_POSITION[issuer] if built is not None else None
        position = built  # type: ignore[assignment]
    if position_cls is None and fetch_splits and isin and issuer not in ("justetf", "yfinance"):
        inferred = _probe_issuer_for_isin(isin, name, registry)
        if inferred is not None:
            logger.info(
                "Factory: using %s for %s (probed)",
                _ISSUER_POSITION[inferred].__name__,
                isin,
            )
            registry.set_issuer_for_isin(isin, inferred)
            built = _build_specialized(_ISSUER_POSITION[inferred], validate_data=validate_data)
            position_cls = _ISSUER_POSITION[inferred] if built is not None else None
            position = built  # type: ignore[assignment]
    if position_cls is None:
        if position_source == YFINANCE:
            position = YFinancePosition(isin, **ctor_kwargs)
            if isin:
                registry.register_isin(
                    isin, issuer="yfinance", bucket=DEFAULT_ISIN_PORTFOLIO_BUCKET
                )
        elif position_source == JUSTETF or use_broker_quote:
            position = JustETFPosition(isin, **ctor_kwargs)
            if isin:
                registry.register_isin(
                    isin, issuer="justetf", bucket=DEFAULT_ISIN_PORTFOLIO_BUCKET
                )
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
        fetch_geosplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    update_sectors = (
        fetch_sectorsplit
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
