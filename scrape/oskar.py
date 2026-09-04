"""
OSKAR portfolio positions (JustETF pricing) plus a Playwright-based client for the
logged-in cockpit «Aktuelle Gewichtung» ETF list.

Sign in manually in the browser when prompted. After ``pip install`` run
``playwright install chromium`` once so the browser binary is available.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from logger import attach_color_stderr_handler_for_module
from common import (
    BOND_PORTFOLIO,
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
)
from utils import portfolio as global_portfolio

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

global global_oskar_etfs
global_oskar_etfs: dict[str, OskarEtf] = {}

_OSKAR = "oskar"

_DASHBOARD_URL = "https://mein.oskar.de/cockpit/dashboard"
_DASHBOARD_PATH = urlparse(_DASHBOARD_URL).path.rstrip("/")

# mein.oskar.de rejects HeadlessChrome with a blank-page redirect; use a normal Chrome UA.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# A minimized / occluded window is throttled by default, which stalls the consent
# iframe and the cockpit SPA while the scrape keeps clicking.
_CHROMIUM_LAUNCH_ARGS = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)

_ISIN_STRICT = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_DE_PERCENT_RE = re.compile(r"([\d][\d.,]*)\s*%")
_DE_EURO_RE = re.compile(r"([\d][\d.,]*)\s*€")


@dataclass(frozen=True)
class OskarEtf:
    """One ETF line from «Aktuelle Gewichtung» (leaf row with an ISIN)."""

    isin: str
    name: str
    weight_pct: float | None
    value_eur: float | None
    raw_text: str
    category: str = ""


def _parse_de_number(num: str) -> float:
    """German number: thousands '.', decimal ','."""
    s = num.strip().replace(".", "").replace(",", ".")
    return float(s)


def _parse_row_blob(blob: str, isin: str) -> tuple[str, float | None, float | None]:
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    name = ""
    idx = next((i for i, ln in enumerate(lines) if ln == isin), -1)
    if idx > 0:
        name = lines[idx - 1]
    elif idx < 0 and isin in blob:
        pos = blob.find(isin)
        if pos > 0:
            name = blob[:pos].strip()

    weight: float | None = None
    value_eur: float | None = None
    tail = lines[idx + 1 :] if idx >= 0 else lines

    for ln in tail:
        pm = _DE_PERCENT_RE.search(ln)
        if pm and weight is None:
            try:
                weight = _parse_de_number(pm.group(1))
            except ValueError:
                pass
        em = _DE_EURO_RE.search(ln)
        if em and value_eur is None:
            try:
                value_eur = _parse_de_number(em.group(1))
            except ValueError:
                pass

    return name, weight, value_eur


def _parse_tagesgeld_blob(blob: str) -> tuple[float | None, float | None]:
    weight: float | None = None
    value_eur: float | None = None
    pm = _DE_PERCENT_RE.search(blob)
    if pm:
        try:
            weight = _parse_de_number(pm.group(1))
        except ValueError:
            pass
    em = _DE_EURO_RE.search(blob)
    if em:
        try:
            value_eur = _parse_de_number(em.group(1))
        except ValueError:
            pass
    return weight, value_eur


def _is_oskar_tagesgeld_fetch_row(*, isin: str, category: str, subcategory: str, raw_text: str) -> bool:
    if isin == _OSKAR_TAGESGELD_FETCH_KEY:
        return True
    if _ISIN_STRICT.match(isin):
        return False
    return (
        category == _OSKAR_CATEGORY_TAGESGELD
        or subcategory == _OSKAR_CATEGORY_TAGESGELD
        or _OSKAR_CATEGORY_TAGESGELD in raw_text
    )


def _try_oskar_logout(page: Any, *, timeout_ms: int = 15_000) -> None:
    """Best-effort: open account menu if needed, then click «Ausloggen»."""
    logger.info("OSKAR logout: looking for Ausloggen")

    for scope in page.frames:
        try:
            loc = scope.get_by_text("Ausloggen", exact=True)
            if loc.count() == 0:
                continue
            el = loc.first
            if el.is_visible():
                el.click(timeout=timeout_ms)
                page.wait_for_timeout(800)
                logger.info("OSKAR logout: clicked Ausloggen (direct text, frame)")
                return
        except Exception:
            continue

    for pat in (re.compile(r"^\s*Ausloggen\s*$", re.I), re.compile(r"Ausloggen", re.I)):
        for role in ("menuitem", "button", "link"):
            loc = page.get_by_role(role, name=pat)
            if loc.count() == 0:
                continue
            try:
                el = loc.first
                if el.is_visible():
                    el.click(timeout=timeout_ms)
                    page.wait_for_timeout(800)
                    logger.info("OSKAR logout: clicked %s (name match)", role)
                    return
            except Exception:
                continue
    for sel in (
        '[role="menuitem"]:has-text("Ausloggen")',
        'button:has-text("Ausloggen")',
        'a:has-text("Ausloggen")',
    ):
        loc = page.locator(sel)
        if loc.count() == 0:
            continue
        try:
            loc.first.click(timeout=timeout_ms)
            page.wait_for_timeout(800)
            logger.info("OSKAR logout: clicked control matching %s", sel)
            return
        except Exception:
            continue
    logger.warning("OSKAR logout: no Ausloggen control found (session may stay active)")


def _try_dismiss_sourcepoint_cookie_banner(page: Any, *, timeout_ms: int = 20_000) -> None:
    """
    Sourcepoint (``cdn.privacy-mgmt.com``) consent iframe often sits above the cockpit;
    dismiss it so tabs / «Ausloggen» in the main shell respond to clicks.
    """
    per = min(8_000, timeout_ms)
    for label, pat in (
        ("alle ablehnen", re.compile(r"alle\s*ablehnen", re.I)),
        ("Weiter", re.compile(r"^Weiter$", re.I)),
        ("Alle akzeptieren", re.compile(r"alle\s*akzeptieren", re.I)),
    ):
        for fr in page.frames:
            u = getattr(fr, "url", "") or ""
            if "privacy-mgmt.com" not in u:
                continue
            try:
                loc = fr.get_by_role("button", name=pat)
                if loc.count() == 0:
                    continue
                el = loc.first
                if el.is_visible():
                    el.click(timeout=per)
                    page.wait_for_timeout(900)
                    logger.info("OSKAR: dismissed cookie banner (%s)", label)
                    return
            except Exception:
                continue


def _on_cockpit_dashboard(page: Any) -> bool:
    """
    True once the browser sits on ``mein.oskar.de/cockpit/dashboard``. Auth0 only
    redirects there after a successful login, so the URL alone tells logged-in from
    logged-out — no page wording involved.
    """
    try:
        parts = urlparse(page.url or "")
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    return host.endswith("mein.oskar.de") and parts.path.rstrip("/") == _DASHBOARD_PATH


def _wait_for_cockpit_dashboard(page: Any, *, timeout_ms: int) -> None:
    """Poll until Auth0 has redirected to the cockpit dashboard URL."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if _on_cockpit_dashboard(page):
            logger.info("OSKAR: cockpit dashboard reached url=%s", page.url)
            return
        page.wait_for_timeout(450)
    raise RuntimeError(
        f"OSKAR: timed out waiting for the redirect to {_DASHBOARD_URL} (url={page.url})."
    )


