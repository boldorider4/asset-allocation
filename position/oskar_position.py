"""
OSKAR portfolio positions (JustETF pricing) plus a Playwright-based client for the
logged-in cockpit «Aktuelle Gewichtung» ETF list.

Credentials live in ``oskar.cred.ini`` (see ``load_oskar_credentials``). After
``pip install`` run ``playwright install chromium`` once so the browser binary
is available.
"""

from __future__ import annotations

import configparser
import getpass
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)

# Default path next to the working directory (same pattern as ``cache.json`` in factory).
_DEFAULT_CRED_FILENAME = "oskar.cred.ini"
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
class OskarCredentials:
    email: str
    password: str


@dataclass(frozen=True)
class OskarWeightingEtf:
    """One ETF line from «Aktuelle Gewichtung» (leaf row with an ISIN)."""

    isin: str
    name: str
    weight_pct: float | None
    value_eur: float | None
    raw_text: str


def load_oskar_credentials(
    path: str | Path | None = None,
    *,
    section: str = "oskar",
) -> OskarCredentials:
    """
    Read ``email`` from an INI file and ``password`` if present. If ``password``
    is missing or empty in the INI, uses ``OSKAR_PASSWORD`` from the environment;
    if still empty, prompts interactively (no echo).

    Example ``oskar.cred.ini``::

        [oskar]
        email = you@example.com
        password = your-secret
    """
    ini_path = Path(path) if path is not None else Path.cwd() / _DEFAULT_CRED_FILENAME
    if not ini_path.is_file():
        raise FileNotFoundError(f"OSKAR credentials file not found: {ini_path}")

    cfg = configparser.ConfigParser()
    cfg.read(ini_path, encoding="utf-8")
    if section not in cfg:
        raise KeyError(f"INI section [{section}] missing in {ini_path}")

    sec = cfg[section]
    email = (sec.get("email") or sec.get("username") or "").strip()
    password = (sec.get("password") or "").strip()
    if not password:
        password = os.environ.get("OSKAR_PASSWORD", "").strip()
    if not password:
        logger.info(
            "OSKAR credentials: password not in %s or OSKAR_PASSWORD; prompting (getpass)",
            ini_path,
        )
        password = getpass.getpass(f"OSKAR password [{section}] ({ini_path}): ").strip()
    if not email:
        raise ValueError(f"{ini_path}: [{section}] needs non-empty email (or username)")
    if not password:
        raise ValueError(
            f"{ini_path}: [{section}] password is empty (ini, OSKAR_PASSWORD env, and prompt)"
        )

    return OskarCredentials(email=email, password=password)


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


def _try_dismiss_cookie_layer(page: Any) -> None:
    """Best-effort consent / overlay dismissal (Sourcepoint / generic)."""
    candidates = (
        'button:has-text("Alle akzeptieren")',
        'button:has-text("Akzeptieren")',
        'button:has-text("Zustimmen")',
        '[aria-label="Close"]',
        "button.sp_choice_allow",
    )
    for sel in candidates:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(300)
            except Exception:
                pass


