"""
OSKAR portfolio positions (JustETF pricing) plus a Playwright-based client for the
logged-in cockpit «Aktuelle Gewichtung» ETF list.

Sign in manually in the browser when prompted. After ``pip install`` run
``playwright install chromium`` once so the browser binary is available.

**Debug dump:** set ``OSKAR_DUMP_PAGE_JSON=1`` to write ``oskar-page-debug-after-gewichtung.json``
(right after opening Gewichtung), then ``expand-{aktien|anleihen}-top`` after each top bucket
chevron, and ``expand-{bucket}-opened-{row-slug}`` after each successful submenu chevron
(e.g. ``…-opened-aktien-small-cap`` from the row ``div.asset`` label).
Override basename with ``OSKAR_PAGE_DUMP_PATH`` (suffixes appended as above).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)

_DASHBOARD_URL = "https://mein.oskar.de/cockpit/dashboard"
# ``OSKAR_DUMP_PAGE_JSON=1``: after-gewichtung + per-click expand dumps for Aktien/Anleihen.
# ``OSKAR_PAGE_DUMP_PATH`` / ``OSKAR_FULL_PAGE_HTML_MAX_CHARS``.
_PAGE_DUMP_ENV = "OSKAR_DUMP_PAGE_JSON"
_DEFAULT_PAGE_DUMP_PATH = "oskar-page-debug.json"
_FULL_HTML_MAX_ENV = "OSKAR_FULL_PAGE_HTML_MAX_CHARS"


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_INTERACTIVE_DUMP_JS = r"""
(frameUrl) => {
    const walkShadowRoots = (doc) => {
        const roots = [];
        const visit = (r) => {
            roots.push(r);
            r.querySelectorAll("*").forEach((el) => {
                try {
                    if (el.shadowRoot) visit(el.shadowRoot);
                } catch (e) {
                    /* closed shadow */
                }
            });
        };
        visit(doc);
        return roots;
    };
    const roots = walkShadowRoots(document);
    const row = (el) => ({
        tag: el.tagName,
        role: el.getAttribute("role"),
        type: el.getAttribute("type"),
        href: (el.href || "").slice(0, 160),
        className: (typeof el.className === "string" ? el.className : "").slice(0, 120),
        text: (el.innerText || el.textContent || "").trim().slice(0, 220),
        ariaLabel: el.getAttribute("aria-label"),
        dataTestid: el.getAttribute("data-testid"),
        ariaExpanded: el.getAttribute("aria-expanded"),
        visible: !!(el.offsetWidth && el.offsetHeight),
        frameUrl,
    });
    const buttons = [];
    const links = [];
    const tabs = [];
    const menuitems = [];
    const pushCap = (arr, el, cap) => {
        if (arr.length >= cap) return;
        arr.push(row(el));
    };
    for (const r of roots) {
        r.querySelectorAll("button, [role='button']").forEach((el) => pushCap(buttons, el, 150));
        r.querySelectorAll("a[href], a[role='link']").forEach((el) => pushCap(links, el, 150));
        r.querySelectorAll('[role="tab"]').forEach((el) => pushCap(tabs, el, 80));
        r.querySelectorAll('[role="menuitem"]').forEach((el) => pushCap(menuitems, el, 80));
    }
    const bodyText = ((document.body && document.body.innerText) || "").slice(0, 12000);
    return { frameUrl, bodyTextSample: bodyText, buttons, links, tabs, menuitems };
}
"""


def _build_page_debug_blob(page: Any) -> dict[str, Any]:
    """Structured snapshot: URL, title, a11y tree, and common interactive nodes (all frames + shadow)."""
    a11y: Any
    try:
        a11y = page.accessibility.snapshot(interesting_only=False)
    except Exception as exc:
        try:
            body = page.locator("body")
            snap = getattr(body, "aria_snapshot", None)
            a11y = snap() if callable(snap) else {"error": str(exc)}
        except Exception as exc2:
            a11y = {"error": f"{exc!s}; fallback: {exc2!s}"}

    per_frame: list[dict[str, Any]] = []
    for fr in page.frames:
        try:
            sample = fr.evaluate(_INTERACTIVE_DUMP_JS, fr.url)
            per_frame.append(sample)
        except Exception as exc:
            per_frame.append({"frameUrl": getattr(fr, "url", ""), "error": str(exc)})

    merged = {"buttons": [], "links": [], "tabs": [], "menuitems": []}
    for block in per_frame:
        if "error" in block:
            continue
        for k in merged:
            merged[k].extend(block.get(k, []))
    return {
        "url": page.url,
        "title": page.title(),
        "accessibility": a11y,
        "interactiveByFrame": per_frame,
        "interactive": merged,
    }


def _full_page_html_max_chars() -> int:
    raw = os.environ.get(_FULL_HTML_MAX_ENV, "").strip()
    if not raw:
        return 4_000_000
    try:
        return max(50_000, int(raw))
    except ValueError:
        return 4_000_000


def _pack_html_field(html: str, *, max_chars: int) -> dict[str, Any]:
    n = len(html)
    if n <= max_chars:
        return {"truncated": False, "length": n, "html": html}
    return {
        "truncated": True,
        "length": n,
        "keptChars": max_chars,
        "html": html[:max_chars],
    }


def _build_full_page_blob(page: Any) -> dict[str, Any]:
    """Debug blob from :func:`_build_page_debug_blob` plus full serialized HTML per frame."""
    blob: dict[str, Any] = dict(_build_page_debug_blob(page))
    cap = _full_page_html_max_chars()
    try:
        blob["fullMainFrameHtml"] = _pack_html_field(page.content(), max_chars=cap)
    except Exception as exc:
        blob["fullMainFrameHtml"] = {"error": str(exc)}
    by_frame: list[dict[str, Any]] = []
    for fr in page.frames:
        u = getattr(fr, "url", "") or ""
        try:
            html = fr.content()
            by_frame.append({"frameUrl": u, **_pack_html_field(html, max_chars=cap)})
        except Exception as exc:
            by_frame.append({"frameUrl": u, "error": str(exc)})
    blob["fullHtmlByFrame"] = by_frame
    return blob


def _dump_full_page_debug_json(
    page: Any,
    *,
    tag: str,
    extra_log: logging.Logger | None = None,
) -> None:
    """Write :func:`_build_full_page_blob` when ``OSKAR_DUMP_PAGE_JSON`` is enabled."""
    if not _env_flag_enabled(_PAGE_DUMP_ENV):
        return
    base = Path(os.environ.get("OSKAR_PAGE_DUMP_PATH", _DEFAULT_PAGE_DUMP_PATH))
    path = base.with_stem(f"{base.stem}-{tag}")
    blob = _build_full_page_blob(page)
    path.write_text(
        json.dumps(blob, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    resolved = path.resolve()
    logger.warning("OSKAR debug: wrote full-page dump to %s", resolved)
    if extra_log is not None:
        extra_log.info("OSKAR page dump: %s (tag=%s)", resolved, tag)


def _oskar_expand_dump_slug(top_label: str) -> str:
    """ASCII slug for dump filenames (e.g. «Anleihen» → ``anleihen``)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", top_label.strip().lower())
    return (s.strip("-") or "bucket")[:40]