def _hide_headed_browser_window(page: Any) -> None:
    """
    Minimize the Chromium window after manual login so scrape clicks stay on the
    same session (cookies + SPA) without leaving the window on screen.

    Playwright cannot switch a live browser from headed to headless; a relaunch
    would drop the Auth0 session. CDP ``Browser.setWindowBounds`` keeps the
    process running. Best-effort: headless or unsupported hosts just log.
    """
    try:
        cdp = page.context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )
        logger.info("OSKAR: minimized headed browser; scrape continues in the same session")
    except Exception as exc:
        logger.debug("OSKAR: could not minimize headed browser: %s", exc)


def _wait_for_manual_oskar_login(page: Any, *, timeout_ms: int) -> None:
    """
    Block until a human has finished Auth0 in the **headed** browser, i.e. until the
    redirect to the cockpit dashboard lands. A typo simply keeps the wait running,
    because a rejected login never leaves the Auth0 URL.
    """
    logger.warning(
        "OSKAR manual login: complete Auth0 in the browser window (credentials + Continue / "
        "Anmelden). Waiting up to %.0f s for the redirect to %s…",
        timeout_ms / 1000,
        _DASHBOARD_URL,
    )
    _wait_for_cockpit_dashboard(page, timeout_ms=timeout_ms)


