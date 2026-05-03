from portfolio.portfolio import Portfolio
from visual.pie_chart import PieChart


class NonRegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict]):
        super().__init__(name, positions)

        self._visualer_data = {
            name: self._value,
        }

        self._visualizer = PieChart(
            data=self._visualer_data,
            title="{}: {:.2f} Euro".format(name, self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    @property
    def visualizer_data(self) -> dict[str, float]:
        return self._visualer_data

    def plot(self) -> None:
        self._visualizer.plot()

    def _calculate_dmem(self) -> None:
        return None

    def _calculate_usavn(self) -> None:
        return None

    def __str__(self):
        return super().__str__()