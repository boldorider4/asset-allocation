from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from position.factory import factory as _factory
from position.position import Position
from logger import attach_color_stderr_handler_for_module
from visual import SECTOR_PALETTE, Visual

if TYPE_CHECKING:
    from context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

# globals
NAME = "name"
SHORT_NAME = "short_name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"
PRICE = "price"

# developed markets vs. emerging markets breakdown
# 1 => 100% developed markets
# 0 => 100% emerging markets
DMEM = "dmem"
# developed markets vs. other markets breakdown when coutry listed is "other"
# 1 => 100% of "other" is considered developed markets
# 0.5 => 50% of "other" is considered developed markets
DMEM_OTHER = "dmem_other"
# us vs. non-us breakdown
# .7 => 70% us
# 0 => 100% non-us
USAVN = "usavn"


# Breakdown wedge for constituents named Gold (e.g. physical gold ETCs),
# which carry no sector data of their own.
_COMMODITIES_LABEL = "Commodities"
# Sector chart prep: wedges below this fraction (0–1) fold into "Other".
_SECTOR_MIN_WEIGHT = 0.02
# At most this many sector wedges are kept; the rest fold into "Other".
_SECTOR_MAX_WEDGES = 25
# Wedge label collecting filtered-out sector mass.
_SECTOR_OTHER_LABEL = "Other"


