"""CLI plotter selection."""

from __future__ import annotations

import unittest

from visual import get_plotter, set_plotter
from visual.pie_chart import PieChart
from visual.web_chart import WebChart


class TestCliPlotFlags(unittest.TestCase):
    def tearDown(self) -> None:
        set_plotter("web")

    def test_set_plotter_web_and_pie(self) -> None:
        set_plotter("web")
        self.assertIs(get_plotter(), WebChart)
        set_plotter("pie-chart")
        self.assertIs(get_plotter(), PieChart)
