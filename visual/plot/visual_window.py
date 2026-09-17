from __future__ import annotations

import logging
import sys

import matplotlib

# Staggered figure windows need a toolkit that exposes top-level window geometry.
# The default macosx backend does not; switch before pyplot is first imported.
if (
    sys.platform == "darwin"
    and "matplotlib.pyplot" not in sys.modules
    and matplotlib.get_backend().lower() == "macosx"
):
    matplotlib.use("tkagg")

from .visual import Visual

logger = logging.getLogger(__name__)


class VisualWindow(Visual):
    """Matplotlib window helpers for on-screen figures."""

    _figure_window_slot = 0
    _figure_window_px = 600
    _figure_grid_side = 3  # 3×3 placements, then repeat from (0, 0)

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

    @classmethod
    def finish_plots(cls) -> None:
        import matplotlib.pyplot as plt

        logger.info("Opening chart window (close window to exit)")
        plt.show()
