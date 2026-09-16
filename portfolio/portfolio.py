import logging

import numpy as np

from position.factory import factory as _factory
from position.position import Position
from logger import attach_color_stderr_handler_for_module
from visual import Visual, get_plotter

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

    @staticmethod
    def _make_sector_visualizer(name: str, value: float, data: dict[str, float]) -> Visual:
        return get_plotter()(
            data=data,
            title="{}: Sector Split: {:.2f} Euro".format(name, value),
            closing_title="Value: {:.2f}".format(value),
            factor={"value": value, "unit": "Euro"},
        )

    def __init__(self, name: str, positions: list[dict] | None = None):
        self._name = name
        self._positions: list[Position] = list()
        for position in positions:
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
            ))
        self._value = self._calculate_value()
        logger.info("Portfolio %r: built total value %.2f from %d position(s)", name, self._value, len(self._positions))
        self._dmem = self._calculate_dmem()
        logger.info("Portfolio %r: calculated DMEM values: %r", name, self._dmem)
        self._usavn = self._calculate_usavn()
        logger.info("Portfolio %r: calculated USAVN values: %r", name, self._usavn)
        self._sectors = self._calculate_sectors()
        logger.info("Portfolio %r: calculated sectors: %r", name, self._sectors)
        self._sector_data = self._filter_sector_wedges(self._sectors)
        self._sector_visualizer = self._make_sector_visualizer(
            name, self._value, self._sector_data
        )
        self._visualizer: Visual | None = None  # subclasses set DEFAULT_VISUALIZER

    def _calculate_value(self) -> float:
        return sum(position.value for position in self._positions)

    def _calculate_dmem(self) -> list[float]:
        return [position.dmem for position in self._positions]

    def _calculate_usavn(self) -> list[float]:
        return [position.usavn for position in self._positions]

    def _calculate_sectors(self) -> dict[str, float]:
        """Value-weighted sector fractions (0–1) across positions with sector data."""
        consolidated: dict[str, float] = {}
        if self._value <= 0:
            return consolidated
        for position in self._positions:
            rows = position.sectors()
            value = position.value
            if not rows or value is None:
                continue
            share = float(value) / self._value
            for row in rows:
                name = str(row["name"])
                # Position rows use weight_pct (0–100); portfolio dicts use fractions (0–1).
                consolidated[name] = (
                    consolidated.get(name, 0.0)
                    + share * float(row["weight_pct"]) / 100.0
                )
        return consolidated

    def plot(
        self,
        title: str | None = None,
        closing_title: str | None = None,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
    ) -> None:
        if self._visualizer is None:
            logger.warning("No visualizer set for portfolio %r; skipping plot", self._name)
            return
        if title is not None:
            self._visualizer.title = title
        if closing_title is not None:
            self._visualizer.closing_title = closing_title
        self._visualizer.plot(
            label_fontsize=label_fontsize,
            autopct_fontsize=autopct_fontsize,
        )

    def plot_sectors(
        self,
        title: str | None = None,
        closing_title: str | None = None,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
    ) -> None:
        if self._sector_visualizer is None or not self._sector_data:
            logger.warning(
                "No sector data for portfolio %r; skipping sector plot", self._name
            )
            return
        if title is not None:
            self._sector_visualizer.title = title
        if closing_title is not None:
            self._sector_visualizer.closing_title = closing_title
        self._sector_visualizer.plot(
            label_fontsize=label_fontsize,
            autopct_fontsize=autopct_fontsize,
        )

    def _constituent_breakdown(self) -> dict[str, float]:
        """Side-relative fractions by short_name for a sector-less portfolio.

        Positions with a short_name get their own wedge; all others fold into
        one wedge named by the portfolio. Exempt from chart filtering.
        """
        breakdown: dict[str, float] = {}
        if self._value <= 0:
            return breakdown
        for position in self._positions:
            value = position.value
            if value is None:
                continue
            label = position._short_name or self._name
            breakdown[label] = breakdown.get(label, 0.0) + float(value) / self._value
        return breakdown

    def _merged_sector_chart_data(
        self, other: 'Portfolio', merged_total: float
    ) -> dict[str, float]:
        """Filtered sector wedges plus unfiltered breakdown wedges of sector-less sides."""
        union: dict[str, float] = {}
        breakdowns: dict[str, float] = {}
        for side in (self, other):
            share = float(side._value) / merged_total if merged_total > 0 else 0.0
            if side._sectors:
                for name, weight in side._sectors.items():
                    union[name] = union.get(name, 0.0) + share * float(weight)
            else:
                for name, weight in side._constituent_breakdown().items():
                    breakdowns[name] = breakdowns.get(name, 0.0) + share * float(weight)
        filtered = self._filter_sector_wedges(union)
        for name, weight in breakdowns.items():
            filtered[name] = filtered.get(name, 0.0) + weight
        return filtered

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
        merged._name = f"{self._name} + {other._name}"
        merged._positions = self._positions + other._positions
        merged._value = self._value + other._value
        merged._dmem = None
        merged._usavn = None
        merged._sectors = merged._calculate_sectors()
        merged._sector_data = self._merged_sector_chart_data(other, merged._value)
        merged._sector_visualizer = merged._make_sector_visualizer(
            merged._name, merged._value, merged._sector_data
        )
        sv, ov = self._visualizer, other._visualizer
        if sv is not None and ov is not None:
            merged._visualizer = sv + ov
        return merged


if __name__ == "__main__":
    portfolio = Portfolio(name="portfolio", positions=[
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