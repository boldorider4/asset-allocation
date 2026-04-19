from abc import ABC, abstractmethod

class Visual(ABC):
    @abstractmethod
    def plot(self, data: dict, *, title: str | None = None) -> None:
        pass