def _click_allocation_tab(page: Any, *, timeout_ms: int) -> None:
    """
    Open the cockpit asset-allocation view (German UI: «Gewichtung» / «Aktuelle Gewichtung»).
    Cockpit may host tabs in a child ``frame`` or shadow root; Playwright's role/text
    locators are evaluated per frame. Polls until ``timeout_ms`` because tabs often load
    after the cockpit shell on slow connections.
    """
    attempts = [
        ("tab-regex-gewichtung", lambda s: s.get_by_role("tab", name=re.compile(r"gewichtung", re.I))),
        ("link-regex-gewichtung", lambda s: s.get_by_role("link", name=re.compile(r"gewichtung", re.I))),
        ("text-regex-aktuelle-gewichtung", lambda s: s.get_by_text(re.compile(r"Aktuelle\s*Gewichtung", re.I))),
        ("text-exact-aktuelle-gewichtung", lambda s: s.get_by_text("Aktuelle Gewichtung", exact=True)),
        ("text-regex-gewichtung-word", lambda s: s.get_by_text(re.compile(r"\bGewichtung\b", re.I))),
    ]
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        per_attempt = min(remaining_ms, 60_000)
        for fr in page.frames:
            try:
                fr_url = getattr(fr, "url", "") or ""
            except Exception:
                fr_url = ""
            for label, factory in attempts:
                try:
                    loc = factory(fr)
                except Exception as exc:
                    logger.debug(
                        "OSKAR: allocation tab scope %s factory %s: %s", fr_url, label, exc
                    )
                    continue
                if loc.count() == 0:
                    continue
                try:
                    first = loc.first
                    first.wait_for(state="visible", timeout=per_attempt)
                    first.click(timeout=per_attempt)
                    logger.info(
                        "OSKAR: opened allocation view via %s (frame=%s)", label, fr_url[:120]
                    )
                    page.wait_for_timeout(800)
                    return
                except Exception as exc:
                    logger.debug(
                        "OSKAR: allocation tab attempt %s frame=%s: %s", label, fr_url[:80], exc
                    )
        _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=2_000)
        page.wait_for_timeout(500)
    raise RuntimeError(
        "OSKAR: could not activate Gewichtung tab."
    )


