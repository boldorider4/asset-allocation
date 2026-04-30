from abc import ABC, abstractmethod

class Visual(ABC):
    def __init__(self, data: dict[str, float], title: str | None = None):
        self._data = data
        self._title = title

    @abstractmethod
    def plot(self) -> None:
        pass
