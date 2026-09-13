from __future__ import annotations

from copy import deepcopy
from typing import TypedDict


class PieFactor(TypedDict):
    value: float | int
    unit: str


def merge_weights(
    left_data: dict[str, float],
    left_factor: PieFactor | None,
    right_data: dict[str, float],
    right_factor: PieFactor | None,
) -> tuple[float, float] | None:
    """Return (left, right) portfolio weights for weighted merge, or None for plain additive merge."""
    if left_factor is None and right_factor is None:
        return None
    if (
        left_factor is not None
        and right_factor is not None
        and left_factor["unit"] != right_factor["unit"]
    ):
        return None
    left = (
        float(left_factor["value"])
        if left_factor is not None
        else float(sum(left_data.values()))
    )
    right = (
        float(right_factor["value"])
        if right_factor is not None
        else float(sum(right_data.values()))
    )
    return (left, right)


def merge_factor(
    left_factor: PieFactor | None,
    right_factor: PieFactor | None,
    weights: tuple[float, float] | None,
) -> PieFactor | None:
    if weights is None:
        return None
    left, right = weights
    unit = (left_factor or right_factor)["unit"]
    return {"value": left + right, "unit": unit}


def merge_charts(
    left_data: dict[str, float],
    left_title: str | None,
    left_factor: PieFactor | None,
    right_data: dict[str, float],
    right_title: str | None,
    right_factor: PieFactor | None,
) -> tuple[dict[str, float], str | None, PieFactor | None]:
    """Merge two label→weight pies the way :class:`PieChart` historically did."""
    weights = merge_weights(left_data, left_factor, right_data, right_factor)
    if weights is None:
        merged: dict[str, float] = deepcopy(left_data)
        for k, v in right_data.items():
            merged[k] = merged.get(k, 0) + v
    else:
        left, right = weights
        total_w = left + right
        # Preserve insertion order: left keys first, then keys only in right.
        keys = list(dict.fromkeys([*left_data, *right_data]))
        merged = {
            k: (left_data.get(k, 0) * left + right_data.get(k, 0) * right) / total_w
            for k in keys
        }

    title_parts = [t for t in (left_title, right_title) if t]
    merged_title = " + ".join(title_parts) if title_parts else None
    return merged, merged_title, merge_factor(left_factor, right_factor, weights)


def merge_closing_title(
    left_closing: str | None,
    right_closing: str | None,
    merged_factor: PieFactor | None,
) -> str | None:
    """Prefer ``Value: {merged}`` when factors combined; otherwise join closing titles."""
    if merged_factor is not None:
        return "Value: {:.2f}".format(float(merged_factor["value"]))
    parts = [t for t in (left_closing, right_closing) if t]
    return " + ".join(parts) if parts else None
