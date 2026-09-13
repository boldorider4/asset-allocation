from .visual import Visual
from .web_chart import WebChart
from .pie_chart import PieChart

DEFAULT_VISUALIZER = WebChart

__all__ = ["Visual", "WebChart", "PieChart", "DEFAULT_VISUALIZER"]