def _unique_oskar_opened_dump_tag(seen: set[str], bucket_slug: str, row_label: str) -> str:
    """Build ``expand-{bucket}-opened-{row-slug}`` with numeric suffix if needed."""
    rl = (row_label or "").strip()
    row_slug = _oskar_expand_dump_slug(rl) if rl else "row"
    base = f"expand-{bucket_slug}-opened-{row_slug}"
    tag = base
    n = 2
    while tag in seen:
        tag = f"{base}-{n}"
        n += 1
    seen.add(tag)
    return tag


# mein.oskar.de rejects HeadlessChrome with a blank-page redirect; use a normal Chrome UA.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_ISIN_STRICT = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_DE_PERCENT_RE = re.compile(r"([\d][\d.,]*)\s*%")
_DE_EURO_RE = re.compile(r"([\d][\d.,]*)\s*€")


@dataclass(frozen=True)
class OskarWeightingEtf:
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


def _click_weighting_tab(page: Any, *, timeout_ms: int) -> None:
    """
    Open the current-weighting view. Cockpit may host tabs in a child ``frame`` or
    shadow root; Playwright's role/text locators are evaluated per frame.
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
                logger.debug("OSKAR: weighting scope %s factory %s: %s", fr_url, label, exc)
                continue
            if loc.count() == 0:
                continue
            try:
                first = loc.first
                first.wait_for(state="visible", timeout=t)
                first.click(timeout=t)
                logger.info("OSKAR: opened weighting view via %s (frame=%s)", label, fr_url[:120])
                page.wait_for_timeout(800)
                return
            except Exception as exc:
                logger.debug("OSKAR: weighting attempt %s frame=%s: %s", label, fr_url[:80], exc)
    raise RuntimeError(
        "OSKAR: could not activate Gewichtung tab (try OSKAR_DUMP_PAGE_JSON=1 for after-gewichtung full-page dump)."
    )


# Only expand these top-level «Aktuelle Gewichtung» buckets (avoids infinite chevron loops
# on Inflation / Tagesgeld / deeper-only branches).
_OSKAR_WEIGHTING_TOP_BUCKETS = ("Aktien", "Anleihen")

_COUNT_COLLAPSED_MIRRORS_IN_BUCKET_JS = r"""
(label) => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const root = document.querySelector(".asset-allocation");
    if (!root) return -1;
    const rows = [...root.querySelectorAll("div.row")].filter((r) =>
        ["level1", "level2", "level3"].some((lv) => r.classList.contains(lv))
    );
    let i0 = -1;
    const L = norm(label);
    for (let i = 0; i < rows.length; i++) {
        const a = rows[i].querySelector(".asset");
        const t = norm(a ? a.textContent : "");
        if (rows[i].classList.contains("level1") && t === L) {
            i0 = i;
            break;
        }
    }
    if (i0 < 0) return -1;
    let end = rows.length;
    for (let j = i0 + 1; j < rows.length; j++) {
        if (rows[j].classList.contains("level1")) {
            end = j;
            break;
        }
    }
    let c = 0;
    for (let j = i0 + 1; j < end; j++) {
        const em = rows[j].querySelector("em.fa-angle-right.mirror");
        if (em && em.offsetParent) c++;
    }
    return c;
}
"""

_CLICK_FIRST_MIRROR_IN_BUCKET_JS = r"""
(label) => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const root = document.querySelector(".asset-allocation");
    if (!root) return { clicked: false, rowLabel: null };
    const rows = [...root.querySelectorAll("div.row")].filter((r) =>
        ["level1", "level2", "level3"].some((lv) => r.classList.contains(lv))
    );
    let i0 = -1;
    const L = norm(label);
    for (let i = 0; i < rows.length; i++) {
        const a = rows[i].querySelector(".asset");
        const t = norm(a ? a.textContent : "");
        if (rows[i].classList.contains("level1") && t === L) {
            i0 = i;
            break;
        }
    }
    if (i0 < 0) return { clicked: false, rowLabel: null };
    for (let j = i0 + 1; j < rows.length; j++) {
        const r = rows[j];
        if (r.classList.contains("level1")) break;
        const em = r.querySelector("em.fa-angle-right.mirror");
        if (em && em.offsetParent) {
            const row = em.closest("div.row");
            const asset = row ? row.querySelector(".asset") : null;
            const rowLabel = norm(asset ? asset.textContent : "") || null;
            em.click();
            return { clicked: true, rowLabel };
        }
    }
    return { clicked: false, rowLabel: null };
}
"""


def _mirror_count_in_weighting_bucket(page: Any, top_label: str) -> int:
    """Number of visible ``fa-angle-right.mirror`` chevrons in subtree, or ``-1`` if bucket missing."""
    try:
        n = page.evaluate(_COUNT_COLLAPSED_MIRRORS_IN_BUCKET_JS, top_label)
    except Exception:
        return -1
    if isinstance(n, bool):
        return -1
    try:
        return int(n)
    except (TypeError, ValueError):
        return -1


def _expand_oskar_weighting_bucket(
    page: Any,
    top_label: str,
    *,
    max_sub_clicks: int,
    extra_log: logging.Logger | None = None,
) -> None:
    """
    Open one level-1 bucket, then expand subtree chevrons while **mirror count**
    changes after each click. Stops when count is 0, no click is possible, or the
    count stops changing (stuck / no real progress).
    """
    root = page.locator(".asset-allocation").first
    if root.count() == 0:
        logger.warning("OSKAR expand: no .asset-allocation on page")
        return
    top_row = root.locator("div.row.level1").filter(
        has=page.locator(
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

    slug = _oskar_expand_dump_slug(top_label)
    page.wait_for_timeout(200)
    _dump_full_page_debug_json(page, tag=f"expand-{slug}-top", extra_log=extra_log)

    stagnation = 0
    opened_dump_tags: set[str] = set()
    for _ in range(max_sub_clicks):
        before = _mirror_count_in_weighting_bucket(page, top_label)
        if before < 0:
            logger.warning("OSKAR expand: could not locate bucket %r for mirror count", top_label)
            break
        if before == 0:
            logger.info("OSKAR expand: bucket=%r no collapsed subtree chevrons left", top_label)
            break
        try:
            raw = page.evaluate(_CLICK_FIRST_MIRROR_IN_BUCKET_JS, top_label)
        except Exception as exc:
            logger.debug("OSKAR expand: subtree %r click failed: %s", top_label, exc)
            break
        if isinstance(raw, dict):
            clicked = bool(raw.get("clicked"))
            row_label = str(raw.get("rowLabel") or "").strip()
        else:
            clicked = bool(raw)
            row_label = ""
        page.wait_for_timeout(480)
        after = _mirror_count_in_weighting_bucket(page, top_label)
        if after < 0:
            after = before
        logger.info(
            "OSKAR expand: bucket=%r collapsed_chevrons=%d->%d clicked=%s row=%r",
            top_label,
            before,
            after,
            clicked,
            row_label or None,
        )

        if not clicked:
            stagnation += 1
            if stagnation >= 2:
                logger.info(
                    "OSKAR expand: stop bucket=%r (no clickable mirror, stagnation=%s)",
                    top_label,
                    stagnation,
                )
                break
            continue
        if after == before:
            stagnation += 1
            if stagnation >= 2:
                logger.warning(
                    "OSKAR expand: stop bucket=%r (mirror count unchanged after click, likely stuck)",
                    top_label,
                )
                break
            continue

        stagnation = 0
        dump_tag = _unique_oskar_opened_dump_tag(opened_dump_tags, slug, row_label)
        _dump_full_page_debug_json(page, tag=dump_tag, extra_log=extra_log)
    logger.info("OSKAR expand: finished subtree for %r", top_label)


def _expand_collapsed_sections(
    page: Any,
    *,
    max_rounds: int = 20,
    extra_log: logging.Logger | None = None,
) -> None:
    """
    Expand «Aktuelle Gewichtung» only under **Aktien** and **Anleihen** (see
    :func:`_expand_oskar_weighting_bucket`). ``max_rounds`` caps subtree clicks
    per bucket; expansion also stops when collapsed-chevron count stops changing.
    """
    sub = max(1, max_rounds)
    for lbl in _OSKAR_WEIGHTING_TOP_BUCKETS:
        _expand_oskar_weighting_bucket(page, lbl, max_sub_clicks=sub, extra_log=extra_log)


def _extract_weighting_etfs_js() -> str:
    """
    Collect ISINs from all text under open shadow roots, then pick the richest
    ``innerText`` ancestor per ISIN (weight % / €) for downstream parsing.
    """
    return r"""
    () => {
        const isinRe = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
        const walkRoots = (doc) => {
            const roots = [];
            const visit = (r) => {
                roots.push(r);
                r.querySelectorAll("*").forEach((el) => {
                    try {
                        if (el.shadowRoot) visit(el.shadowRoot);
                    } catch (e) { /* closed shadow */ }
                });
            };
            visit(doc);
            return roots;
        };
        const roots = walkRoots(document);
        const seen = new Set();
        const isins = [];
        for (const r of roots) {
            const hay = r.innerText || "";
            const g = /\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b/g;
            let m;
            while ((m = g.exec(hay)) !== null) {
                const x = m[1];
                if (!isinRe.test(x) || seen.has(x)) continue;
                seen.add(x);
                isins.push(x);
            }
        }
        const out = [];
        for (const isin of isins) {
            let bestEl = null;
            let bestScore = -1e9;
            for (const r of roots) {
                r.querySelectorAll("*").forEach((el) => {
                    const blob = (el.innerText || "").trim();
                    if (!blob.includes(isin)) return;
                    if (blob.length < 12 || blob.length > 6000) return;
                    const hasPct = /%/.test(blob);
                    const hasEur = /€|EUR/i.test(blob);
                    const words = blob.split(/\s+/).filter(Boolean).length;
                    const score =
                        (hasPct ? 4 : 0) +
                        (hasEur ? 4 : 0) +
                        Math.min(words, 60) * 0.04 -
                        blob.length * 0.0015;
                    if (score > bestScore) {
                        bestScore = score;
                        bestEl = el;
                    }
                });
            }
            if (bestEl) {
                out.push({
                    isin,
                    raw: (bestEl.innerText || "").trim().slice(0, 5000),
                });
            }
        }
        return out;
    }
    """


def _collect_raw_weighting_rows_from_page(page: Any) -> list[dict[str, Any]]:
    """Run :func:`_extract_weighting_etfs_js` in every same-origin frame; merge by ISIN."""
    js = _extract_weighting_etfs_js()
    by_isin: dict[str, dict[str, Any]] = {}
    for fr in page.frames:
        try:
            chunk = fr.evaluate(js)
        except Exception as exc:
            logger.debug(
                "OSKAR weighting extract: skipped frame url=%s err=%s",
                (getattr(fr, "url", "") or "")[:100],
                exc,
            )
            continue
        if not isinstance(chunk, list):
            continue
        for item in chunk:
            if not isinstance(item, dict):
                continue
            isin = str(item.get("isin", "")).strip()
            raw = str(item.get("raw", "")).strip()
            if not isin:
                continue
            prev = by_isin.get(isin)
            fr_url = getattr(fr, "url", "") or ""
            if prev is None or len(raw) > len(str(prev.get("raw", ""))):
                by_isin[isin] = {"isin": isin, "raw": raw, "frameUrl": fr_url}
    return list(by_isin.values())


def fetch_oskar_weighting_etfs(
    *,
    dashboard_url: str = _DASHBOARD_URL,
    headless: bool = True,
    timeout_ms: int = 120_000,
    extra_log: logging.Logger | None = None,
) -> list[OskarWeightingEtf]:
    """
    Launch Chromium (TLS verification on). If login is required, sign in **manually**
    in the browser; the run continues once cockpit tabs («Aktuelle Gewichtung» /
    «Wertentwicklung» / «Gewichtung») appear. With ``headless=True`` and a login gate,
    the browser is restarted **headed** once so you can complete Auth0. Then the
    weighting tab is opened and ETF rows are parsed.

    If ``extra_log`` is set (e.g. the test module logger), each full-page JSON dump
    path is also emitted at INFO on that logger.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "playwright is required for OSKAR scraping. "
            "Install with pip and run: playwright install chromium"
        ) from e

    rows: list[OskarWeightingEtf] = []

    with sync_playwright() as p:
        logger.info("fetch_oskar_weighting_etfs: launching browser")
        browser = p.chromium.launch(headless=headless)
        page: Any | None = None
        try:
            logger.info("fetch_oskar_weighting_etfs: creating context")
            context = browser.new_context(
                user_agent=_USER_AGENT,
                ignore_https_errors=False,
                locale="de-DE",
            )
            context.set_default_navigation_timeout(timeout_ms)
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            logger.info("fetch_oskar_weighting_etfs: page created")
            logger.info("fetch_oskar_weighting_etfs: navigating to dashboard")
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
                    "fetch_oskar_weighting_etfs: waiting for you to sign in or cockpit to load "
                    "(url=%s needs_login=%s cockpit_ready=%s)",
                    page.url,
                    needs_login,
                    cockpit_ok,
                )
                if headless:
                    logger.info(
                        "fetch_oskar_weighting_etfs: restarting as headed browser for manual Auth0"
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

            logger.info("fetch_oskar_weighting_etfs: clicking weighting tab")
            _click_weighting_tab(page, timeout_ms=timeout_ms)
            _dump_full_page_debug_json(page, tag="after-gewichtung", extra_log=extra_log)
            _expand_collapsed_sections(page, extra_log=extra_log)
            page.wait_for_timeout(1_800)
            logger.info("fetch_oskar_weighting_etfs: evaluating weighting etfs js (all frames + shadow)")
            raw_rows = _collect_raw_weighting_rows_from_page(page)

            for item in raw_rows:
                if not isinstance(item, dict):
                    continue
                isin = str(item.get("isin", "")).strip()
                raw_text = str(item.get("raw", "")).strip()
                if not _ISIN_STRICT.match(isin):
                    continue
                name, weight_pct, value_eur = _parse_row_blob(raw_text, isin)
                logger.info("fetch_oskar_weighting_etfs: appending row isin=%s, name=%s, weight_pct=%s, value_eur=%s", isin, name, weight_pct, value_eur)
                rows.append(
                    OskarWeightingEtf(
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


class OskarPosition(JustETFPosition):
    """JustETF-backed position; cockpit scraping helpers are static below."""

    def __init__(
        self,
        isin: str,
        name: str,
        short_name: str,
        shares: float,
        value: float,
        broker: str,
        *,
        last_price: float | None = None,
    ):
        super().__init__(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            last_price=last_price,
        )

    @staticmethod
    def fetch_weighting_etfs(
        *,
        dashboard_url: str = _DASHBOARD_URL,
        headless: bool = True,
        timeout_ms: int = 120_000,
        extra_log: logging.Logger | None = None,
    ) -> list[OskarWeightingEtf]:
        """Same as :func:`fetch_oskar_weighting_etfs` (manual sign-in in the browser)."""
        return fetch_oskar_weighting_etfs(
            dashboard_url=dashboard_url,
            headless=headless,
            timeout_ms=timeout_ms,
            extra_log=extra_log,
        )
