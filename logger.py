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


class ColorLevelFormatter(logging.Formatter):
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


_STDERR_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_STDERR_DATEFMT = "%H:%M:%S"


def attach_color_stderr_handler_for_module(logger: logging.Logger) -> None:
    """Use :class:`ColorLevelFormatter` on ``logger`` when the root logger is not configured yet.

    If the root logger already has handlers (e.g. ``configure_cli_logging``, tests, or host
    app), child log records propagate and this is a no-op.
    """
    if logger.handlers:
        return
    root = logging.getLogger()
    if root.handlers:
        return
    use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        ColorLevelFormatter(
            _STDERR_LOG_FORMAT, datefmt=_STDERR_DATEFMT, use_color=use_color
        )
    )
    logger.addHandler(handler)
    logger.propagate = False


def configure_cli_logging(level: int) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    handler.setFormatter(
        ColorLevelFormatter(
            _STDERR_LOG_FORMAT, datefmt=_STDERR_DATEFMT, use_color=use_color
        )
    )
    root.addHandler(handler)
    if level <= logging.DEBUG:
        logging.getLogger("asyncio").setLevel(logging.INFO)
