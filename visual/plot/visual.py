# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from abc import ABC, abstractmethod


# Alternate wedge colors for sector breakdowns (matplotlib tab20c breaks)
SECTOR_PALETTE: tuple[str, ...] = (
    "#3182bd",
    "#6baed6",
    "#9ecae1",
    "#c6dbef",
    "#e6550d",
    "#fd8d3c",
    "#fdae6b",
    "#fdd0a2",
    "#31a354",
    "#74c476",
    "#a1d99b",
    "#c7e9c0",
    "#756bb1",
    "#9e9ac8",
    "#bcbddc",
    "#dadaeb",
    "#636363",
    "#969696",
    "#bdbdbd",
    "#d9d9d9",
)


class Visual(ABC):
    """Abstract pie visualizer; subclasses implement :meth:`plot`."""

    def __init__(
        self,
        data: dict[str, float],
        title: str | None = None,
        closing_title: str | None = None,
    ):
        self._data = data
        self._title = title
        self._closing_title = closing_title

    @property
    def title(self) -> str | None:
        return self._title

    @title.setter
    def title(self, value: str | None) -> None:
        self._title = value

    @property
    def closing_title(self) -> str | None:
        return self._closing_title

    @closing_title.setter
    def closing_title(self, value: str | None) -> None:
        self._closing_title = value

    @property
    def factor(self) -> dict | None:
        """Absolute-value factor (``{"value": ..., "unit": ...}``), if any."""
        return getattr(self, "_factor", None)

    @factor.setter
    def factor(self, value: dict | None) -> None:
        self._factor = value

    @abstractmethod
    def plot(self) -> None:
        pass

    @classmethod
    def finish_plots(cls) -> None:
        """Run after all :meth:`plot` calls. Window backends keep figures open."""
        return
