"""CLI stderr logging: level tags and ANSI-colored level segment when stderr is a TTY."""

from __future__ import annotations

import logging
import sys

_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[35m",
}
_RESET = "\033[0m"


class _ColorLevelFormatter(logging.Formatter):
    """Prefix each line with a level tag; color the tag when stderr is a TTY."""

    def __init__(self, fmt: str, datefmt: str | None, *, use_color: bool) -> None:
        super().__init__(fmt, datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if not self._use_color:
            return base
        color = _LEVEL_COLORS.get(record.levelno, "")
        if not color:
            return base
        idx = base.find(" [")
        if idx == -1:
            return f"{color}{base}{_RESET}"
        end = base.find("]", idx) + 1
        if end <= idx:
            return f"{color}{base}{_RESET}"
        return base[:idx] + color + base[idx:end] + _RESET + base[end:]


def configure_cli_logging(level: int) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler.setFormatter(
        _ColorLevelFormatter(fmt, datefmt="%H:%M:%S", use_color=use_color)
    )
    root.addHandler(handler)
    if level <= logging.DEBUG:
        logging.getLogger("asyncio").setLevel(logging.INFO)
