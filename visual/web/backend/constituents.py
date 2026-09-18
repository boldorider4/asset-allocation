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

# Broker icons served from visual/web/icons/ (copied by `make web`).
_BROKER_ICONS: dict[str, str] = {
    "oskar": "oskar.png",
    "scalable": "scalable.png",
    "traderepublic": "traderepublic.png",
    "check24": "check24.png",
    "alte-leipziger": "alte-leipziger.png",
}
_FALLBACK_MARK = ("?", "#6b6259")

# Max accepted characters per editable box (Name is plain text, not a box).
_SHORT_NAME_MAXLEN = 32
_FIGURE_MAXLEN = 16


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
        f'aria-label="{html.escape(name, quote=True)}" '
        f'maxlength="{_FIGURE_MAXLEN}" />'
    )


def _editable_text_box(value: Any, *, name: str, bucket: str, index: int) -> str:
    text = "" if value is None else str(value)
    return (
        f'<input class="cell-box editable text" value="{html.escape(text, quote=True)}" '
        f'data-field="{html.escape(name, quote=True)}" '
        f'data-bucket="{html.escape(bucket, quote=True)}" '
        f'data-index="{index}" '
        f'data-original="{html.escape(text, quote=True)}" '
        f'aria-label="{html.escape(name, quote=True)}" '
        f'maxlength="{_SHORT_NAME_MAXLEN}" />'
    )


def _locked_box(value: Any, *, name: str, extra_class: str = "") -> str:
    cls = "cell-box locked" + (f" {extra_class}" if extra_class else "")
    return (
        f'<span class="{cls}" data-field="{html.escape(name, quote=True)}">'
        f"{html.escape(_text(value), quote=False)}</span>"
    )


def _trash_button(*, name: str, bucket: str, index: int) -> str:
    label = f"Delete {name}"
    return (
        f'<button type="button" class="trash" '
        f'data-bucket="{html.escape(bucket, quote=True)}" '
        f'data-index="{index}" '
        f'title="{html.escape(label, quote=True)}" '
        f'aria-label="{html.escape(label, quote=True)}">'
        '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 6h18"/>'
        '<path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<line x1="10" y1="10" x2="10" y2="17"/>'
        '<line x1="12" y1="10" x2="12" y2="17"/>'
        '<line x1="14" y1="10" x2="14" y2="17"/>'
        "</svg></button>"
    )


