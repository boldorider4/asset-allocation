"""Static checks on the shipped systemd units."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestLiteUpdateUnit(unittest.TestCase):
    def _exec_start(self) -> str:
        text = (REPO_ROOT / "systemd" / "asalloc-lite-update.service").read_text(
            encoding="utf-8"
        )
        for line in text.splitlines():
            if line.startswith("ExecStart="):
                return line
        self.fail("no ExecStart in lite unit")

    def test_lite_runs_plain_update_with_clear_charts_only(self) -> None:
        cmd = self._exec_start()
        self.assertIn("asalloc update", cmd)
        self.assertIn("--plot-clear", cmd)
        self.assertIn("--log-level ERROR", cmd)
        for flag in (
            "--fetch-prices",
            "--fetch-geosplit",
            "--fetch-sectorsplit",
            "--fetch-oskar",
            "--fetch-scalable",
            "--fetch-tr",
            "--plot-incognito",
        ):
            self.assertNotIn(flag, cmd)

    def test_make_service_copies_but_never_enables_lite_unit(self) -> None:
        lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "service:")
        # Collect the tab-indented recipe block only.
        block: list[str] = []
        for line in lines[start + 1 :]:
            if line.startswith("\t"):
                block.append(line)
            elif line.strip() == "":
                continue
            else:
                break
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


if __name__ == "__main__":
    unittest.main()
