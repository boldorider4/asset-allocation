from portfolio.portfolio import Portfolio
from visual.pie_chart import PieChart


class NonRegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict], consolidate: bool = False):
        super().__init__(name, positions)

        if consolidate:
            self._visualizer_data = {
                name: self._value,
            }
        else:
            self._visualizer_data = dict()
            for position in self._positions:
                prev_val = 0
                label = position._short_name or position._name
                if label in self._visualizer_data:
                    prev_val = float(self._visualizer_data[label])
                self._visualizer_data[label] = prev_val + float(position.value)

        self._visualizer = PieChart(
            data=self._visualizer_data,
            title="{}: {:.2f} Euro".format(name, self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    def plot(self) -> None:
        self._visualizer.plot()

    def _calculate_dmem(self) -> None:
        return None

    def _calculate_usavn(self) -> None:
        return None

    def __str__(self):
        return super().__str__()