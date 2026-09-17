"""Constituents view: read-only rendering of the assets file.

Separated from the ``asalloc update`` execution path by design: this module
knows nothing about ``RuntimeContext``, scrapes, or portfolios. It reads
``assets.json`` and ``cache.json`` from disk on every call, so the page is
always fresh after an update. Nothing here persists anything anywhere.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

# Canonical section order with display labels; unknown bucket keys are
# appended after these, prettified.
SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("equity_portfolio", "Equity"),
    ("bond_portfolio", "Bonds"),
    ("fixed_maturity_bond_portfolio", "Fixed Maturity"),
    ("cash_portfolio", "Cash"),
    ("commodity_portfolio", "Commodities"),
    ("pension_portfolio", "Pension"),
)

_MISSING = "—"

_OSKAR = "oskar"

# Placeholder broker marks (inline SVG, swappable for real uploads later).
_BROKER_MARKS: dict[str, tuple[str, str]] = {
    "oskar": ("O", "#7c5cbf"),
    "scalable": ("S", "#2e9e6b"),
    "traderepublic": ("TR", "#c8a24a"),
    "check24": ("C24", "#1a5fb4"),
    "alte-leipziger": ("AL", "#0b2a4a"),
}
_FALLBACK_MARK = ("?", "#6b6259")


def _prettify_bucket(key: str) -> str:
    return key.replace("_portfolio", "").replace("_", " ").strip().title() or key


def _broker_mark(broker: str | None) -> str:
    letter, color = _BROKER_MARKS.get(
        (broker or "").strip().casefold(), _FALLBACK_MARK
    )
    return (
        f'<span class="broker-mark" title="{html.escape(broker or "", quote=True)}">'
        f'<svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">'
        f'<rect x="1" y="1" width="30" height="30" rx="7" fill="{color}"/>'
        f'<text x="16" y="21" text-anchor="middle" font-size="13" '
        f'font-family="Georgia, serif" fill="#ffffff">{html.escape(letter)}</text>'
        f"</svg></span>"
    )


def _text(value: Any) -> str:
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Two decimals tops: round, then drop trailing zeros.
        return f"{value:.2f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _editable_box(value: Any, *, name: str) -> str:
    text = "" if value is None else _text(value)
    return (
        f'<input class="cell-box editable" value="{html.escape(text, quote=True)}" '
        f'data-field="{html.escape(name, quote=True)}" '
        f'aria-label="{html.escape(name, quote=True)}" />'
    )


def _locked_box(value: Any, *, name: str) -> str:
    return (
        f'<span class="cell-box locked" data-field="{html.escape(name, quote=True)}">'
        f"{html.escape(_text(value), quote=False)}</span>"
    )


def load_constituents(
    assets_path: str | Path, cache_path: str | Path
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Read assets + cache from disk into ordered display sections."""
    with open(assets_path, encoding="utf-8") as f:
        assets = json.load(f)
    if not isinstance(assets, dict):
        raise ValueError("assets root must be a JSON object")
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}

    known = {key for key, _ in SECTION_ORDER}
    ordered_keys = [key for key, _ in SECTION_ORDER if key in assets]
    ordered_keys.extend(k for k in assets if k not in known)

    labels = {key: label for key, label in SECTION_ORDER}
    sections: list[tuple[str, list[dict[str, Any]]]] = []
    for key in ordered_keys:
        rows = assets.get(key) or []
        if not isinstance(rows, list):
            continue
        display: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            isin = row.get("ISIN")
            price = None
            if isinstance(isin, str) and isin:
                entry = cache.get(isin)
                if isinstance(entry, dict):
                    price = entry.get("price")
            display.append(
                {
                    "name": row.get("name"),
                    "value": row.get("value"),
                    "shares": row.get("shares"),
                    "price": price,
                    "broker": row.get("broker"),
                    "editable_shares": (row.get("broker") or "") != _OSKAR,
                }
            )
        sections.append((labels.get(key, _prettify_bucket(key)), display))
    return sections


def render_constituents_page(
    sections: list[tuple[str, list[dict[str, Any]]]],
) -> str:
    """Render display sections as a full standalone HTML page."""
    parts: list[str] = []
    for index, (label, rows) in enumerate(sections):
        if index:
            parts.append('<hr class="section-rule" />')
        parts.append(f"<section><h2>{html.escape(label, quote=False)}</h2>")
        parts.append(
            "<table><thead><tr>"
            "<th>Name</th><th>Value</th><th>Shares</th><th>Price</th><th>Broker</th>"
            "</tr></thead><tbody>"
        )
        for row in rows:
            shares_cell = (
                _editable_box(row["shares"], name="shares")
                if row["editable_shares"]
                else _locked_box(row["shares"], name="shares")
            )
            name_text = _text(row["name"])
            parts.append(
                "<tr>"
                f'<td class="name" title="{html.escape(name_text, quote=True)}">'
                f"{html.escape(name_text, quote=False)}</td>"
                f"<td>{_locked_box(row['value'], name='value')}</td>"
                f"<td>{shares_cell}</td>"
                f"<td>{_locked_box(row['price'], name='price')}</td>"
                f"<td>{_broker_mark(row['broker'])}</td>"
                "</tr>"
            )
        parts.append("</tbody></table></section>")
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Constituents</title>
    <link rel="stylesheet" href="styles.css" />
  </head>
  <body>
    <header class="masthead">
      <h1>Constituents</h1>
      <a class="nav-button" href="/dashboard">Overview</a>
    </header>
    <main>{"".join(parts)}</main>
  </body>
</html>
"""