class Portfolio:
    @staticmethod
    def _filter_sector_wedges(sectors: dict[str, float]) -> dict[str, float]:
        """Keep the largest N wedges ≥ floor; fold the rest into "Other"."""
        ranked = sorted(sectors.items(), key=lambda item: -item[1])
        head, tail = ranked[:_SECTOR_MAX_WEDGES], ranked[_SECTOR_MAX_WEDGES:]
        kept: dict[str, float] = {}
        dropped = sum(weight for _, weight in tail)
        for name, weight in head:
            if weight >= _SECTOR_MIN_WEIGHT:
                kept[name] = kept.get(name, 0.0) + weight
            else:
                dropped += weight
        if dropped > 0:
            kept[_SECTOR_OTHER_LABEL] = kept.get(_SECTOR_OTHER_LABEL, 0.0) + dropped
        return kept

    def _make_sector_visualizer(
        self, name: str, value: float, data: dict[str, float]
    ) -> Visual | None:
        if not data:
            return None
        return self._ctx.plotter_class()(
            data=data,
            title="{}: Sector Split: {:.2f} Euro".format(name, value),
            factor={"value": value, "unit": "Euro"},
        )

    def __init__(
        self, name: str, positions: list[dict] | None = None, ctx: RuntimeContext | None = None
    ):
        if ctx is None:
            raise TypeError("Portfolio requires an explicit RuntimeContext (ctx=...)")
        self._ctx = ctx
        self._name = name
        self._positions: list[Position] = list()
        for position in positions or []:
            self._positions.append(_factory(
                isin=position.get(ISIN),
                name=position.get(NAME),
                short_name=position.get(SHORT_NAME),
                shares=position.get(SHARES),
                value=position.get(VALUE),
                broker=position.get(BROKER),
                dmem=position.get(DMEM),
                usavn=position.get(USAVN),
                dmem_other=position.get(DMEM_OTHER),
                price=position.get(PRICE),
                ctx=ctx,
            ))
        self._value = self._calculate_value()
        logger.info("Portfolio %r: built total value %.2f from %d position(s)", name, self._value, len(self._positions))
        self._dmem = self._calculate_dmem()
        logger.info("Portfolio %r: calculated DMEM values: %r", name, self._dmem)
        self._usavn = self._calculate_usavn()
        logger.info("Portfolio %r: calculated USAVN values: %r", name, self._usavn)
        self._sectors = self._calculate_sectors()
        logger.info("Portfolio %r: calculated sectors: %r", name, self._sectors)
        self._geosplit_visualizer: Visual | None = None  # subclasses set via ctx.plotter_class()
        # Filtered once here so repeat plot_sectors() calls reuse it.
        self._sector_visualizer = self._make_sector_visualizer(
            name, self._value, self._sector_chart_data()
        )

    def _calculate_value(self) -> float:
        return sum(position.value for position in self._positions)

    def _calculate_dmem(self) -> list[float]:
        return [position.dmem for position in self._positions]

    def _calculate_usavn(self) -> list[float]:
        return [position.usavn for position in self._positions]

    def _calculate_sectors(self) -> dict[str, float]:
        """Value-weighted sector fractions (0–1) across all positions.

        Positions with sector rows contribute their split; rowless positions
        contribute a constituent wedge (short_name, Gold aggregating into
        Commodities, everything else into "Other"). Single pass, so every
        position's mass enters exactly once and merges are plain unions.
        """
        consolidated: dict[str, float] = {}
        if self._value <= 0:
            return consolidated
        for position in self._positions:
            rows = position.sectors()
            value = position.value
            if value is None:
                continue
            share = float(value) / self._value
            if rows:
                for row in rows:
                    name = str(row["name"])
                    # Position rows use weight_pct (0–100); portfolio dicts use fractions (0–1).
                    consolidated[name] = (
                        consolidated.get(name, 0.0)
                        + share * float(row["weight_pct"]) / 100.0
                    )
            else:
                short_name = position._short_name
                if short_name is not None and short_name.lower() == "gold":
                    logger.info("Portfolio %r: position %r is Gold; aggregating into Commodities", self._name, position._isin)
                    label = _COMMODITIES_LABEL
                else:
                    logger.info("Portfolio %r: position %r has no sector info; aggregating into Other", self._name, position._isin)
                    label = short_name or _SECTOR_OTHER_LABEL
                consolidated[label] = consolidated.get(label, 0.0) + share
        return consolidated

    def plot_geosplit(
        self,
        title: str | None = None,
        closing_title: str | None = None,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
    ) -> None:
        if self._geosplit_visualizer is None:
            logger.warning("No geosplit visualizer set for portfolio %r; skipping plot", self._name)
            return
        if title is not None:
            self._geosplit_visualizer.title = title
        if closing_title is not None:
            self._geosplit_visualizer.closing_title = closing_title
        self._geosplit_visualizer.plot(
            label_fontsize=label_fontsize,
            autopct_fontsize=autopct_fontsize,
        )

    def _sector_chart_data(self) -> dict[str, float]:
        """Final chart wedges: _sectors through the Other aggregation.

        Evaluated when the persistent visualizer is built (construction and
        merges) so repeat plot_sectors() calls never re-run the filters.
        """
        chart = self._filter_sector_wedges(self._sectors)
        logger.info("Portfolio %r: sector chart data: %r", self._name, chart)
        return chart

    def plot_sectors(
        self,
        title: str | None = None,
        closing_title: str | None = None,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
    ) -> None:
        if self._sector_visualizer is None:
            logger.warning(
                "No sector visualizer set for portfolio %r; skipping sector plot",
                self._name,
            )
            return
        if title is not None:
            self._sector_visualizer.title = title
        if closing_title is not None:
            self._sector_visualizer.closing_title = closing_title
        self._sector_visualizer.plot(
            label_fontsize=label_fontsize,
            autopct_fontsize=autopct_fontsize,
            colors=SECTOR_PALETTE,
        )

    def _merged_sector_union(
        self, other: 'Portfolio', merged_total: float
    ) -> dict[str, float]:
        """Value-weighted union of both sides' _sectors (merged-relative).

        Every position's mass enters exactly once — at its home portfolio via
        rows or constituent wedges — so merges are plain associative unions
        with no carried state and no double-counting.
        """
        union: dict[str, float] = {}
        for side in (self, other):
            share = float(side._value) / merged_total if merged_total > 0 else 0.0
            logger.debug(
                "Sector union: side %r contributes share %.4f",
                side._name,
                share,
            )
            for name, weight in side._sectors.items():
                union[name] = union.get(name, 0.0) + share * float(weight)
        logger.info("Sector union (merged-relative): %r", union)
        return union

    @property
    def value(self) -> float:
        return self._value
    
    @property
    def dmem(self) -> list[float] | None:
        return self._dmem
    
    @property
    def usavn(self) -> list[float] | None:
        return self._usavn

    @property
    def sectors(self) -> dict[str, float]:
        return self._sectors

    @property
    def total_value(self) -> float:
        return self._value

    def __str__(self) -> str:
        dmem_nonzero = [x for x in self._dmem if x is not None] if self._dmem is not None else None
        dmem_mean = np.mean(dmem_nonzero) if dmem_nonzero else 0

        usavn_nonzero = [x for x in self._usavn if x is not None] if self._usavn is not None else None
        usavn_mean = np.mean(usavn_nonzero) if usavn_nonzero else 0

        return (
            f"*************** Portfolio: {self._name} ***************\n"
            f"Value: {self._value:.2f} \n"
            f"DMEM: {dmem_mean * 100:.2f}% \n"
            f"USAVN: {usavn_mean * 100:.2f}% \n"
            f"Positions:\n"
            f"{''.join(str(position) for position in self._positions)}"
        )

    def __add__(self, other: 'Portfolio') -> 'Portfolio':
        if not isinstance(other, Portfolio):
            return NotImplemented
        merged = object.__new__(Portfolio)
        merged._ctx = self._ctx
        merged._name = f"{self._name} + {other._name}"
        merged._positions = self._positions + other._positions
        merged._value = self._value + other._value
        merged._dmem = None
        merged._usavn = None
        # Plain associative union: every position's mass enters exactly once,
        # at its home portfolio via rows or constituent wedges.
        merged._sectors = self._merged_sector_union(other, merged._value)
        # Filtered once here so repeat plot_sectors() calls reuse it.
        merged._sector_visualizer = merged._make_sector_visualizer(
            merged._name, merged._value, merged._sector_chart_data()
        )
        sv, ov = self._geosplit_visualizer, other._geosplit_visualizer
        if sv is not None and ov is not None:
            merged._geosplit_visualizer = sv + ov
        else:
            # Always set so chained adds never hit a missing attribute.
            merged._geosplit_visualizer = sv if sv is not None else ov
        return merged


