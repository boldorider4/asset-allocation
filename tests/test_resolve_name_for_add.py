# SPDX-License-Identifier: AGPL-3.0-or-later
"""The registry-first name cache for the constituents add flow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.repositories import IsinRegistry
from storage.wiring import isin_backend
from tests.store_helpers import read_isin
from visual.web.backend.constituents import resolve_name_for_add

_ISIN = "IE00BHZPJ239"


class TestResolveNameForAdd(unittest.TestCase):
    def test_registry_name_wins_without_a_lookup(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            registry = IsinRegistry(isin_backend(db_file))
            registry.open()
            registry.register_isin(_ISIN, name="Cached ETF")

            name = resolve_name_for_add(
                _ISIN,
                registry=registry,
                name_provider=lambda isin: calls.append(isin) or "Fresh ETF",
            )
            self.assertEqual(name, "Cached ETF")
            self.assertEqual(calls, [])

    def test_missing_name_is_fetched_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            registry = IsinRegistry(isin_backend(db_file))
            registry.open()

            name = resolve_name_for_add(
                _ISIN,
                registry=registry,
                name_provider=lambda isin: "Fresh ETF",
            )
            self.assertEqual(name, "Fresh ETF")
            row = read_isin(db_file)[_ISIN]
            self.assertEqual(row["name"], "Fresh ETF")

    def test_cached_name_blocks_the_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            registry = IsinRegistry(isin_backend(db_file))
            registry.open()
            registry.register_isin(_ISIN, name="Cached ETF")

            name = resolve_name_for_add(
                _ISIN,
                registry=registry,
                name_provider=lambda isin: "Different ETF",
            )
            self.assertEqual(name, "Cached ETF")
            row = read_isin(db_file)[_ISIN]
            self.assertEqual(row["name"], "Cached ETF")

    def test_provider_failure_leaves_the_registry_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            registry = IsinRegistry(isin_backend(db_file))
            registry.open()

            def boom(isin: str) -> str:
                raise RuntimeError("blocked")

            name = resolve_name_for_add(_ISIN, registry=registry, name_provider=boom)
            self.assertIsNone(name)
            self.assertNotIn(_ISIN, read_isin(db_file))

    def test_blank_isin_skips_everything(self) -> None:
        self.assertIsNone(resolve_name_for_add(None, name_provider=lambda _isin: "nope"))
        self.assertIsNone(resolve_name_for_add("  ", name_provider=lambda _isin: "nope"))
        self.assertIsNone(
            resolve_name_for_add("IE00BHZPJ2", name_provider=lambda _isin: "nope")
        )


if __name__ == "__main__":
    unittest.main()
