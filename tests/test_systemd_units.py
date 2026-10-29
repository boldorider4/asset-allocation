# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks on the shipped systemd units."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _service_recipe() -> list[str]:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "service:")
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("\t"):
            block.append(line)
        elif line.strip() == "":
            continue
        else:
            break
    return block


class TestLiteUpdateUnit(unittest.TestCase):
    def _exec_start(self) -> str:
        text = (REPO_ROOT / "systemd" / "asalloc-lite-update.service").read_text(
            encoding="utf-8"
        )
        for line in text.splitlines():
            if line.startswith("ExecStart="):
                return line
        self.fail("no ExecStart in lite unit")

    def test_lite_runs_plain_update_with_both_chart_modes(self) -> None:
        cmd = self._exec_start()
        self.assertIn("asalloc update", cmd)
        self.assertIn("--plot web", cmd)
        self.assertNotIn("--plot-clear", cmd)
        self.assertNotIn("--plot-incognito", cmd)
        self.assertIn("--log-level ERROR", cmd)
        # The same database as the server: one file holds every store
        # (assets, cache, isin registry, users, sessions), so the
        # endpoint update and the timer update cannot end up writing
        # to different caches and missing each other's writes.
        self.assertIn("--db-file %h/.local/asalloc/asalloc.db", cmd)
        # The per-store files are the json backend's: the database
        # backend has one file for all of them.
        self.assertNotIn("--assets-file", cmd)
        self.assertNotIn("--cache-file", cmd)
        for flag in (
            "--fetch-prices",
            "--fetch-geosplit",
            "--fetch-sectorsplit",
            "--fetch-oskar",
            "--fetch-scalable",
            "--fetch-tr",
        ):
            self.assertNotIn(flag, cmd)

    def test_full_update_unit_shares_cache_with_endpoint(self) -> None:
        text = (REPO_ROOT / "systemd" / "asalloc-update.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("--db-file %h/.local/asalloc/asalloc.db", text)
        self.assertNotIn("--assets-file", text)
        self.assertNotIn("--cache-file", text)

    def test_make_service_copies_but_never_enables_lite_unit(self) -> None:
        block = _service_recipe()
        self.assertIn("systemd/asalloc-lite-update.service", "\n".join(block))
        cp_lines = [
            line
            for line in block
            if "lite-update" in line and line.strip().startswith("cp ")
        ]
        self.assertEqual(len(cp_lines), 1)
        for line in block:
            stripped = line.strip()
            if stripped.startswith("@echo"):
                continue
            if "enable" in line or "start" in line:
                self.assertNotIn("lite", line)


class TestCurateUnits(unittest.TestCase):
    _UNITS = (
        "asalloc-curate-all-users.service",
        "asalloc-curate-default-user.service",
    )

    def _text(self, name: str) -> str:
        return (REPO_ROOT / "systemd" / name).read_text(encoding="utf-8")

    def test_units_return_to_the_shell_before_curation_finishes(self) -> None:
        for name in self._UNITS:
            text = self._text(name)
            self.assertIn("Type=exec", text)
            self.assertNotIn("Type=oneshot", text)
            self.assertIn("asalloc curate", text)

    def test_default_user_unit_selects_the_user_explicitly(self) -> None:
        text = self._text("asalloc-curate-default-user.service")
        self.assertIn("--user default", text)

    def test_all_users_unit_does_not_select_a_user(self) -> None:
        text = self._text("asalloc-curate-all-users.service")
        self.assertNotIn("--user", text)

    def test_make_service_copies_and_enables_curate_timers(self) -> None:
        block = _service_recipe()
        recipe = "\n".join(block)
        self.assertIn("systemd/asalloc-curate-all-users.service", recipe)
        self.assertIn("systemd/asalloc-curate-all-users.timer", recipe)
        self.assertIn("systemd/asalloc-curate-default-user.service", recipe)
        self.assertIn("systemd/asalloc-curate-default-user.timer", recipe)
        self.assertIn(
            "systemctl --user enable --now asalloc-curate-all-users.timer",
            recipe,
        )
        self.assertIn(
            "systemctl --user enable --now asalloc-curate-default-user.timer",
            recipe,
        )


class TestServeUnit(unittest.TestCase):
    """The server serves the one database (and nothing else)."""

    def _text(self) -> str:
        return (REPO_ROOT / "systemd" / "asalloc-serve.service").read_text(
            encoding="utf-8"
        )

    def test_the_server_uses_the_one_database(self) -> None:
        text = self._text()
        self.assertIn("asalloc serve", text)
        self.assertIn("--db-file %h/.local/asalloc/asalloc.db", text)

    def test_the_server_names_no_json_store(self) -> None:
        # The database holds every store; a per-store file would point
        # the server at a second, empty set of stores.
        text = self._text()
        for flag in ("--assets-file", "--cache-file", "--users-file"):
            self.assertNotIn(flag, text)


if __name__ == "__main__":
    unittest.main()
