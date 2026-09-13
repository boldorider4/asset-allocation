from __future__ import annotations

from abc import ABC, abstractmethod


class Visual(ABC):
    """Abstract pie visualizer; subclasses implement :meth:`plot`."""

    def __init__(self, data: dict[str, float], title: str | None = None):
        self._data = data
        self._title = title

    @property
    def title(self) -> str | None:
        return self._title

    @title.setter
    def title(self, value: str | None) -> None:
        self._title = value

    @abstractmethod
    def plot(self) -> None:
        pass