def _wait_for_allocation_scope(page: Any, *, timeout_ms: int) -> Any:
    """Return the frame/page whose DOM contains the allocation widget."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_urls: list[str] = []
    while time.monotonic() < deadline:
        last_urls = []
        for fr in page.frames:
            try:
                fr_url = getattr(fr, "url", "") or ""
                last_urls.append(fr_url[:120])
                root = fr.locator(".asset-allocation").first
                if root.count() > 0:
                    root.wait_for(state="visible", timeout=min(5_000, timeout_ms))
                    logger.info("OSKAR: allocation widget detected (frame=%s)", fr_url[:120])
                    return fr
            except Exception as exc:
                logger.debug(
                    "OSKAR: allocation widget wait skipped frame url=%s err=%s",
                    (getattr(fr, "url", "") or "")[:100],
                    exc,
                )
        _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=2_000)
        page.wait_for_timeout(500)
    raise RuntimeError(
        "OSKAR: timed out waiting for .asset-allocation after opening Gewichtung "
        f"(frames={last_urls})."
    )


_OSKAR_CATEGORY_AKTIEN = "Aktien"
_OSKAR_CATEGORY_ANLEIHEN = "Anleihen"
_OSKAR_CATEGORY_INFLATIONSGESCHUTZT = "Inflationsgeschützt"
_OSKAR_CATEGORY_TAGESGELD = "Tagesgeld"
_OSKAR_TAGESGELD_FETCH_KEY = "__OSKAR_TAGESGELD__"

_OSKAR_CATEGORY_TO_PORTFOLIO: dict[str, str] = {
    _OSKAR_CATEGORY_AKTIEN: EQUITY_PORTFOLIO,
    _OSKAR_CATEGORY_ANLEIHEN: BOND_PORTFOLIO,
    _OSKAR_CATEGORY_INFLATIONSGESCHUTZT: COMMODITY_PORTFOLIO,
    _OSKAR_CATEGORY_TAGESGELD: CASH_PORTFOLIO,
}
_DEFAULT_OSKAR_PORTFOLIO_BUCKET = EQUITY_PORTFOLIO


# Expand top levels and then discover each sub-bucket
_OSKAR_ALLOCATION_BUCKETS = {
    _OSKAR_CATEGORY_AKTIEN: (
        "Aktien Small Cap",
        "Aktien Europa", "Aktien Japan",
        "Aktien Schwellenländer",
        "Aktien Asien und pazifischer Raum",
        "Aktien USA"
        ),
    _OSKAR_CATEGORY_ANLEIHEN: (
        "Anleihen Global",
        "Anleihen Schwellenländer"
        ),
    _OSKAR_CATEGORY_INFLATIONSGESCHUTZT: ("Gold", "Anleihen inflationsgeschützt"),
    _OSKAR_CATEGORY_TAGESGELD: (_OSKAR_CATEGORY_TAGESGELD,),
}


def _oskar_category_from_row(*, category: str = "", subcategory: str = "") -> str:
    """
    Level-1 category for portfolio mapping. ``subcategory`` (level-2 label) can
    backfill when merge snapshots lose the level-1 row after another bucket opens.
    """
    category = category.strip()
    if category:
        return category
    subcategory = subcategory.strip()
    if not subcategory:
        return ""
    for top_label, sub_labels in _OSKAR_ALLOCATION_BUCKETS.items():
        if subcategory in sub_labels:
            return top_label
    return ""


_CLICK_MIRROR_FOR_ROW_IN_BUCKET_JS = r"""
([topLabel, rowLabel]) => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const root = document.querySelector(".asset-allocation");
    if (!root) return { clicked: false, rowLabel: null };
    const rows = [...root.querySelectorAll("div.row")].filter((r) =>
        ["level1", "level2", "level3"].some((lv) => r.classList.contains(lv))
    );
    let i0 = -1;
    const L = norm(topLabel);
    for (let i = 0; i < rows.length; i++) {
        const a = rows[i].querySelector(".asset");
        const t = norm(a ? a.textContent : "");
        if (rows[i].classList.contains("level1") && t === L) {
            i0 = i;
            break;
        }
    }
    if (i0 < 0) return { clicked: false, rowLabel: null };
    let end = rows.length;
    for (let j = i0 + 1; j < rows.length; j++) {
        if (rows[j].classList.contains("level1")) {
            end = j;
            break;
        }
    }
    const RL = norm(rowLabel);
    for (let j = i0 + 1; j < end; j++) {
        const r = rows[j];
        const asset = r.querySelector(".asset");
        const t = norm(asset ? asset.textContent : "");
        if (t !== RL) continue;
        const em = r.querySelector("em.fa-angle-right.mirror");
        if (em && em.offsetParent) {
            em.click();
            return { clicked: true, rowLabel: t || null };
        }
        return { clicked: false, rowLabel: t || null };
    }
    return { clicked: false, rowLabel: null };
}
"""


def _expand_oskar_allocation_bucket(
    page: Any,
    allocation_scope: Any,
    top_label: str,
    sub_labels: tuple[str, ...],
    *,
    merge_state: dict[str, Any] | None = None,
) -> None:
    """
    Open one level-1 bucket, then expand each named sub-row in order (see
    :data:`_OSKAR_ALLOCATION_BUCKETS`). Skips missing buckets or rows; rows without
    a visible collapse chevron are left as-is.
    """
    root = allocation_scope.locator(".asset-allocation").first
    if root.count() == 0:
        logger.warning("OSKAR expand: no .asset-allocation on page")
        return
    top_row = root.locator("div.row.level1").filter(
        has=allocation_scope.locator(
            "div.asset",
            has_text=re.compile(rf"^\s*{re.escape(top_label)}\s*$", re.I),
        )
    )
    if top_row.count() == 0:
        logger.info("OSKAR expand: skip missing top bucket %r", top_label)
        return
    tr = top_row.first
    em_top = tr.locator("em.fa-angle-right.mirror")
    try:
        if em_top.count() > 0 and em_top.first.is_visible():
            em_top.first.click(timeout=2_000)
            page.wait_for_timeout(450)
            logger.info("OSKAR expand: opened top bucket %r", top_label)
    except Exception as exc:
        logger.debug("OSKAR expand: top %r chevron skip: %s", top_label, exc)

    page.wait_for_timeout(200)
    for row_label in sub_labels:
        try:
            raw = allocation_scope.evaluate(_CLICK_MIRROR_FOR_ROW_IN_BUCKET_JS, [top_label, row_label])
        except Exception as exc:
            logger.debug(
                "OSKAR expand: bucket=%r row=%r mirror click failed: %s",
                top_label,
                row_label,
                exc,
            )
            continue
        if isinstance(raw, dict):
            clicked = bool(raw.get("clicked"))
        else:
            clicked = bool(raw)
        page.wait_for_timeout(480)
        logger.info(
            "OSKAR expand: bucket=%r sub_row=%r clicked=%s",
            top_label,
            row_label,
            clicked,
        )
        if merge_state is not None:
            snap = _collect_raw_rows_from_page(page)
            _merge_row_snapshots_into(
                merge_state["ordered"],
                merge_state["idx_by_isin"],
                snap,
            )
            logger.debug(
                "OSKAR expand: merged row snapshot after %r / %r → %d row(s)",
                top_label,
                row_label,
                len(merge_state["ordered"]),
            )
    logger.info("OSKAR expand: finished subtree for %r", top_label)


def _expand_collapsed_sections(
    page: Any,
    allocation_scope: Any,
    *,
    merge_state: dict[str, Any] | None = None,
) -> None:
    """Expand «Aktuelle Gewichtung» using :data:`_OSKAR_ALLOCATION_BUCKETS`."""
    for top_label, sub_labels in _OSKAR_ALLOCATION_BUCKETS.items():
        _expand_oskar_allocation_bucket(
            page,
            allocation_scope,
            top_label,
            sub_labels,
            merge_state=merge_state,
        )


def _collect_allocation_positions_js() -> str:
    """
    After buckets are expanded, walk ``.asset-allocation`` rows in screen order:
    level1 → category, level2 → sub-bucket, level3 leaf with an ISIN → one position
    (ETF line as shown, including name / ISIN / % / € in ``raw`` for :func:`_parse_row_blob`).
    Open shadow roots are visited so a tree inside a component host is still found.
    """
    return r"""
    () => {
        const isinStrict = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
        const isinLoose = /\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b/;
        const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

        const walkShadows = (node, visit) => {
            visit(node);
            node.querySelectorAll("*").forEach((el) => {
                try {
                    if (el.shadowRoot) walkShadows(el.shadowRoot, visit);
                } catch (e) { /* closed shadow */ }
            });
        };

        const allocRoots = [];
        const seenAlloc = new Set();
        walkShadows(document, (root) => {
            if (!root || !root.querySelector) return;
            const aa = root.querySelector(".asset-allocation");
            if (aa && !seenAlloc.has(aa)) {
                seenAlloc.add(aa);
                allocRoots.push(aa);
            }
        });

        const out = [];
        for (const root of allocRoots) {
            const rowEls = [...root.querySelectorAll("div.row")].filter((r) =>
                ["level1", "level2", "level3"].some((lv) => r.classList.contains(lv))
            );

            let category = "";
            let subcategory = "";

            for (const r of rowEls) {
                const asset = r.querySelector(".asset");
                const label = norm(asset ? asset.textContent : "");
                const blob = norm(r.innerText || "");

                if (r.classList.contains("level1")) {
                    category = label;
                    subcategory = "";
                    continue;
                }
                if (r.classList.contains("level2")) {
                    subcategory = label;
                    if (category === "Tagesgeld" && label === "Tagesgeld" && /€/.test(blob)) {
                        out.push({
                            isin: "__OSKAR_TAGESGELD__",
                            raw: blob.slice(0, 5000),
                            category,
                            subcategory: label,
                        });
                    }
                    continue;
                }
                if (!r.classList.contains("level3")) continue;

                let isin = "";
                for (const w of blob.split(/\s+/)) {
                    if (isinStrict.test(w)) {
                        isin = w;
                        break;
                    }
                }
                if (!isin) {
                    const m = blob.match(isinLoose);
                    if (m) isin = m[1];
                }
                if (!isin || !isinStrict.test(isin)) {
                    if ((category === "Tagesgeld" || subcategory === "Tagesgeld" || label === "Tagesgeld") && /€/.test(blob)) {
                        out.push({
                            isin: "__OSKAR_TAGESGELD__",
                            raw: blob.slice(0, 5000),
                            category,
                            subcategory,
                        });
                    }
                    continue;
                }

                out.push({
                    isin,
                    raw: blob.slice(0, 5000),
                    category,
                    subcategory,
                });
            }
        }
        return out;
    }
    """


def _merge_row_snapshots_into(
    ordered: list[dict[str, Any]],
    idx_by_isin: dict[str, int],
    items: list[dict[str, Any]],
) -> None:
    """
    Merge a snapshot of DOM rows into *ordered* / *idx_by_isin* (same rules
    as :func:`_collect_raw_rows_from_page`): first ISIN wins list order;
    later rows with longer ``raw`` replace that slot; missing category/subcategory
    are backfilled.
    """
    for item in items:
        if not isinstance(item, dict):
            continue
        isin = str(item.get("isin", "")).strip()
        raw = str(item.get("raw", "")).strip()
        if not isin:
            continue
        cat = str(item.get("category", "") or "").strip()
        sub = str(item.get("subcategory", "") or "").strip()
        fr_url = str(item.get("frameUrl", "") or "").strip()
        row = {
            "isin": isin,
            "raw": raw,
            "category": cat,
            "subcategory": sub,
            "frameUrl": fr_url,
        }
        prev_i = idx_by_isin.get(isin)
        if prev_i is None:
            idx_by_isin[isin] = len(ordered)
            ordered.append(row)
            continue
        prev = ordered[prev_i]
        prev_raw = str(prev.get("raw", ""))
        if len(raw) > len(prev_raw):
            prev.update(row)
        else:
            if not str(prev.get("category", "")).strip() and cat:
                prev["category"] = cat
            if not str(prev.get("subcategory", "")).strip() and sub:
                prev["subcategory"] = sub


def _collect_raw_rows_from_page(page: Any) -> list[dict[str, Any]]:
    """
    One dict per leaf position (ETF) under ``.asset-allocation``, in on-screen
    order for the **current** DOM. Each item includes ``isin``, ``raw`` (that row's
    text, including name as shown), optional ``category`` / ``subcategory``, and
    ``frameUrl``. Evaluates each same-origin frame in order.

    The cockpit often **collapses** previously expanded buckets when another is
    opened; callers that need the full list should merge snapshots over time via
    :func:`_merge_row_snapshots_into` (see :func:`_expand_collapsed_sections`).
    """
    js = _collect_allocation_positions_js()
    flat: list[dict[str, Any]] = []
    for fr in page.frames:
        try:
            chunk = fr.evaluate(js)
        except Exception as exc:
            logger.debug(
                "OSKAR allocation rows: skipped frame url=%s err=%s",
                (getattr(fr, "url", "") or "")[:100],
                exc,
            )
            continue
        if not isinstance(chunk, list):
            continue
        fr_url = getattr(fr, "url", "") or ""
        for item in chunk:
            if not isinstance(item, dict):
                continue
            isin = str(item.get("isin", "")).strip()
            raw = str(item.get("raw", "")).strip()
            if not isin:
                continue
            cat = str(item.get("category", "") or "").strip()
            sub = str(item.get("subcategory", "") or "").strip()
            flat.append(
                {
                    "isin": isin,
                    "raw": raw,
                    "category": cat,
                    "subcategory": sub,
                    "frameUrl": fr_url,
                }
            )
    ordered: list[dict[str, Any]] = []
    idx_by_isin: dict[str, int] = {}
    _merge_row_snapshots_into(ordered, idx_by_isin, flat)
    return ordered


def _open_oskar_page(
    p: Any,
    *,
    headless: bool,
    dashboard_url: str,
    timeout_ms: int,
    storage_state: Any | None = None,
) -> tuple[Any, Any, Any]:
    """
    Launch Chromium (TLS verification on), optionally restoring *storage_state*
    (cookies + localStorage of an already logged-in session), and land on the
    dashboard. Returns ``(browser, context, page)``.
    """
    browser = p.chromium.launch(headless=headless, args=list(_CHROMIUM_LAUNCH_ARGS))
    context_kwargs: dict[str, Any] = {
        "user_agent": _USER_AGENT,
        "ignore_https_errors": False,
        "locale": "de-DE",
    }
    if storage_state is not None:
        context_kwargs["storage_state"] = storage_state
    context = browser.new_context(**context_kwargs)
    context.set_default_navigation_timeout(timeout_ms)
    context.set_default_timeout(timeout_ms)
    page = context.new_page()
    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=timeout_ms)
    for state in ("load", "networkidle"):
        # ``networkidle`` lets a pending Auth0 hop land, so ``page.url`` is trustworthy
        # for callers; the cockpit SPA may never go idle, hence best-effort.
        try:
            page.wait_for_load_state(state, timeout=min(15_000, timeout_ms))
        except Exception:
            pass
    return browser, context, page


def _switch_to_headless_after_login(
    p: Any,
    browser: Any,
    context: Any,
    *,
    dashboard_url: str,
    timeout_ms: int,
) -> tuple[Any, Any, Any, bool]:
    """
    Carry the logged-in cookies/localStorage of *context* into a fresh **headless**
    browser so the rest of the scrape runs with no window on screen. Returns the
    ``(browser, context, page)`` to keep using plus whether a headed window is still
    visible.

    ``mein.oskar.de`` has been seen to blank-redirect headless Chromium, so if the
    dashboard URL is not reached we relaunch headed from the same storage state (no
    second manual login in the common case) and minimize that window instead.
    """
    storage_state = context.storage_state()
    browser.close()

    logger.info("OSKAR: handing the logged-in session to a headless browser")
    browser, context, page = _open_oskar_page(
        p,
        headless=True,
        dashboard_url=dashboard_url,
        timeout_ms=timeout_ms,
        storage_state=storage_state,
    )
    try:
        _wait_for_cockpit_dashboard(page, timeout_ms=min(45_000, timeout_ms))
        logger.info("OSKAR: headless session accepted url=%s", page.url)
        # Fresh context, so consent has to be answered again before tabs accept clicks.
        _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=20_000)
        return browser, context, page, False
    except Exception as exc:
        logger.warning(
            "OSKAR: headless handover failed (%s); falling back to a minimized headed browser",
            exc,
        )
        browser.close()

    browser, context, page = _open_oskar_page(
        p,
        headless=False,
        dashboard_url=dashboard_url,
        timeout_ms=timeout_ms,
        storage_state=storage_state,
    )
    if not _on_cockpit_dashboard(page):
        _wait_for_manual_oskar_login(page, timeout_ms=max(timeout_ms, 300_000))
    _hide_headed_browser_window(page)
    return browser, context, page, True


def fetch_oskar_etfs(
    *,
    dashboard_url: str = _DASHBOARD_URL,
    headless: bool = True,
    headless_after_login: bool = False,
    timeout_ms: int = 120_000,
) -> dict[str, OskarEtf]:
    """
    Launch Chromium (TLS verification on). Everything hinges on one signal: the
    redirect to ``mein.oskar.de/cockpit/dashboard``, which Auth0 only performs after a
    successful login. Until it lands, sign in **manually** in the browser (a typo just
    keeps the wait running). With ``headless=True`` and a login gate, the browser is
    restarted **headed** once so you can complete Auth0.

    Once that URL is reached, the headed window is taken off screen before the
    allocation tab is opened:

    * default — the window is **minimized**, keeping the very same browser process;
    * ``headless_after_login=True`` — the session (cookies + localStorage) is moved
      into a fresh **headless** browser, which has to reach the same URL. Falls back
      to the minimized headed window if it does not.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "playwright is required for OSKAR scraping. "
            "Install with pip and run: playwright install chromium"
        ) from e

    rows: dict[str, OskarEtf] = {}

    with sync_playwright() as p:
        logger.info("fetch_oskar_etfs: launching browser")
        browser: Any | None = None
        headed_visible = not headless
        page: Any | None = None
        try:
            browser, context, page = _open_oskar_page(
                p,
                headless=headless,
                dashboard_url=dashboard_url,
                timeout_ms=timeout_ms,
            )
            logger.info("fetch_oskar_etfs: dashboard loaded url=%s", page.url)

            if not _on_cockpit_dashboard(page):
                logger.info("fetch_oskar_etfs: login required (url=%s)", page.url)
                if headless:
                    logger.info(
                        "fetch_oskar_etfs: restarting as headed browser for manual Auth0"
                    )
                    browser.close()
                    browser, context, page = _open_oskar_page(
                        p,
                        headless=False,
                        dashboard_url=dashboard_url,
                        timeout_ms=timeout_ms,
                    )
                    headed_visible = True
                _wait_for_manual_oskar_login(page, timeout_ms=max(timeout_ms, 300_000))

            if headed_visible and headless_after_login:
                browser, context, page, headed_visible = _switch_to_headless_after_login(
                    p,
                    browser,
                    context,
                    dashboard_url=dashboard_url,
                    timeout_ms=timeout_ms,
                )
            elif headed_visible:
                _hide_headed_browser_window(page)
            _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=20_000)

            logger.info("fetch_oskar_etfs: clicking allocation tab")
            _click_allocation_tab(page, timeout_ms=timeout_ms)
            allocation_scope = _wait_for_allocation_scope(page, timeout_ms=timeout_ms)
            merge_state: dict[str, Any] = {"ordered": [], "idx_by_isin": {}}
            _expand_collapsed_sections(
                page,
                allocation_scope,
                merge_state=merge_state,
            )
            page.wait_for_timeout(1_800)
            logger.info("fetch_oskar_etfs: evaluating ETF row js (all frames + shadow)")
            _merge_row_snapshots_into(
                merge_state["ordered"],
                merge_state["idx_by_isin"],
                _collect_raw_rows_from_page(page),
            )
            raw_rows = merge_state["ordered"]

            logger.debug("fetch_oskar_etfs: raw_rows=%s", raw_rows)
            for item in raw_rows:
                if not isinstance(item, dict):
                    continue
                isin = str(item.get("isin", "")).strip()
                raw_text = str(item.get("raw", "")).strip()
                category = str(item.get("category", "") or "")
                subcategory = str(item.get("subcategory", "") or "")
                if _is_oskar_tagesgeld_fetch_row(
                    isin=isin,
                    category=category,
                    subcategory=subcategory,
                    raw_text=raw_text,
                ):
                    weight_pct, value_eur = _parse_tagesgeld_blob(raw_text)
                    logger.info(
                        "fetch_oskar_etfs: appending Tagesgeld name=%s weight_pct=%s value_eur=%s",
                        _OSKAR_CATEGORY_TAGESGELD,
                        weight_pct,
                        value_eur,
                    )
                    rows[_OSKAR_TAGESGELD_FETCH_KEY] = OskarEtf(
                        isin=_OSKAR_TAGESGELD_FETCH_KEY,
                        name=_OSKAR_CATEGORY_TAGESGELD,
                        weight_pct=weight_pct,
                        value_eur=value_eur,
                        raw_text=raw_text,
                        category=_OSKAR_CATEGORY_TAGESGELD,
                    )
                    continue
                if not _ISIN_STRICT.match(isin):
                    continue
                name, weight_pct, value_eur = _parse_row_blob(raw_text, isin)
                category = _oskar_category_from_row(
                    category=category,
                    subcategory=subcategory,
                )
                logger.info("fetch_oskar_etfs: appending row isin=%s, name=%s, weight_pct=%s, value_eur=%s", isin, name, weight_pct, value_eur)
                rows[isin] = (
                    OskarEtf(
                        isin=isin,
                        name=name,
                        weight_pct=weight_pct,
                        value_eur=value_eur,
                        raw_text=raw_text,
                        category=category,
                    )
                )
        finally:
            try:
                if page is not None:
                    _try_oskar_logout(page, timeout_ms=min(15_000, timeout_ms))
            except Exception as exc:
                logger.warning("OSKAR logout: error before browser close: %s", exc)
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass

    return rows


