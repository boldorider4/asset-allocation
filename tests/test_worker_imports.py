# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spawned update-worker imports resolve in a fresh interpreter.

Regression test: ``POST /api/update`` runs ``_update_worker`` in a
``spawn`` child whose ``sys.path`` is inherited from the serve process.
``portfolio``/``scrape`` used to be namespace packages (no
``__init__.py``), resolving only via the editable-install placeholder
hack — a serve process started before a reinstall produced children
failing with ``ModuleNotFoundError: No module named 'portfolio'``
(worker exit 1, dashboard "Update failed"). Both packages are regular
now; this pins that invariant plus the end-to-end spawned import.

Run from repo root::

    pytest tests/test_worker_imports.py -v
"""

from __future__ import annotations

import multiprocessing
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _spawn_child_imports(conn) -> None:
    """Child entry point: import the full endpoint-worker chain."""
    try:
        from cli.update import main as run_update  # noqa: F401

        import portfolio.regional_portfolio  # noqa: F401
        import scrape.scalable  # noqa: F401
        import scrape.oskar  # noqa: F401
        import scrape.traderepublic  # noqa: F401
        conn.send({"ok": True, "error": ""})
    except BaseException as exc:
        # BaseException on purpose: a SystemExit here is exactly the
        # worker-exit-1 class this guards against.
        conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()


class TestWorkerPackagesRegular(unittest.TestCase):
    def test_portfolio_and_scrape_have_init(self) -> None:
        for package in ("portfolio", "scrape"):
            init = REPO_ROOT / package / "__init__.py"
            self.assertTrue(init.is_file(), f"{package}/__init__.py missing")

    def test_portfolio_and_scrape_not_namespace(self) -> None:
        import portfolio
        import scrape

        for mod in (portfolio, scrape):
            self.assertIsNotNone(
                mod.__spec__.loader, f"{mod.__name__} resolved as namespace package"
            )
            for entry in (mod.__path__ or []):
                self.assertNotIn("__path_hook__", entry)


class TestSpawnedWorkerImports(unittest.TestCase):
    def test_spawn_child_imports_worker_chain(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        recv, send = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_spawn_child_imports, args=(send,), daemon=True)
        proc.start()
        proc.join(timeout=180)
        self.assertFalse(proc.is_alive(), "spawned import child hung")
        self.assertEqual(proc.exitcode, 0, "spawned import child crashed")
        result = recv.recv() if recv.poll(30) else None
        recv.close()
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("ok"), result.get("error"))


if __name__ == "__main__":
    unittest.main()
