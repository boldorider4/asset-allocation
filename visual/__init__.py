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

__all__ = ["Visual"]
