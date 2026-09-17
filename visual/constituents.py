"""Constituents view: read-only rendering of the assets file.

Separated from the ``asalloc update`` execution path by design: this module
knows nothing about ``RuntimeContext``, scrapes, or portfolios. It reads
``assets.json`` and ``cache.json`` from disk on every call, so the page is
always fresh after an update. Nothing here persists anything anywhere.
"""

from __future__ import annotations

import html
import json
import os
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


def _editable_box(value: Any, *, name: str, bucket: str, index: int) -> str:
    text = "" if value is None else _text(value)
    return (
        f'<input class="cell-box editable" value="{html.escape(text, quote=True)}" '
        f'data-field="{html.escape(name, quote=True)}" '
        f'data-bucket="{html.escape(bucket, quote=True)}" '
        f'data-index="{index}" '
        f'data-original="{html.escape(text, quote=True)}" '
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
        for position, row in enumerate(rows):
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
            fields = updatable_fields(key, row)
            display.append(
                {
                    "name": row.get("name"),
                    "value": row.get("value"),
                    "shares": row.get("shares"),
                    "price": price,
                    "broker": broker,
                    "bucket": key,
                    "index": position,
                    "editable_shares": "shares" in fields,
                    "editable_value": "value" in fields,
                    "no_quote": no_quote,
                }
            )
        sections.append((labels.get(key, _prettify_bucket(key)), display))
    return sections


def updatable_fields(bucket_key: str, row: dict[str, Any]) -> set[str]:
    """Editable fields for a row, mirroring the render rules.

    ``shares`` unless the broker is Oskar or the row has no quote;
    ``value`` only for cash-like, pension, or check24 rows.
    """
    broker = row.get("broker") or ""
    broker_key = broker.strip().casefold()
    cashlike = isinstance(row.get("name"), str) and (
        row["name"].strip().casefold() in _CASHLIKE_NAMES
    )
    no_quote = (
        cashlike
        or bucket_key == _PENSION_BUCKET
        or broker_key == _CHECK24
    )
    fields: set[str] = set()
    if broker != _OSKAR and not no_quote:
        fields.add("shares")
    if cashlike or bucket_key == _PENSION_BUCKET or broker_key == _CHECK24:
        fields.add("value")
    return fields


def _parse_stored_value(raw: Any) -> float:
    """Parse a posted cell value; empty means 0.0, junk raises ValueError."""
    if raw is None:
        return 0.0
    if isinstance(raw, bool):
        raise ValueError("value must be a number")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return 0.0
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"value is not a number: {raw!r}") from None
    else:
        raise ValueError(f"value must be a number, got {type(raw).__name__}")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"value must be finite: {raw!r}")
    return value


def store_constituent_value(
    assets_path: str | Path, bucket: str, index: int, field: str, raw_value: Any
) -> float:
    """Persist one editable cell into the assets file (atomic write).

    Raises ``ValueError`` for invalid addresses, locked fields, or junk
    values; ``OSError``/``json`` errors propagate for missing/corrupt files.
    """
    if field not in ("shares", "value"):
        raise ValueError(f"field must be shares or value, got {field!r}")
    with open(assets_path, encoding="utf-8") as f:
        assets = json.load(f)
    if not isinstance(assets, dict):
        raise ValueError("assets root must be a JSON object")
    rows = assets.get(bucket)
    if not isinstance(rows, list):
        raise ValueError(f"unknown bucket: {bucket!r}")
    if not isinstance(index, bool) and isinstance(index, int) and 0 <= index < len(rows):
        row = rows[index]
    else:
        raise ValueError(f"row index out of range: {index!r}")
    if not isinstance(row, dict):
        raise ValueError(f"row {index} in {bucket!r} is not an object")
    if field not in updatable_fields(bucket, row):
        raise ValueError(f"field {field!r} is not editable for this row")
    value = _parse_stored_value(raw_value)
    row[field] = value
    path = Path(assets_path)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    return value
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
                    _editable_box(
                        row["shares"],
                        name="shares",
                        bucket=row["bucket"],
                        index=row["index"],
                    )
                    if row["editable_shares"]
                    else _locked_box(row["shares"], name="shares")
                )
                price_cell = _locked_box(row["price"], name="price")
            name_text = _text(row["name"])
            if row["editable_value"]:
                value_cell = _editable_box(
                    row["value"],
                    name="value",
                    bucket=row["bucket"],
                    index=row["index"],
                )
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
    <script src="constituents.js"></script>
  </body>
</html>
"""
