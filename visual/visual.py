from __future__ import annotations

from abc import ABC, abstractmethod


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

    @abstractmethod
    def plot(self) -> None:
        pass

    @classmethod
    def finish_plots(cls) -> None:
        """Run after all :meth:`plot` calls. Window backends keep figures open."""
        return
