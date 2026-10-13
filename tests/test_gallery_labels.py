# SPDX-License-Identifier: AGPL-3.0-or-later
"""Donut wedge labels: permissive fit, sub-1% gate, wrap-on-collision.

Wedges below 1% of the pie show no in-donut label (their legend row is
untouched); everything else fits generously, wraps onto multiple lines
when neighbors collide, and is never dropped for geometry. Behavior is
verified headlessly in /tmp/labeltest.js; these asserts pin the policy
constants and code shape.
"""

from __future__ import annotations

import unittest
from pathlib import Path


def _app_js() -> str:
    return (
        Path(__file__).resolve().parent.parent
        / "visual"
        / "web"
        / "frontend"
        / "app.js"
    ).read_text(encoding="utf-8")


class TestLabelPolicy(unittest.TestCase):
    def test_sub_one_percent_gate(self) -> None:
        js = _app_js()
        self.assertIn("WEDGE_LABEL_MIN_SHARE = 0.01", js)
        self.assertIn("share >= WEDGE_LABEL_MIN_SHARE", js)

    def test_permissive_initial_fit(self) -> None:
        js = _app_js()
        # Generous overflow cap and a low attempt floor...
        self.assertIn("chord * 2.5", js)
        self.assertIn("WEDGE_LABEL_MIN_WIDTH = 6", js)
        # ...with multi-line wrapping straight from the first pass.
        self.assertIn(
            "wrapLabel(label, maxWidth, fontSize, WEDGE_LABEL_MAX_LINES)", js
        )
        # An unfittable label still goes out unwrapped rather than
        # vanishing: only the share gate drops.
        self.assertIn("|| [label]", js)
        # The old last-space-only two-way split is gone.
        self.assertNotIn("splitLabel", js)

    def test_tight_collision_sprites(self) -> None:
        js = _app_js()
        self.assertIn("WEDGE_LABEL_PAD_X = 2", js)
        self.assertIn("WEDGE_LABEL_PAD_Y = 1", js)

    def test_overlap_resolves_without_dropping(self) -> None:
        js = _app_js()
        # More resolution rounds for crowded pies...
        self.assertIn("pass < 6", js)
        # ...and the resolver still never deletes entries from the list
        # it was given (unresolvable pairs keep both labels).
        resolved_fn = js.split("function resolveLabelOverlaps")[1].split(
            "function renderDonut"
        )[0]
        self.assertNotIn("splice", resolved_fn)


if __name__ == "__main__":
    unittest.main()
