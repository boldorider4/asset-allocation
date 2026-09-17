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
    ("bond_portfolio", "Fixed Income"),
    ("commodity_portfolio", "Commodities / Inflation"),
    ("fixed_maturity_bond_portfolio", "Fixed Maturity"),
    ("cash_portfolio", "Cash"),
    ("pension_portfolio", "Pensions"),
)

_MISSING = "—"
_NO_QUOTE = "-"

_OSKAR = "oskar"

# Constituents without a tradeable quote: shares and price render as locked
# "-" boxes, like Oskar rows. Cash-likes match by name; pensions match by
# bucket; check24 matches by broker but only inside fixed maturity.
_CASHLIKE_NAMES = frozenset({"cash", "tagesgeld"})
_PENSION_BUCKET = "pension_portfolio"
_CHECK24 = "check24"

# Broker icons served from visual/web/icons/ (copied by `make web`).
_BROKER_ICONS: dict[str, str] = {
    "oskar": "oskar.png",
    "scalable": "scalable.png",
    "traderepublic": "traderepublic.png",
    "check24": "check24.png",
    "alte-leipziger": "alte-leipziger.png",
}
_FALLBACK_MARK = ("?", "#6b6259")


def _prettify_bucket(key: str) -> str:
    return key.replace("_portfolio", "").replace("_", " ").strip().title() or key


def _broker_mark(broker: str | None) -> str:
    key = (broker or "").strip().casefold()
    title = html.escape(broker or "", quote=True)
    icon = _BROKER_ICONS.get(key)
    if icon is not None:
        return (
            f'<span class="broker-mark" title="{title}">'
            f'<img src="icons/{icon}" width="22" height="22" alt="{title}" />'
            f"</span>"
        )
    letter, color = _FALLBACK_MARK
    return (
        f'<span class="broker-mark" title="{title}">'
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
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
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
            broker = row.get("broker") or ""
            broker_key = broker.strip().casefold()
            cashlike = isinstance(row.get("name"), str) and (
                row["name"].strip().casefold() in _CASHLIKE_NAMES
            )
            no_quote = (
                cashlike
                or key == _PENSION_BUCKET
                or broker_key == _CHECK24
            )
            display.append(
                {
                    "name": row.get("name"),
                    "value": row.get("value"),
                    "shares": row.get("shares"),
                    "price": price,
                    "broker": broker,
                    "editable_shares": broker != _OSKAR and not no_quote,
                    "editable_value": cashlike
                    or key == _PENSION_BUCKET
                    or broker_key == _CHECK24,
                    "no_quote": no_quote,
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
            '<div class="table-scroll">'
            "<table><thead><tr>"
            "<th>Name</th><th>Value</th><th>Shares</th><th>Price</th><th>Broker</th>"
            "</tr></thead><tbody>"
        )
        for row in rows:
            if row["no_quote"]:
                shares_cell = _locked_box(_NO_QUOTE, name="shares")
                price_cell = _locked_box(_NO_QUOTE, name="price")
            else:
                shares_cell = (
                    _editable_box(row["shares"], name="shares")
                    if row["editable_shares"]
                    else _locked_box(row["shares"], name="shares")
                )
                price_cell = _locked_box(row["price"], name="price")
            name_text = _text(row["name"])
            if row["editable_value"]:
                value_cell = _editable_box(row["value"], name="value")
            else:
                value_cell = _locked_box(row["value"], name="value")
            parts.append(
                "<tr>"
                f'<td class="name" title="{html.escape(name_text, quote=True)}">'
                f"{html.escape(name_text, quote=False)}</td>"
                f"<td>{value_cell} "
                '<span class="unit">Euro</span></td>'
                f"<td>{shares_cell}</td>"
                f"<td>{price_cell} "
                '<span class="unit">Euro</span></td>'
                f"<td>{_broker_mark(row['broker'])}</td>"
                "</tr>"
            )
        parts.append("</tbody></table></div></section>")
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
