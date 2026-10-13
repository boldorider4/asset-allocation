# SPDX-License-Identifier: AGPL-3.0-or-later
"""Label-aware wedge colors for the geo charts.

Wedges whose label contains ``Equity`` / ``Bond`` / ``Gold`` (at the
start or after a space, case-insensitive) share a family ramp so related
slices read as related: equity runs vibrant green through yellow to red,
bonds run dark to light blue, gold shares one yellow tint. Everything
else cycles through :data:`FALLBACK_PALETTE`, which deliberately holds
no green, blue, or yellow.

Assignment is deterministic: plain region wedges rank first (canonical
region order, so ``Equity US`` is greenest whenever present), then
grouped wedges alphabetically; fallback cycles in payload order. Hence
identical data always renders identical colors. Callers that pass
explicit ``colors=`` (e.g. the sector charts with ``SECTOR_PALETTE``)
bypass this entirely.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_EQUITY_RE = re.compile(r"(?:^| )equity", re.IGNORECASE)
_BOND_RE = re.compile(r"(?:^| )bond", re.IGNORECASE)
_GOLD_RE = re.compile(r"(?:^| )gold", re.IGNORECASE)

# Equity waypoints (vibrant green through yellow to red); interpolated
# piecewise-linearly in RGB by rank fraction.
_EQUITY_WAYPOINTS = ("#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#d73027")
_BLUE_DARK = "#1e4e8c"
_BLUE_LIGHT = "#aecbe8"
_GOLD = "#d9a521"

# Plain region keys rank greenest-first in canonical region order (kept
# here, not imported, to avoid a visual -> portfolio import cycle).
_PLAIN_REGION_ORDER = ("Equity US", "Equity Ex-US", "Equity Emrg. Markets")

# Wedge colors for labels in no family: oranges, reds, purples, pinks,
# browns, and grays — never green, blue, or yellow.
FALLBACK_PALETTE: tuple[str, ...] = (
    "#ff7f0e",
    "#d62728",
    "#8e44ad",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#ffbb78",
    "#c49c94",
)


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(round(max(0.0, min(1.0, c)) * 255) for c in rgb))


def _ramp(waypoints: Sequence[str], count: int) -> list[str]:
    """``count`` shades along ``waypoints`` (inclusive ends)."""
    if count <= 0:
        return []
    if count == 1:
        return [waypoints[0]]
    segments = len(waypoints) - 1
    rgb = [_hex_to_rgb(point) for point in waypoints]
    shades = []
    for rank in range(count):
        position = rank / (count - 1) * segments
        segment = min(int(position), segments - 1)
        fraction = position - segment
        start, end = rgb[segment], rgb[segment + 1]
        shades.append(
            _rgb_to_hex(
                tuple(s + (e - s) * fraction for s, e in zip(start, end))
            )
        )
    return shades


def _classify(label: str) -> str | None:
    """Family name for *label*: equity, bond, and gold checked in order."""
    if _EQUITY_RE.search(label):
        return "equity"
    if _BOND_RE.search(label):
        return "bond"
    if _GOLD_RE.search(label):
        return "gold"
    return None


def family_colors(labels: Sequence[str]) -> list[str]:
    """One color per label, in input order.

    Family wedges map alphabetically ranked labels onto their ramp;
    gold wedges share a single tint; the rest cycle through
    :data:`FALLBACK_PALETTE` in payload order.
    """
    names = [str(label) for label in labels]
    kinds = [_classify(name) for name in names]
    out: list[str | None] = [None] * len(names)

    def rank_key(i: int) -> tuple[int, object]:
        # Plain region wedges first (canonical region order, so Equity US
        # is greenest whenever present), then grouped ones alphabetically.
        try:
            return (0, _PLAIN_REGION_ORDER.index(names[i]))
        except ValueError:
            return (1, names[i])

    ordered = sorted(
        [i for i, kind_ in enumerate(kinds) if kind_ == "equity"],
        key=rank_key,
    )
    equity_shades = _ramp(list(_EQUITY_WAYPOINTS), len(ordered))
    for rank, i in enumerate(ordered):
        out[i] = equity_shades[rank]
    bond_ordered = sorted(
        [i for i, kind_ in enumerate(kinds) if kind_ == "bond"],
        key=lambda i: names[i],
    )
    bond_shades = _ramp([_BLUE_DARK, _BLUE_LIGHT], len(bond_ordered))
    for rank, i in enumerate(bond_ordered):
        out[i] = bond_shades[rank]
    for i, kind in enumerate(kinds):
        if kind == "gold":
            out[i] = _GOLD
    fallback = 0
    for i, kind in enumerate(kinds):
        if kind is None:
            out[i] = FALLBACK_PALETTE[fallback % len(FALLBACK_PALETTE)]
            fallback += 1
    return [color for color in out if color is not None]
