from __future__ import annotations

from abc import ABC, abstractmethod

import matplotlib


class Visual(ABC):
    """Base for small matplotlib figures; subclasses should call `_stagger_figure_window`."""

    _figure_window_slot = 0
    _figure_window_px = 600
    _figure_grid_side = 3  # 3×3 placements, then repeat from (0, 0)

    def __init__(self, data: dict[str, float], title: str | None = None):
        self._data = data
        self._title = title

    @classmethod
    def _stagger_figure_window(cls, fig) -> None:
        """600×600 px windows: (0,0), (600,0), (1200,0), then next row (0,600), …; ninth (1200,1200); repeat."""
        g = cls._figure_grid_side
        n = g * g
        slot = cls._figure_window_slot % n
        cls._figure_window_slot += 1
        row = slot // g
        col = slot % g
        s = cls._figure_window_px
        x, y = col * s, row * s

        mgr = fig.canvas.manager
        if mgr is None:
            return
        win = getattr(mgr, "window", None)
        if win is None:
            return
        backend = matplotlib.get_backend().lower()
        w, h = int(s), int(s)
        try:
            if "tk" in backend:
                win.wm_geometry(f"{w}x{h}+{x}+{y}")
            elif "wx" in backend:
                win.SetSize((w, h))
                win.SetPosition((int(x), int(y)))
            else:
                win.setGeometry(int(x), int(y), w, h)
        except Exception:
            pass

    @abstractmethod
    def plot(
        self,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
    ) -> None:
        pass