if __name__ == "__main__":
    from context import RuntimeContext

    _ctx = RuntimeContext()
    _ctx.ensure_cache_loaded()
    portfolio = Portfolio(name="portfolio", ctx=_ctx, positions=[
        # Amundi Equity World UCITS ETF (Acc)
        {
            ISIN: "IE000BI8OT95",
            SHARES: 100,
            VALUE: 10000,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 0.7,
            DMEM_OTHER: 1,
        },
        # Scalable AC World Xtrackers UCITS ETF (Acc)
        {
            ISIN: "LU2903252349",
            SHARES: 133,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 0.88,
            USAVN: 0.625,
            DMEM_OTHER: 0.5,
        },
        # iShares Core MSCI EM IMI UCITS ETF (Acc)
        {
            ISIN: "IE00BKM4GZ66",
            SHARES: 78,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 0,
            USAVN: 0,
            DMEM_OTHER: 0,
        },
        # State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF (Acc)
        {
            ISIN: "IE00B4YBJ215",
            SHARES: 80,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 1,
            DMEM_OTHER: 1,
        },
        # iShares US Treasury Bond 1-3Y Aggregate EUR Hedged ETF
        {
            ISIN: "IE00BDFK1573",
            SHARES: 51,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 1,
            DMEM_OTHER: 0.7,
        },
        # Xtrackers II EUR Overnight Rate Swap UCITS ETF
        {
            ISIN: "LU0290358497",
            SHARES: 50,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 0,
            DMEM_OTHER: 0.8,
        },
    ])
    print(portfolio)