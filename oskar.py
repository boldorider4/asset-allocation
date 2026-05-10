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

from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)

_DASHBOARD_URL = "https://mein.oskar.de/cockpit/dashboard"

# mein.oskar.de rejects HeadlessChrome with a blank-page redirect; use a normal Chrome UA.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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
    elif idx == 0:
        name = ""

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


# Same predicate as ``_wait_for_manual_oskar_login`` polling (keep in sync).
_OSKAR_COCKPIT_READY_JS = r"""() => {
    const h = (location.hostname || '').toLowerCase();
    if (!h.includes('mein.oskar.de')) return false;
    const t = (document.body && document.body.innerText) || '';
    if (t.includes('Wertentwicklung')) return true;
    if (/Aktuelle\s*Gewichtung/i.test(t)) return true;
    return /\bGewichtung\b/i.test(t);
}"""


def _cockpit_ready(page: Any) -> bool:
    try:
        return bool(page.evaluate(_OSKAR_COCKPIT_READY_JS))
    except Exception:
        return False


def _wait_for_oskar_nav_after_dashboard_goto(page: Any, *, timeout_ms: int) -> None:
    """
    After ``goto(..., domcontentloaded)``, Auth0 may still be redirecting: ``page.url``
    can briefly stay on ``mein.oskar.de`` so a one-shot ``_page_needs_login`` is wrong.
    Poll until login host, cockpit content, or timeout.
    """
    steps = max(1, min(timeout_ms // 400, 80))
    for _ in range(steps):
        if _page_needs_login(page) or _cockpit_ready(page):
            return
        page.wait_for_timeout(400)


def _wait_for_manual_oskar_login(page: Any, *, timeout_ms: int) -> None:
    """
    Block until a human has finished Auth0 in the **headed** browser: the cockpit
    shows «Aktuelle Gewichtung» or «Wertentwicklung» on ``mein.oskar.de``.
    Periodically clears the Sourcepoint cookie iframe so ``innerText`` can reflect
    the real cockpit.
    """
    logger.warning(
        "OSKAR manual login: complete Auth0 in the browser window (credentials + Continue / "
        "Anmelden). Waiting up to %.0f s until cockpit tabs appear…",
        timeout_ms / 1000,
    )
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            if _cockpit_ready(page):
                logger.info("OSKAR manual login: cockpit detected url=%s", page.url)
                return
        except Exception:
            pass
        _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=5_000)
        page.wait_for_timeout(450)
    raise RuntimeError(
        "OSKAR manual login: timed out waiting for cockpit (expected «Wertentwicklung» or "
        "«Gewichtung» / «Aktuelle Gewichtung» on mein.oskar.de after Auth0)."
    )


def _page_needs_login(page: Any) -> bool:
    url = (page.url or "").lower()
    try:
        host = (urlparse(page.url).hostname or "").lower()
    except Exception:
        host = ""
    if "auth0" in url or "login.oskar" in url or host == "login.oskar.de":
        return True
    if "/login" in url and "mein.oskar" in url:
        return True
    pw = page.locator('input[type="password"]')
    if pw.count() > 0:
        try:
            if pw.first.is_visible():
                return True
        except Exception:
            return True
    return False


def _click_allocation_tab(page: Any, *, timeout_ms: int) -> None:
    """
    Open the cockpit asset-allocation view (German UI: «Gewichtung» / «Aktuelle Gewichtung»).
    Cockpit may host tabs in a child ``frame`` or shadow root; Playwright's role/text
    locators are evaluated per frame.
    """
    t = min(timeout_ms, 60_000)
    attempts = [
        ("tab-regex-gewichtung", lambda s: s.get_by_role("tab", name=re.compile(r"gewichtung", re.I))),
        ("link-regex-gewichtung", lambda s: s.get_by_role("link", name=re.compile(r"gewichtung", re.I))),
        ("text-regex-aktuelle-gewichtung", lambda s: s.get_by_text(re.compile(r"Aktuelle\s*Gewichtung", re.I))),
        ("text-exact-aktuelle-gewichtung", lambda s: s.get_by_text("Aktuelle Gewichtung", exact=True)),
        ("text-regex-gewichtung-word", lambda s: s.get_by_text(re.compile(r"\bGewichtung\b", re.I))),
    ]
    for fr in page.frames:
        try:
            fr_url = getattr(fr, "url", "") or ""
        except Exception:
            fr_url = ""
        for label, factory in attempts:
            try:
                loc = factory(fr)
            except Exception as exc:
                logger.debug("OSKAR: allocation tab scope %s factory %s: %s", fr_url, label, exc)
                continue
            if loc.count() == 0:
                continue
            try:
                first = loc.first
                first.wait_for(state="visible", timeout=t)
                first.click(timeout=t)
                logger.info("OSKAR: opened allocation view via %s (frame=%s)", label, fr_url[:120])
                page.wait_for_timeout(800)
                return
            except Exception as exc:
                logger.debug("OSKAR: allocation tab attempt %s frame=%s: %s", label, fr_url[:80], exc)
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


# Expand top levels and then discover each sub-bucket
_OSKAR_ALLOCATION_BUCKETS = {
    "Aktien": (
        "Aktien Small Cap",
        "Aktien Europa", "Aktien Japan",
        "Aktien Schwellenländer",
        "Aktien Asien und pazifischer Raum",
        "Aktien USA"
        ),
    "Anleihen": (
        "Anleihen Global",
        "Anleihen Schwellenländer"
        ),
    "Inflationsgeschützt": ("Gold", "Anleihen inflationsgeschützt"),
    "Tagesgeld": ("Tagesgeld",),
}

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
                if (!isin || !isinStrict.test(isin)) continue;

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


def fetch_oskar_etfs(
    *,
    dashboard_url: str = _DASHBOARD_URL,
    headless: bool = True,
    timeout_ms: int = 120_000,
) -> list[OskarEtf]:
    """
    Launch Chromium (TLS verification on). If login is required, sign in **manually**
    in the browser; the run continues once cockpit tabs («Aktuelle Gewichtung» /
    «Wertentwicklung» / «Gewichtung») appear. With ``headless=True`` and a login gate,
    the browser is restarted **headed** once so you can complete Auth0. Then the
    allocation tab is opened and ETF rows are parsed.
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
        browser = p.chromium.launch(headless=headless)
        page: Any | None = None
        try:
            logger.info("fetch_oskar_etfs: creating context")
            context = browser.new_context(
                user_agent=_USER_AGENT,
                ignore_https_errors=False,
                locale="de-DE",
            )
            context.set_default_navigation_timeout(timeout_ms)
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            logger.info("fetch_oskar_etfs: page created")
            logger.info("fetch_oskar_etfs: navigating to dashboard")
            page.goto(dashboard_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("load", timeout=min(60_000, timeout_ms))
            except Exception:
                pass
            _wait_for_oskar_nav_after_dashboard_goto(
                page, timeout_ms=min(45_000, timeout_ms)
            )

            needs_login = _page_needs_login(page)
            cockpit_ok = _cockpit_ready(page)
            if needs_login or not cockpit_ok:
                logger.info(
                    "fetch_oskar_etfs: waiting for you to sign in or cockpit to load "
                    "(url=%s needs_login=%s cockpit_ready=%s)",
                    page.url,
                    needs_login,
                    cockpit_ok,
                )
                if headless:
                    logger.info(
                        "fetch_oskar_etfs: restarting as headed browser for manual Auth0"
                    )
                    browser.close()
                    browser = p.chromium.launch(headless=False)
                    context = browser.new_context(
                        user_agent=_USER_AGENT,
                        ignore_https_errors=False,
                        locale="de-DE",
                    )
                    context.set_default_navigation_timeout(timeout_ms)
                    context.set_default_timeout(timeout_ms)
                    page = context.new_page()
                    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("load", timeout=min(60_000, timeout_ms))
                    except Exception:
                        pass
                    _wait_for_oskar_nav_after_dashboard_goto(
                        page, timeout_ms=min(45_000, timeout_ms)
                    )

                manual_timeout = max(timeout_ms, 300_000)
                _wait_for_manual_oskar_login(page, timeout_ms=manual_timeout)
                # Avoid ``networkidle``: cockpit SPAs keep analytics / long-poll traffic
                # open so ``networkidle`` often never fires (looks like a hang).
                page.goto(dashboard_url, wait_until="load", timeout=timeout_ms)
            else:
                try:
                    page.wait_for_load_state("load", timeout=timeout_ms)
                except Exception:
                    pass

            _try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=20_000)
            page.wait_for_timeout(1_200)

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
                if not _ISIN_STRICT.match(isin):
                    continue
                name, weight_pct, value_eur = _parse_row_blob(raw_text, isin)
                logger.info("fetch_oskar_etfs: appending row isin=%s, name=%s, weight_pct=%s, value_eur=%s", isin, name, weight_pct, value_eur)
                rows[isin] = (
                    OskarEtf(
                        isin=isin,
                        name=name,
                        weight_pct=weight_pct,
                        value_eur=value_eur,
                        raw_text=raw_text,
                    )
                )
        finally:
            try:
                if page is not None:
                    _try_oskar_logout(page, timeout_ms=min(15_000, timeout_ms))
            except Exception as exc:
                logger.warning("OSKAR logout: error before browser close: %s", exc)
            try:
                browser.close()
            except Exception:
                pass

    return rows
