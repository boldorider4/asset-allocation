from portfolio.portfolio import Portfolio
import numpy as np
from visual.pie_chart import PieChart


class RegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict]):
        super().__init__(name, positions)

        values = np.asarray([position.value for position in self._positions], dtype=float)
        dmem_arr = np.asarray(self._dmem, dtype=float)
        usavn_arr = np.asarray(self._usavn, dtype=float)
        developed_share = (
            float(np.dot(values, dmem_arr)) / self._value if self._value > 0 else 0.0
        )
        dmem_weighted = float(np.dot(values, dmem_arr))
        us_within_developed = (
            float(np.dot(values, usavn_arr)) / dmem_weighted if dmem_weighted > 0 else 0.0
        )

        self._dmem_visualizer = PieChart(data={
            "Developed Markets": developed_share,
            "Emerging Markets": 1.0 - developed_share,
        }, title="{}: Developed Markets vs. Emerging Markets".format(self._name))

        self._usavn_visualizer = PieChart(data={
            "US": us_within_developed,
            "Non-US": 1.0 - us_within_developed,
        }, title="{}: US vs. Non-US (within developed markets)".format(self._name))

        # now let's look at regional split: us vs. non-us vs. emerging markets
        # Scale us_within_developed by the developed_share so that US is proportional to the total_value
        self._regional_split = {
            "US": us_within_developed * developed_share,
            "Non-US": (1.0 - us_within_developed) * developed_share,
            "Emerging Markets": 1.0 - developed_share,
        }
        self._regional_split_visualizer = PieChart(
            data=self._regional_split,
            title="{}: Total Regional Split (US vs. Non-US vs. EM): {:.2f} Euro".format(self._name, self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    @property
    def visualizer_data(self) -> dict[str, float]:
        return self._regional_split

    def plot_dmem(self) -> None:
        self._dmem_visualizer.plot()

    def plot_usavn(self) -> None:
        self._usavn_visualizer.plot()

    def plot_regional_split(self) -> None:
        self._regional_split_visualizer.plot()

    def __str__(self):
        return super().__str__()