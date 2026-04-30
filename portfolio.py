from asset_price.factory import factory as _factory
from asset_price.position import Position
import numpy as np
from visual.pie_chart import PieChart


# globals
NAME = "name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"

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


class Portfolio:
    def __init__(self, name: str, positions: list[dict] | None = None):
        self._name = name
        self._positions: list[Position] = list()
        for position in positions:
            self._positions.append(_factory(
                isin=position.get(ISIN),
                name=position.get(NAME),
                shares=position.get(SHARES),
                value=position.get(VALUE),
                broker=position.get(BROKER),
                dmem=position.get(DMEM),
                usavn=position.get(USAVN),
                dmem_other=position.get(DMEM_OTHER),
            ))
        self._value = self._calculate_value()
        self._dmem = self._calculate_dmem()
        self._usavn = self._calculate_usavn()

        values = np.asarray(self.position_values, dtype=float)
        total_value = float(np.sum(values))
        dmem_arr = np.asarray(self._dmem, dtype=float)
        usavn_arr = np.asarray(self._usavn, dtype=float)
        developed_share = (
            float(np.dot(values, dmem_arr)) / total_value if total_value > 0 else 0.0
        )
        dmem_weighted = float(np.dot(values, dmem_arr))
        us_within_developed = (
            float(np.dot(values, usavn_arr)) / dmem_weighted if dmem_weighted > 0 else 0.0
        )

        self._dmem_visualizer = PieChart(data={
            "Developed Markets": developed_share,
            "Emerging Markets": 1.0 - developed_share,
        }, title="Developed Markets vs. Emerging Markets")

        self._usavn_visualizer = PieChart(data={
            "US": us_within_developed,
            "Non-US": 1.0 - us_within_developed,
        }, title="US vs. Non-US (within developed markets)")

        # now let's look at regional split: us vs. non-us vs. emerging markets
        # Scale us_within_developed by the developed_share so that US is proportional to the total_value
        self._regional_split = {
            "US": us_within_developed * developed_share,
            "Non-US": (1.0 - us_within_developed) * developed_share,
            "Emerging Markets": 1.0 - developed_share,
        }
        self._regional_split_visualizer = PieChart(
            data=self._regional_split,
            title="Total Regional Split (US vs. Non-US vs. EM): {:.2f} Euro".format(self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    def _calculate_value(self) -> float:
        return sum(position.value for position in self._positions)

    def _calculate_dmem(self) -> list[float]:
        return [position.dmem for position in self._positions]

    def _calculate_usavn(self) -> list[float]:
        return [position.usavn for position in self._positions]

    def plot_dmem(self) -> None:
        self._dmem_visualizer.plot()

    def plot_usavn(self) -> None:
        self._usavn_visualizer.plot()

    def plot_regional_split(self) -> None:
        self._regional_split_visualizer.plot()

    @property
    def value(self) -> float:
        return self._value
    
    @property
    def dmem(self) -> list[float]:
        return self._dmem
    
    @property
    def usavn(self) -> list[float]:
        return self._usavn

    @property
    def position_values(self) -> list[float]:
        return [position.value for position in self._positions]

    @property
    def total_value(self) -> float:
        return np.sum(self.position_values)

    def __str__(self) -> str:
        dmem_nonzero = [x for x in self._dmem if x is not None]
        dmem_mean = np.mean(dmem_nonzero) if dmem_nonzero else 0

        usavn_nonzero = [x for x in self._usavn if x is not None]
        usavn_mean = np.mean(usavn_nonzero) if usavn_nonzero else 0

        return (
            f"*************** Portfolio: {self._name} ***************\n"
            f"Value: {self._value:.2f} \n"
            f"DMEM: {dmem_mean * 100:.2f}% \n"
            f"USAVN: {usavn_mean * 100:.2f}% \n"
            f"Positions:\n"
            f"{''.join(str(position) for position in self._positions)}"
        )


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