def _auth0_login(page: Any, creds: OskarCredentials, *, timeout_ms: int) -> None:
    logger.info("OSKAR Auth0 login: start url=%s", page.url)

    email_selectors = (
        'input[type="email"]',
        'input[name="username"]',
        "input#username",
        'input[inputmode="email"]',
        'input[autocomplete="username"]',
    )
    filled_email = False
    for sel in email_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            logger.info("OSKAR Auth0 login: found email/username field selector=%r", sel)
            loc.first.wait_for(state="visible", timeout=timeout_ms)
            loc.first.fill(creds.email)
            filled_email = True
            logger.info("OSKAR Auth0 login: filled email/username field")
            break
    if not filled_email:
        logger.error("OSKAR Auth0 login: no email/username input matched")
        raise RuntimeError("OSKAR login: could not find email / username field (Auth0)")

    # Identifier-first flow: Continue then password.
    clicked_continue = False
    for label in ("Continue", "Weiter", "Fortfahren", "Next"):
        btn = page.get_by_role("button", name=re.compile(f"^{re.escape(label)}$", re.I))
        if btn.count() > 0:
            try:
                btn.first.click(timeout=5000)
                clicked_continue = True
                logger.info("OSKAR Auth0 login: clicked identifier-first button label=%r", label)
                break
            except Exception as exc:
                logger.debug(
                    "OSKAR Auth0 login: identifier-first button %r click failed: %s",
                    label,
                    exc,
                )
    if not clicked_continue:
        logger.info(
            "OSKAR Auth0 login: no identifier-first Continue button (password may be on same page)"
        )

    logger.info("OSKAR Auth0 login: waiting for password field")
    pwd = page.locator('input[type="password"]')
    pwd.first.wait_for(state="visible", timeout=timeout_ms)
    pwd.first.fill(creds.password)
    logger.info("OSKAR Auth0 login: filled password field")

    logger.info("OSKAR Auth0 login: clicking submit")
    submit = page.locator(
        'button[type="submit"], button[name="submit"], '
        'button[data-action-button-primary="true"]'
    )
    submit.first.click()

    # Wait until we are back on the OSKAR app host (or dashboard path).
    logger.info("OSKAR Auth0 login: waiting for redirect to oskar app host")
    page.wait_for_function(
        """() => {
            const h = location.hostname;
            return h.includes('mein.oskar.de')
                || h.includes('login.oskar.de')
                || h.endsWith('oskar.de');
        }""",
        timeout=timeout_ms,
    )
    logger.info("OSKAR Auth0 login: done url=%s", page.url)


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
    creds: OskarCredentials,
    *,
    cred_path: str | Path | None = None,
    dashboard_url: str = _DASHBOARD_URL,
    headless: bool = True,
    timeout_ms: int = 10_000,
) -> list[OskarWeightingEtf]:
    """
    Launch Chromium (TLS verification on), log in via Auth0, open the cockpit
    dashboard, activate «Aktuelle Gewichtung», expand nested sections, and return
    parsed ETF rows.

    ``cred_path`` is only used in error messages when re-reading is not needed;
    credentials are passed in ``creds``.
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
            _try_dismiss_cookie_layer(page)

            if _page_needs_login(page):
                logger.info("fetch_oskar_weighting_etfs: page needs login")
                _auth0_login(page, creds, timeout_ms=timeout_ms)
                # Avoid ``networkidle``: cockpit SPAs keep analytics / long-poll traffic
                # open so ``networkidle`` often never fires (looks like a hang).
                page.goto(dashboard_url, wait_until="load", timeout=timeout_ms)
            else:
                try:
                    page.wait_for_load_state("load", timeout=timeout_ms)
                except Exception:
                    pass

            logger.info("fetch_oskar_weighting_etfs: dismissing cookie layer")
            _try_dismiss_cookie_layer(page)
            _try_dismiss_cookie_layer(page)

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
            browser.close()

    _ = cred_path
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
    def default_credentials_path() -> Path:
        return Path.cwd() / _DEFAULT_CRED_FILENAME

    @staticmethod
    def load_credentials(
        path: str | Path | None = None,
        *,
        section: str = "oskar",
    ) -> OskarCredentials:
        return load_oskar_credentials(path, section=section)

    @staticmethod
    def fetch_weighting_etfs(
        creds: OskarCredentials | None = None,
        *,
        cred_path: str | Path | None = None,
        dashboard_url: str = _DASHBOARD_URL,
        headless: bool = True,
        timeout_ms: int = 10_000,
    ) -> list[OskarWeightingEtf]:
        """
        Static entry point: same as :func:`fetch_oskar_weighting_etfs`, but loads
        credentials from ``cred_path`` (or ``./oskar.cred.ini``) when ``creds``
        is omitted.
        """
        if creds is None:
            creds = load_oskar_credentials(cred_path)
        return fetch_oskar_weighting_etfs(
            creds,
            cred_path=cred_path,
            dashboard_url=dashboard_url,
            headless=headless,
            timeout_ms=timeout_ms,
        )
