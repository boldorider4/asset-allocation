"""
OSKAR portfolio positions (JustETF pricing) plus a Playwright-based client for the
logged-in cockpit «Aktuelle Gewichtung» ETF list.

Sign in manually in the browser when prompted. After ``pip install`` run
``playwright install chromium`` once so the browser binary is available.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

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
    """Best-effort: click «Ausloggen» so the session ends before the browser closes."""
    logger.info("OSKAR logout: looking for Ausloggen")
    for pat in (re.compile(r"^\s*Ausloggen\s*$", re.I), re.compile(r"Ausloggen", re.I)):
        for role in ("button", "link"):
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
        'button:has-text("Ausloggen")',
        'a:has-text("Ausloggen")',
        '[role="menuitem"]:has-text("Ausloggen")',
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


def _wait_for_manual_oskar_login(page: Any, *, timeout_ms: int) -> None:
    """
    Block until a human has finished Auth0 in the **headed** browser: the cockpit
    shows «Aktuelle Gewichtung» or «Wertentwicklung» on ``mein.oskar.de``.
    """
    logger.warning(
        "OSKAR manual login: complete Auth0 in the browser window (credentials + Continue / "
        "Anmelden). Waiting up to %.0f s until cockpit tabs appear…",
        timeout_ms / 1000,
    )
    try:
        page.wait_for_function(
            r"""() => {
                const h = (location.hostname || '').toLowerCase();
                if (!h.includes('mein.oskar.de')) return false;
                const t = (document.body && document.body.innerText) || '';
                return t.includes('Aktuelle Gewichtung') || t.includes('Wertentwicklung');
            }""",
            timeout=timeout_ms,
        )
    except Exception as exc:
        raise RuntimeError(
            "OSKAR manual login: timed out waiting for cockpit (expected «Aktuelle Gewichtung» "
            "or «Wertentwicklung» on mein.oskar.de after Auth0)."
        ) from exc
    logger.info("OSKAR manual login: cockpit detected url=%s", page.url)


def _page_needs_login(page: Any) -> bool:
    url = page.url
    if "auth0" in url or "login.oskar" in url:
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
    tab = page.get_by_text("Aktuelle Gewichtung", exact=True)
    tab.first.wait_for(state="visible", timeout=timeout_ms)
    tab.first.click()
    page.wait_for_timeout(800)


def _expand_collapsed_sections(page: Any, *, max_rounds: int = 12) -> None:
    """Expand accordion / tree rows so nested ETF rows (with ISINs) appear."""
    for _ in range(max_rounds):
        collapsed = page.locator('[aria-expanded="false"]')
        n = collapsed.count()
        if n == 0:
            break
        clicked = False
        for i in range(min(n, 40)):
            try:
                collapsed.nth(i).click(timeout=1500)
                clicked = True
            except Exception:
                continue
        page.wait_for_timeout(250)
        if not clicked:
            break


def _extract_weighting_etfs_js() -> str:
    return r"""
    () => {
        const isinRe = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
        const seen = new Set();
        const out = [];
        const elements = Array.from(document.querySelectorAll('body *'));
        for (const el of elements) {
            if (el.children && el.children.length) continue;
            const t = (el.textContent || '').trim();
            if (!isinRe.test(t)) continue;
            if (seen.has(t)) continue;
            seen.add(t);
            let row = el.parentElement;
            for (let depth = 0; depth < 8 && row; depth++) {
                const blob = (row.innerText || '').trim();
                if (blob.length > 15 && blob.includes(t)) {
                    out.push({ isin: t, raw: blob });
                    break;
                }
                row = row.parentElement;
            }
        }
        return out;
    }
    """


def fetch_oskar_weighting_etfs(
    *,
    dashboard_url: str = _DASHBOARD_URL,
    headless: bool = True,
    timeout_ms: int = 120_000,
) -> list[OskarWeightingEtf]:
    """
    Launch Chromium (TLS verification on). If login is required, sign in **manually**
    in the browser; the run continues once cockpit tabs («Aktuelle Gewichtung» /
    «Wertentwicklung») appear. With ``headless=True`` and a login gate, the browser
    is restarted **headed** once so you can complete Auth0. Then «Aktuelle Gewichtung»
    is opened and ETF rows are parsed.
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

            if _page_needs_login(page):
                logger.info("fetch_oskar_weighting_etfs: page needs login")
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

            logger.info("fetch_oskar_weighting_etfs: clicking weighting tab")
            _click_weighting_tab(page, timeout_ms=timeout_ms)
            _expand_collapsed_sections(page)

            logger.info("fetch_oskar_weighting_etfs: evaluating weighting etfs js")
            raw_rows = page.evaluate(_extract_weighting_etfs_js())
            if not isinstance(raw_rows, list):
                raw_rows = []

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
    ) -> list[OskarWeightingEtf]:
        """Same as :func:`fetch_oskar_weighting_etfs` (manual sign-in in the browser)."""
        return fetch_oskar_weighting_etfs(
            dashboard_url=dashboard_url,
            headless=headless,
            timeout_ms=timeout_ms,
        )
