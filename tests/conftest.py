"""
Pytest hooks: ensure ``position.*`` loggers emit INFO on the console.

Without this, the root logger stays at WARNING and ``position.oskar_position``
INFO lines (e.g. Auth0 steps) are dropped before pytest's live log handler sees
them. ``log_cli_level`` in ``pyproject.toml`` does not lower child loggers.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(scope="session", autouse=True)
def _position_loggers_info() -> None:
    for name in ("position", "position.oskar_position", "tests", "tests.test_oskar_login"):
        logging.getLogger(name).setLevel(logging.INFO)