def _has_isin(isin: Any) -> bool:
    """True when the row carries a usable ISIN (non-blank string)."""
    return isinstance(isin, str) and bool(isin.strip())


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
            # Without an ISIN there is no quote: shares and price render as
            # locked "-" boxes and only the value stays editable.
            no_quote = not _has_isin(isin)
            fields = updatable_fields(key, row)
            display.append(
                {
                    "name": row.get("name"),
                    "short_name": row.get("short_name"),
                    "isin": row.get("ISIN"),
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

    ``short_name`` and ``value`` are always editable; ``shares`` too when
    the row has an ISIN. ``ISIN`` and ``Price`` are never editable. The
    broker plays no role.
    """
    fields: set[str] = {"short_name", "value"}
    if _has_isin(row.get("ISIN")):
        fields.add("shares")
    return fields


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cached_price_for(cache: dict[str, Any] | None, isin: Any) -> float | None:
    if not isinstance(cache, dict) or not isinstance(isin, str) or not isin:
        return None
    entry = cache.get(isin)
    if not isinstance(entry, dict):
        return None
    price = entry.get("price")
    return float(price) if _is_number(price) else None


def _parse_stored_text(raw: Any) -> str:
    """Parse a posted label value; empty stays empty, junk raises ValueError."""
    if raw is None:
        return ""
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise ValueError(f"value must be text, got {type(raw).__name__}")
    if len(raw) > _SHORT_NAME_MAXLEN:
        raise ValueError(f"value must be at most {_SHORT_NAME_MAXLEN} characters")
    return raw


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
        if len(text) > _FIGURE_MAXLEN:
            raise ValueError(f"value must be at most {_FIGURE_MAXLEN} characters")
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
    assets_path: str | Path,
    bucket: str,
    index: int,
    field: str,
    raw_value: Any,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one editable cell into the assets file (atomic write).

    The sibling figure is recomputed immediately from the cached quote so
    the stored pair stays consistent: shares edits set
    ``value = shares × price``, value edits set ``shares = value / price``.
    Without a usable cached price the sibling is left untouched.
    ``short_name`` edits store text as-is with no recompute.

    Returns ``{"value": ..., "shares": ..., "short_name": ...,
    "recomputed": bool}``; ``recomputed`` is False only when a needed
    sibling recompute was impossible for lack of price.

    Raises ``ValueError`` for invalid addresses, locked fields, or junk
    values; ``OSError``/``json`` errors propagate for missing/corrupt files.
    """
    if field not in ("shares", "value", "short_name"):
        raise ValueError(f"field must be shares, value or short_name, got {field!r}")
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
    if field == "short_name":
        row[field] = _parse_stored_text(raw_value)
        recomputed = True
    else:
        value = _parse_stored_value(raw_value)
        row[field] = value
        price = _cached_price_for(cache, row.get("ISIN"))
        recomputed = True
        if price:
            if field == "shares":
                row["value"] = value * price
            elif field == "value" and _is_number(row.get("shares")):
                row["shares"] = value / price
        elif field == "shares" or _is_number(row.get("shares")):
            recomputed = False
    path = Path(assets_path)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    return {
        "value": row.get("value"),
        "shares": row.get("shares"),
        "short_name": row.get("short_name"),
        "recomputed": recomputed,
    }


def reorder_constituents(
    assets_path: str | Path, bucket: str, order: list[int]
) -> dict[str, Any]:
    """Persist a new row order for one bucket (atomic write).

    ``order`` must be an exact permutation of the row indices: every
    index exactly once. Anything else raises ``ValueError`` and leaves
    the file untouched, so a reorder can never drop or duplicate rows.

    Returns ``{"order": [...]}`` echoing the stored order.
    """
    if (
        not isinstance(order, list)
        or not order
        or any(isinstance(i, bool) or not isinstance(i, int) for i in order)
    ):
        raise ValueError(f"order must be a non-empty list of indices, got {order!r}")
    with open(assets_path, encoding="utf-8") as f:
        assets = json.load(f)
    if not isinstance(assets, dict):
        raise ValueError("assets root must be a JSON object")
    rows = assets.get(bucket)
    if not isinstance(rows, list):
        raise ValueError(f"unknown bucket: {bucket!r}")
    if sorted(order) != list(range(len(rows))):
        raise ValueError(f"order must list every row index once, got {order!r}")
    assets[bucket] = [rows[i] for i in order]
    path = Path(assets_path)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    return {"order": list(order)}


def delete_constituent(
    assets_path: str | Path, bucket: str, index: int
) -> dict[str, Any]:
    """Delete one row from a bucket (atomic write).

    Raises ``ValueError`` for unknown buckets or out-of-range indices
    and leaves the file untouched.

    Returns ``{"deleted": True, "rows": n}`` with the remaining row count.
    """
    with open(assets_path, encoding="utf-8") as f:
        assets = json.load(f)
    if not isinstance(assets, dict):
        raise ValueError("assets root must be a JSON object")
    rows = assets.get(bucket)
    if not isinstance(rows, list):
        raise ValueError(f"unknown bucket: {bucket!r}")
    if not isinstance(index, bool) and isinstance(index, int) and 0 <= index < len(rows):
        del rows[index]
    else:
        raise ValueError(f"row index out of range: {index!r}")
    path = Path(assets_path)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    return {"deleted": True, "rows": len(rows)}


def render_constituents_page(
    sections: list[tuple[str, list[dict[str, Any]]]],
    incognito: bool = False,
) -> str:
    """Render display sections as a full standalone HTML page."""
    overview_href = "/dashboard?incognito=true" if incognito else "/dashboard"
    parts: list[str] = []
    for index, (label, rows) in enumerate(sections):
        if index:
            parts.append('<hr class="section-rule" />')
        parts.append(f"<section><h2>{html.escape(label, quote=False)}</h2>")
        parts.append(
            '<div class="table-scroll">'
            "<table><thead><tr>"
            '<th class="grip-head" aria-hidden="true"></th>'
            "<th>Name</th><th>Group</th><th>ISIN</th>"
            "<th>Value</th><th>Shares</th><th>Price</th><th>Broker</th>"
            '<th class="trash-head" aria-hidden="true"></th>'
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
            trash_cell = _trash_button(
                name=name_text,
                bucket=row["bucket"],
                index=row["index"],
            )
            label_cell = _editable_text_box(
                row["short_name"],
                name="short_name",
                bucket=row["bucket"],
                index=row["index"],
            )
            isin_text = _text(row["isin"])
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
                f'<tr data-bucket="{html.escape(row["bucket"], quote=True)}" '
                f'data-index="{row["index"]}">'
                '<td class="grip-cell">'
                '<span class="grip" title="Drag to reorder" aria-hidden="true">≡</span>'
                "</td>"
                f'<td class="name" title="{html.escape(name_text, quote=True)}">'
                f"{html.escape(name_text, quote=False)}</td>"
                f"<td>{label_cell}</td>"
                f"<td>{html.escape(isin_text, quote=False)}</td>"
                f"<td>{value_cell} "
                '<span class="unit">Euro</span></td>'
                f"<td>{shares_cell}</td>"
                f"<td>{price_cell} "
                '<span class="unit">Euro</span></td>'
                f"<td>{_broker_mark(row['broker'])}</td>"
                f'<td class="trash-cell">{trash_cell}</td>'
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
      <div class="nav-buttons">
        <a id="overview-link" class="nav-button" href="{overview_href}">Dashboard</a>
      </div>
      <p id="update-status" class="status" hidden></p>
    </header>
    <main>{"".join(parts)}</main>
    <div id="update-overlay" class="overlay" hidden>
      <span>Updating charts…</span>
    </div>
    <script src="constituents.js"></script>
  </body>
</html>
"""