def update_oskar_etfs_in_portfolio(*, headless_after_login: bool = True):
    def _is_oskar_position_tagesgeld(oskar_etf: OskarEtf) -> bool:
        return oskar_etf.name == _OSKAR_CATEGORY_TAGESGELD

    def _is_portfolio_position_oskar_tagesgeld(position: dict[str, Any]) -> bool:
        pos_name = position.get("name") or position.get("Name") or ""
        pos_broker = position.get("broker") or position.get("Broker")
        return pos_name == _OSKAR_CATEGORY_TAGESGELD and pos_broker == _OSKAR

    global global_oskar_etfs
    global_oskar_etfs = fetch_oskar_etfs(headless_after_login=headless_after_login)
    # unique set of ISINs from OSKAR
    fetched_oskar_isins = set(global_oskar_etfs)
    # unique set of ISINs that have been scanned, including those in the portfolio that are not freshly fetched from OSKAR
    scanned_oskar_isins: set[str | None] = set()
    # list of positions to remove from the portfolio because missing from OSKAR
    to_remove: list[tuple[str, dict[str, Any]]] = []

    if not fetched_oskar_isins:
        logger.warning(
            "update_oskar_etfs_in_portfolio: no OSKAR ETFs fetched; leaving portfolio unchanged",
        )
        return

    for oskar_etf in global_oskar_etfs.values():
        matched = False
        for bucket, positions in global_portfolio.items():
            for position in positions:
                pos_isin = position.get("ISIN") or position.get("isin")
                pos_broker = position.get("broker") or position.get("Broker")
                # it doesn't make to process non-OSKAR positions
                if pos_broker != _OSKAR:
                    continue
                # it doesn't make to process positions that have already been scanned (removed or updated)
                if pos_isin in scanned_oskar_isins:
                    continue
                # if oskar position is in global portfolio but not freshly fetched from OSKAR, it means OSKAR removed it
                # make an exception for Tagesgeld or those oskar portoflio positions without an ISIN
                if pos_isin not in fetched_oskar_isins and not _is_portfolio_position_oskar_tagesgeld(position):
                    # add to the list of positions to remove from the portfolio
                    to_remove.append((bucket, position))
                    logger.info(
                        "update_oskar_etfs_in_portfolio: removing stale OSKAR ISIN %s from %r",
                        pos_isin,
                        bucket,
                    )
                    # this position doesn't need to be addressed anymore
                    scanned_oskar_isins.add(pos_isin)
                    continue
                # this oskar position is in the global portfolio and in OSKAR, it matches the oskar etf at hand
                # so update the position with the new value and shares in the portfolio
                isin_match = pos_isin == oskar_etf.isin
                tagesgeld_match = (
                    _is_portfolio_position_oskar_tagesgeld(position)
                    and _is_oskar_position_tagesgeld(oskar_etf)
                )
                if isin_match or tagesgeld_match:
                    position["value"] = oskar_etf.value_eur
                    position["shares"] = None
                    position["price"] = None
                    matched = True
                    if pos_isin is not None:
                        scanned_oskar_isins.add(pos_isin)
        if matched:
            continue
        # this oskar position is not in the global portfolio, so add it
        bucket = _OSKAR_CATEGORY_TO_PORTFOLIO.get(oskar_etf.category, _DEFAULT_OSKAR_PORTFOLIO_BUCKET)
        global_portfolio.setdefault(bucket, []).append(
            {
                "name": oskar_etf.name,
                "ISIN": None if _is_oskar_position_tagesgeld(oskar_etf) else oskar_etf.isin,
                "shares": None,
                "value": oskar_etf.value_eur,
                "price": None,
                "broker": _OSKAR,
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
        # this oskar position has been added to the portfolio, so it doesn't need to be addressed anymore
        scanned_oskar_isins.add(oskar_etf.isin)
        logger.info(
            "update_oskar_etfs_in_portfolio: added missing ISIN %s to %r (value=%s)",
            oskar_etf.isin,
            bucket,
            oskar_etf.value_eur,
        )

    # remove the positions that have been marked for removal
    for bucket, position in to_remove:
        global_portfolio[bucket].remove(position)