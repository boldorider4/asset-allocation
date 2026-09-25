# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Scalable Capital positions via the ``sc`` CLI (login, holdings, overnight, logout).
"""

from __future__ import annotations

import getpass
import json
import logging
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from cli.common import BROKER, CASH_PORTFOLIO, DMEM, DMEM_OTHER, ISIN, NAME, PRICE, SHARES, USAVN, VALUE
from cli.logger import attach_color_stderr_handler_for_module
from utils import bucket_for_isin, cache_broker_quotes

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_SCALABLE = "scalable"
_SC_BIN = "sc"
_LOGIN_TIMEOUT_S = 300
_CMD_TIMEOUT_S = 60

# Browser-driven device login: activation output appears quickly, the rest
# of the budget covers credentials + the phone-side 2FA wait.
_ACTIVATE_WAIT_S = 60
_BROWSER_LOGIN_TIMEOUT_S = 300
_NAV_TIMEOUT_MS = 60_000
_BROWSER_POLL_S = 1.0
_SUBMIT_SETTLE_S = 30.0

# secure.scalable.capital rejects HeadlessChrome with a blank page; use a
# normal Chrome UA (same trick as the Oskar client).
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Background throttling stalls SPA logins while headless scraping continues.
_CHROMIUM_LAUNCH_ARGS = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)

_ACTIVATE_URL_RE = re.compile(
    r"https?://\S*activate\?user_code=([A-Za-z0-9][A-Za-z0-9-]*)"
)
_VERIFY_CODE_RE = re.compile(r"[Vv]erify the code\s+([A-Za-z0-9][A-Za-z0-9-]*)")

_CODE_INPUT_SELECTORS = (
    'input[name*="code"]',
    'input[id*="code"]',
    'input[name*="user_code"]',
    'input[autocomplete="one-time-code"]',
)
_LOGIN_EMAIL_SELECTORS = (
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
)
_LOGIN_PASSWORD_SELECTORS = ('input[type="password"]',)
_CONFIRM_PATTERNS = (
    re.compile(r"^\s*Bestätigen\s*$", re.I),
    re.compile(r"^\s*Verifizieren\s*$", re.I),
    re.compile(r"^\s*Weiter\s*$", re.I),
    re.compile(r"^\s*Confirm\s*$", re.I),
    re.compile(r"^\s*Verify\s*$", re.I),
    re.compile(r"^\s*Continue\s*$", re.I),
    re.compile(r"^\s*Log\s*in\s*$", re.I),
    re.compile(r"^\s*Anmelden\s*$", re.I),
)

_TAGESGELD_NAME = "Tagesgeld"
_TAGESGELD_FETCH_KEY = "__SCALABLE_TAGESGELD__"

_CASH_NAME = "Cash"
_CASH_FETCH_KEY = "__SCALABLE_CASH__"

# Keys of the intermediate sc-row dicts built by the parsers below. The
# lowercase ``isin`` is the ``sc`` wire shape (not the assets-file shape,
# where ``common.ISIN`` is ``"ISIN"``); ``NAME``/``SHARES``/``VALUE``/``PRICE``
# from ``common`` cover the rest, so only the wire-only keys live here.
_ROW_ISIN = "isin"
_ROW_IS_TAGESGELD = "is_tagesgeld"
_ROW_IS_CASH = "is_cash"


@dataclass(frozen=True)
class ScalableHolding:
    """One broker holding, overnight cash row, or cash-breakdown row from ``sc``."""

    isin: str | None
    name: str
    shares: float | None
    value: float
    price: float | None
    is_tagesgeld: bool = False
    is_cash: bool = False


def _stream_chunks(stream):
    """Yield a text stream one char at a time until EOF."""
    while True:
        data = stream.read(1)
        if not data:
            return
        yield data


def parse_activation_output(text: str) -> tuple[str, str] | None:
    """Extract ``(activation_url, user_code)`` from ``sc login`` output.

    Returns None until the output contains a complete activation URL.
    """
    match = _ACTIVATE_URL_RE.search(text or "")
    if match is None:
        return None
    url = match.group(0).rstrip(".,;)]")
    code = match.group(1)
    verify = _VERIFY_CODE_RE.search(text)
    if verify is not None and verify.group(1) != code:
        logger.warning(
            "scalable: activation URL code %r differs from verify line %r; trusting the URL",
            code,
            verify.group(1),
        )
    return url, code


def _require_interactive_terminal() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Scalable browser login needs an interactive terminal for the "
            "email/password prompt, but stdin is not a TTY. Run "
            "`asalloc update --fetch-scalable` in a real terminal "
            "instead of a pipe."
        )


def _prompt_scalable_email() -> str:
    """Read the Scalable email/username from the terminal (visible)."""
    _require_interactive_terminal()
    email = input("Scalable email: ").strip()
    while not email:
        email = input("Scalable email (must not be empty): ").strip()
    return email


def _prompt_scalable_password() -> str:
    """Read the Scalable password from the terminal without echoing it."""
    _require_interactive_terminal()
    password = getpass.getpass("Scalable password (hidden): ")
    while not password:
        password = getpass.getpass("Scalable password (hidden, must not be empty): ")
    return password


def _first_visible_locator(scope: Any, selectors: tuple[str, ...]) -> Any | None:
    """First visible locator among *selectors* in *scope* (page or frame)."""
    for sel in selectors:
        try:
            loc = scope.locator(sel)
        except Exception:
            continue
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _find_visible_button(scope: Any) -> Any | None:
    """Visible confirm/submit button in *scope*, or None.

    Text-matched confirm buttons win over a bare ``button[type=submit]``:
    confirmation screens pair them with lookalike cancel buttons
    (e.g. Abbrechen first, Bestätigen second) and grabbing ``.first``
    would click the wrong one.
    """
    for pat in _CONFIRM_PATTERNS:
        try:
            loc = scope.get_by_role("button", name=pat)
        except Exception:
            continue
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    try:
        btn = scope.locator('button[type="submit"]')
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
    except Exception:
        pass
    return None


def _find_confirm_button(page: Any) -> Any | None:
    """Visible confirm button (Bestätigen/Confirm/…) on any frame, or None.

    Text match only — never a bare submit fallback, so cancel buttons
    can never be picked up here.
    """
    for fr in page.frames:
        try:
            for pat in _CONFIRM_PATTERNS:
                try:
                    loc = fr.get_by_role("button", name=pat)
                except Exception:
                    continue
                try:
                    if loc.count() > 0 and loc.first.is_visible():
                        return loc.first
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("scalable: confirm scan skipped frame: %s", exc)
            continue
    return None


_CODE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{3,}$")


def _find_code_display(page: Any) -> Any | None:
    """Visible text input acting as the code field, or None.

    The real confirmation screen renders the device code prefilled in a
    bare ``<input type="text">`` (no name/id/placeholder), so attribute
    selectors cannot see it — match an empty field or a code-shaped
    value instead. Callers only invoke this on confirmation screens
    (confirm button present), so stray text boxes elsewhere are out
    of reach.
    """
    for fr in page.frames:
        try:
            loc = fr.locator('input[type="text"]')
        except Exception:
            continue
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(count):
            try:
                item = loc.nth(i)
                if not item.is_visible():
                    continue
                value = item.input_value(timeout=5_000)
            except Exception:
                continue
            if not isinstance(value, str):
                continue
            if not value.strip() or _CODE_VALUE_RE.fullmatch(value.strip()):
                return item
    return None


def _ensure_code(page: Any, user_code: str) -> bool:
    """Reconcile the confirmation code field with ``user_code``.

    Fills it when empty; raises on a mismatch (the page itself warns to
    proceed only when both codes match — the wrong code would authorize
    somebody else's session). Returns True when a code field was found.
    """
    display = _find_code_display(page)
    if display is not None:
        current = _locator_input_value(display).strip()
        if not current:
            logger.info("scalable: entering activation code")
            display.fill(user_code, timeout=15_000)
        elif current.upper() != user_code.strip().upper():
            raise RuntimeError(
                f"scalable: activation code mismatch: page shows {current!r}; "
                "aborting instead of authorizing an unknown session"
            )
        return True
    for fr in page.frames:
        try:
            code = _first_visible_locator(fr, _CODE_INPUT_SELECTORS)
        except Exception as exc:
            logger.debug("scalable: code scan skipped frame: %s", exc)
            continue
        if code is None:
            continue
        if not _locator_input_value(code).strip():
            logger.info("scalable: entering activation code")
            code.fill(user_code, timeout=15_000)
        return True
    return False


def _find_login_controls(page: Any) -> tuple[Any | None, Any | None, Any | None]:
    """Return ``(email, password, submit)`` locators for a login form.

    Each poll looks for whatever is visible (identifier-first flows show
    email before password). Returns ``(None, None, None)`` when no login
    screen is showing.
    """
    for fr in page.frames:
        try:
            email = _first_visible_locator(fr, _LOGIN_EMAIL_SELECTORS)
            password = _first_visible_locator(fr, _LOGIN_PASSWORD_SELECTORS)
            if email is None and password is None:
                continue
            return email, password, _find_visible_button(fr)
        except Exception as exc:
            logger.debug("scalable: login scan skipped frame: %s", exc)
            continue
    return None, None, None


def _locator_input_value(locator: Any) -> str:
    try:
        current = locator.input_value(timeout=5_000)
    except Exception:
        return ""
    return current if isinstance(current, str) else ""


def _screenshot(page: Any, *, tag: str) -> None:
    try:
        path = Path(tempfile.gettempdir()) / f"asalloc-scalable-login-{tag}.png"
        page.screenshot(path=str(path))
        logger.warning("scalable: browser login screenshot at %s", path)
    except Exception as exc:
        logger.debug("scalable: screenshot failed: %s", exc)


def playwright_device_login(
    url: str,
    user_code: str,
    *,
    sc_done: Callable[[], bool],
    timeout_s: float = _BROWSER_LOGIN_TIMEOUT_S,
) -> None:
    """Complete an ``sc`` device login in headless Chromium; destroy it after.

    Fills the activation code, prompts for email/password in the terminal
    (like the Oskar client) only when a login form shows up, submits, then
    waits for the phone-side 2FA while polling ``sc_done`` — the ``sc``
    process exiting is the primary success signal. The browser (the whole
    session) is closed before returning. Raises on timeout or failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "playwright is required for Scalable browser login. "
            "Install with pip and run: playwright install chromium"
        ) from e

    deadline = time.monotonic() + timeout_s
    submitted: set[str] = set()
    with sync_playwright() as p:
        logger.info("scalable: launching headless browser for device login")
        browser = p.chromium.launch(headless=True, args=list(_CHROMIUM_LAUNCH_ARGS))
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT,
                ignore_https_errors=False,
                locale="de-DE",
            )
            context.set_default_navigation_timeout(_NAV_TIMEOUT_MS)
            context.set_default_timeout(_NAV_TIMEOUT_MS)
            page = context.new_page()
            logger.info("scalable: opening activation page")
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            for state in ("load", "networkidle"):
                try:
                    page.wait_for_load_state(state, timeout=15_000)
                except Exception:
                    pass
            while time.monotonic() < deadline:
                if sc_done():
                    logger.info("scalable: device login confirmed url=%s", page.url)
                    return
                # Stage 1: login form first — its submit button belongs to
                # the form, so credential handling owns this iteration.
                email, password, submit = _find_login_controls(page)
                if email is not None or password is not None:
                    if email is not None and not _locator_input_value(email).strip():
                        email.fill(_prompt_scalable_email(), timeout=15_000)
                        page.wait_for_timeout(300)
                    if password is not None and password.is_visible():
                        if not _locator_input_value(password).strip():
                            password.fill(_prompt_scalable_password(), timeout=15_000)
                            page.wait_for_timeout(300)
                    key = (
                        _locator_input_value(email) if email is not None else "",
                        _locator_input_value(password) if password is not None else "",
                    )
                    anchor = password if password is not None else email
                    if key in submitted:
                        # Rejected and re-shown: clear so the next loop
                        # re-prompts instead of resubmitting the same pair.
                        logger.warning(
                            "scalable: login screen reappeared; clearing for a fresh prompt"
                        )
                        try:
                            if email is not None:
                                email.fill("", timeout=5_000)
                            if password is not None:
                                password.fill("", timeout=5_000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                        continue
                    if submit is not None and submit.is_visible():
                        submit.click(timeout=15_000)
                    elif anchor is not None:
                        anchor.press("Enter", timeout=15_000)
                    else:
                        page.wait_for_timeout(800)
                        continue
                    submitted.add(key)
                    if _await_sc_done(page, sc_done=sc_done, deadline=deadline):
                        logger.info(
                            "scalable: device login confirmed url=%s", page.url
                        )
                        return
                    continue
                # Stage 2: device confirmation screen — the code renders
                # prefilled, so the click is the whole action (a mismatch
                # aborts instead of authorizing an unknown session).
                confirm = _find_confirm_button(page)
                if confirm is not None:
                    _ensure_code(page, user_code)
                    logger.info("scalable: confirming device login")
                    confirm.click(timeout=15_000)
                    page.wait_for_timeout(2_000)
                    continue
                # Stage 3: neither form is showing — 2FA happens on the
                # phone now; just wait for sc to observe the confirmation.
                page.wait_for_timeout(int(_BROWSER_POLL_S * 1000))
        except Exception:
            try:
                _screenshot(page, tag="failure")
            except Exception:
                pass
            raise
        finally:
            # The session is destroyed here either way.
            try:
                browser.close()
            except Exception:
                pass
    raise RuntimeError(
        f"scalable: browser login timed out after {timeout_s:.0f}s "
        "waiting for the phone-side 2FA confirmation."
    )


def _await_sc_done(
    page: Any, *, sc_done: Callable[[], bool], deadline: float
) -> bool:
    """Wait for the ``sc`` process to observe the confirmation.

    Returns True on success; False when the wait ran out but the overall
    deadline still holds (caller re-scans the page).
    """
    attempt_end = min(deadline, time.monotonic() + _SUBMIT_SETTLE_S)
    while time.monotonic() < attempt_end:
        if sc_done():
            return True
        try:
            page.wait_for_timeout(int(_BROWSER_POLL_S * 1000))
        except Exception:
            return False
    return False


class Scalable:
    """
    Session wrapper around ``sc``: browser-driven device login, then
    holdings/overnight commands, then logout.

    The ``sc`` activation URL/code are parsed from its streamed output and
    completed in a headless Chromium session instead of the user's own
    browser: credentials are prompted in the terminal, 2FA stays on the
    phone, and the browser session is destroyed afterwards.
    """

    def __init__(self, sc_bin: str = _SC_BIN) -> None:
        self._sc_bin = sc_bin
        self._sc: subprocess.Popen[str] | None = None
        self._logged_in = False

    def __enter__(self) -> Scalable:
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()

    def login(self, *, timeout_s: float = _LOGIN_TIMEOUT_S) -> None:
        """Device login completed in a headless browser session.

        Keeps ``sc login`` running (it must observe the server-side
        confirmation), parses the activation URL/code from its streamed
        output, drives the confirmation + credential prompts in Chromium,
        and destroys the browser session afterwards. 2FA stays on the
        phone: the flow waits for ``sc`` to exit successfully.
        """
        logger.info("scalable: starting sc login (browser-driven)")
        proc = subprocess.Popen(
            [self._sc_bin, "login", "--local-read-only"],
            # Nothing to type here: the browser answers for us. Closed
            # stdin fails fast instead of hanging on a prompt.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._sc = proc
        chunks: list[str] = []

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                for data in _stream_chunks(proc.stdout):
                    sys.stdout.write(data)
                    chunks.append(data)
            except (ValueError, OSError) as exc:
                logger.debug("scalable: login relay ended: %s", exc)
            finally:
                try:
                    sys.stdout.flush()
                except (ValueError, OSError):
                    pass

        reader = threading.Thread(target=_reader, daemon=True)
        start = time.monotonic()
        overall_end = start + min(timeout_s, _LOGIN_TIMEOUT_S)
        activate_end = start + min(_ACTIVATE_WAIT_S, timeout_s)
        activation: tuple[str, str] | None = None
        try:
            reader.start()
            while time.monotonic() < activate_end and activation is None:
                if proc.poll() is not None:
                    break
                activation = parse_activation_output("".join(chunks))
                if activation is None:
                    time.sleep(0.5)
            if activation is None:
                if proc.poll() is not None:
                    # Drain the relay so the error shows sc's last words.
                    reader.join(timeout=5)
                    raise RuntimeError(
                        "sc login exited before printing an activation URL: "
                        + "".join(chunks).strip()
                    )
                raise RuntimeError(
                    "sc login printed no activation URL in time; "
                    "confirm it manually in your own browser"
                )
            url, user_code = activation
            logger.info("scalable: activating %s in headless browser", url)
            playwright_device_login(
                url,
                user_code,
                sc_done=lambda: proc.poll() is not None,
                timeout_s=max(1.0, overall_end - time.monotonic()),
            )
            reader.join(timeout=_LOGIN_TIMEOUT_S)
            rc = proc.wait(timeout=_LOGIN_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise RuntimeError("sc login timed out waiting for authorization") from e
        finally:
            self._sc = None
        if rc != 0:
            raise RuntimeError(
                f"sc login failed with exit {rc}: {''.join(chunks).strip()}"
            )
        self._logged_in = True
        logger.info("scalable: login complete")

    def logout(self) -> None:
        if not self._logged_in:
            return
        logger.info("scalable: sc logout")
        try:
            self._run(["logout"], timeout=_CMD_TIMEOUT_S)
        except Exception as exc:
            logger.warning("scalable: logout error: %s", exc)
        finally:
            self._logged_in = False

    def holdings_json(self) -> str:
        self._require_session()
        return self._run(["broker", "holdings", "--json"], timeout=_CMD_TIMEOUT_S)

    def overnight_text(self) -> str:
        self._require_session()
        return self._run(["overnight"], timeout=_CMD_TIMEOUT_S)

    def cash_breakdown_json(self) -> str:
        self._require_session()
        return self._run(["broker", "cash-breakdown", "--json"], timeout=_CMD_TIMEOUT_S)

    def _require_session(self) -> None:
        if not self._logged_in:
            raise RuntimeError("sc session is not logged in")

    def _run(self, args: list[str], *, timeout: int) -> str:
        self._sc = subprocess.Popen(
            [self._sc_bin, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = self._sc.communicate(timeout=timeout)
            rc = self._sc.returncode
        except subprocess.TimeoutExpired as e:
            self._sc.kill()
            raise RuntimeError(f"sc {' '.join(args)} timed out") from e
        finally:
            self._sc = None
        if rc != 0:
            raise RuntimeError(
                f"sc {' '.join(args)} failed with exit {rc}: {(err or out).strip()}"
            )
        return out


def _find_items(node: Any) -> list[Any] | None:
    """Depth-first search for the holdings array inside an ``sc`` JSON payload."""
    if isinstance(node, list):
        return node if any(isinstance(x, dict) and "isin" in x for x in node) else None
    if not isinstance(node, dict):
        return None
    if "items" in node:
        items = node["items"]
        if items is None:
            return []
        if not isinstance(items, list):
            raise ValueError("sc broker holdings items must be an array")
        return items
    if node.get("count") == 0:
        return []
    for value in node.values():
        found = _find_items(value)
        if found is not None:
            return found
    return None


def _holdings_items(payload: Any) -> list[Any]:
    """
    Locate the holdings array. ``sc`` wraps payloads in an ``ok``/``command``/``data``
    envelope and a broker-context ``result`` object, so search instead of assuming
    one nesting.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise ValueError(
            f"sc broker holdings reported failure: {json.dumps(payload)[:300]}"
        )
    items = _find_items(payload)
    if items is None:
        keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"sc broker holdings JSON has no items array (got {keys})")
    return items


def parse_holdings_json(raw: str) -> list[dict[str, Any]]:
    """Parse ``sc broker holdings --json`` into raw holding dicts."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"sc broker holdings output is not JSON: {e}") from e
    items = _holdings_items(payload)
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"sc broker holdings item {i} must be an object")
        quote_ccy = str(item.get("quote_currency") or "").upper()
        val_ccy = str(item.get("valuation_currency") or "").upper()
        if quote_ccy and quote_ccy != "EUR":
            raise ValueError(
                f"sc broker holdings item {i} quote_currency {quote_ccy!r} is not EUR"
            )
        if val_ccy and val_ccy != "EUR":
            raise ValueError(
                f"sc broker holdings item {i} valuation_currency {val_ccy!r} is not EUR"
            )
        isin = str(item.get("isin") or "").strip()
        if not isin:
            raise ValueError(f"sc broker holdings item {i} missing isin")
        try:
            shares = float(item["quantity"])
            value = float(item["valuation"])
            price = float(item["quote_mid_price"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(
                f"sc broker holdings item {i} missing or invalid quantity/valuation/quote_mid_price"
            ) from e
        name = str(item.get("name") or isin)
        logger.info(
            "scalable scrape: ISIN=%s name=%s quote_mid_price=%s valuation=%s quantity=%s",
            isin,
            name,
            price,
            value,
            shares,
        )
        rows.append(
            {
                _ROW_ISIN: isin,
                NAME: name,
                SHARES: shares,
                VALUE: value,
                PRICE: price,
            }
        )
    return rows


def _find_balance_object(node: Any) -> dict[str, Any] | None:
    """Depth-first search for the savings-account object in a JSON payload."""
    if isinstance(node, dict):
        if "balance" in node:
            return node
        for value in node.values():
            found = _find_balance_object(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_balance_object(value)
            if found is not None:
                return found
    return None


def parse_overnight_text(raw: str) -> dict[str, str]:
    """
    Parse ``sc overnight`` output into flat string fields.

    Plain output is ``key: value`` lines; JSON output nests the savings account
    inside an ``ok``/``command``/``data`` envelope.
    """
    text = raw.strip()
    if text.startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"sc overnight output is not JSON: {e}") from e
        account = _find_balance_object(payload)
        if account is None:
            return {}
        return {str(k): str(v) for k, v in account.items() if v is not None}
    data: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def overnight_tagesgeld_row(raw: str) -> dict[str, Any] | None:
    """Return a Tagesgeld dict from overnight stdout, or None if absent."""
    text = raw.strip()
    if not text:
        return None
    data = parse_overnight_text(text)
    name = data.get("account_name") or ""
    balance_s = data.get("balance")
    if not balance_s:
        return None
    if name and name != _TAGESGELD_NAME:
        logger.warning(
            "scalable: overnight account_name=%r (expected %r); using as Tagesgeld",
            name,
            _TAGESGELD_NAME,
        )
    try:
        balance = float(balance_s)
    except ValueError as e:
        raise ValueError(f"sc overnight balance is not a number: {balance_s!r}") from e
    logger.info(
        "scalable scrape: ISIN=%s name=%s quote_mid_price=%s valuation=%s quantity=%s",
        None,
        _TAGESGELD_NAME,
        None,
        balance,
        None,
    )
    return {
        _ROW_ISIN: None,
        NAME: _TAGESGELD_NAME,
        SHARES: None,
        VALUE: balance,
        PRICE: None,
        _ROW_IS_TAGESGELD: True,
    }


def _find_cash_balance_object(node: Any) -> dict[str, Any] | None:
    """Depth-first search for the cash-breakdown object in a JSON payload."""
    if isinstance(node, dict):
        if "cash_balance" in node:
            return node
        for value in node.values():
            found = _find_cash_balance_object(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_cash_balance_object(value)
            if found is not None:
                return found
    return None


def parse_cash_breakdown_json(raw: str) -> dict[str, str]:
    """
    Parse ``sc broker cash-breakdown --json`` into flat string fields.

    The breakdown nests inside an ``ok``/``command``/``data`` envelope;
    only the object carrying ``cash_balance`` is returned.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"sc broker cash-breakdown output is not JSON: {e}") from e
    account = _find_cash_balance_object(payload)
    if account is None:
        return {}
    return {str(k): str(v) for k, v in account.items() if v is not None}


def cash_breakdown_cash_row(raw: str) -> dict[str, Any] | None:
    """Return a Cash dict from cash-breakdown stdout, or None if absent."""
    text = raw.strip()
    if not text:
        return None
    data = parse_cash_breakdown_json(text)
    balance_s = data.get("cash_balance")
    if not balance_s:
        return None
    try:
        balance = float(balance_s)
    except ValueError as e:
        raise ValueError(f"sc cash-breakdown cash_balance is not a number: {balance_s!r}") from e
    logger.info(
        "scalable scrape: ISIN=%s name=%s quote_mid_price=%s valuation=%s quantity=%s",
        None,
        _CASH_NAME,
        None,
        balance,
        None,
    )
    return {
        _ROW_ISIN: None,
        NAME: _CASH_NAME,
        SHARES: None,
        VALUE: balance,
        PRICE: None,
        _ROW_IS_CASH: True,
    }


def holdings_to_rows(holdings: list[dict[str, Any]]) -> dict[str, ScalableHolding]:
    rows: dict[str, ScalableHolding] = {}
    for item in holdings:
        isin = item[_ROW_ISIN]
        rows[isin] = ScalableHolding(
            isin=isin,
            name=item[NAME],
            shares=item[SHARES],
            value=item[VALUE],
            price=item[PRICE],
            is_tagesgeld=False,
        )
    return rows


def fetch_scalable_etfs(
    *, sc_bin: str = _SC_BIN
) -> dict[str, ScalableHolding]:
    """
    Login with ``sc`` via a headless browser device activation, scrape
    broker holdings, overnight Tagesgeld, and the cash-breakdown Cash
    balance, then logout. Credentials are prompted in the terminal, 2FA
    stays on the phone.
    """
    rows: dict[str, ScalableHolding] = {}
    session = Scalable(sc_bin=sc_bin)
    try:
        session.login()
        holdings_raw = session.holdings_json()
        overnight_raw = session.overnight_text()
        rows = holdings_to_rows(parse_holdings_json(holdings_raw))
        tagesgeld = overnight_tagesgeld_row(overnight_raw)
        if tagesgeld is not None:
            rows[_TAGESGELD_FETCH_KEY] = ScalableHolding(
                isin=None,
                name=tagesgeld[NAME],
                shares=None,
                value=tagesgeld[VALUE],
                price=None,
                is_tagesgeld=True,
            )
        else:
            logger.info("scalable: overnight scan returned no Tagesgeld")
        try:
            cash_breakdown_raw = session.cash_breakdown_json()
        except Exception as exc:
            logger.warning("scalable: cash-breakdown unavailable, skipping Cash: %s", exc)
        else:
            cash = cash_breakdown_cash_row(cash_breakdown_raw)
            if cash is not None:
                rows[_CASH_FETCH_KEY] = ScalableHolding(
                    isin=None,
                    name=cash[NAME],
                    shares=None,
                    value=cash[VALUE],
                    price=None,
                    is_cash=True,
                )
            else:
                logger.info("scalable: cash-breakdown returned no cash_balance")
    finally:
        session.logout()
    return rows


def _is_portfolio_position_scalable_tagesgeld(position: dict[str, Any]) -> bool:
    pos_name = position.get("name") or position.get("Name") or ""
    pos_broker = position.get("broker") or position.get("Broker")
    return pos_name == _TAGESGELD_NAME and pos_broker == _SCALABLE


def _is_portfolio_position_scalable_cash(position: dict[str, Any]) -> bool:
    pos_name = position.get("name") or position.get("Name") or ""
    pos_broker = position.get("broker") or position.get("Broker")
    return pos_name == _CASH_NAME and pos_broker == _SCALABLE


def update_scalable_etfs_in_portfolio(ctx: RuntimeContext) -> set[str]:
    ctx.scalable_holdings = fetch_scalable_etfs()
    fetched = ctx.scalable_holdings
    if not fetched:
        logger.warning(
            "update_scalable_etfs_in_portfolio: no Scalable holdings fetched; "
            "leaving portfolio unchanged",
        )
        return

    fetched_by_isin = {
        holding.isin: holding
        for holding in fetched.values()
        if not holding.is_tagesgeld and not holding.is_cash and holding.isin
    }
    fetched_tagesgeld = fetched.get(_TAGESGELD_FETCH_KEY)
    fetched_cash = fetched.get(_CASH_FETCH_KEY)
    to_remove: list[tuple[str, dict[str, Any]]] = []
    matched_isins: set[str] = set()
    tagesgeld_matched = False
    cash_matched = False

    for bucket, positions in ctx.portfolio.items():
        for position in positions:
            pos_broker = position.get("broker") or position.get("Broker")
            if pos_broker != _SCALABLE:
                continue
            if _is_portfolio_position_scalable_tagesgeld(position):
                if fetched_tagesgeld is None:
                    to_remove.append((bucket, position))
                    logger.info(
                        "update_scalable_etfs_in_portfolio: removing stale Scalable Tagesgeld from %r",
                        bucket,
                    )
                else:
                    position["value"] = fetched_tagesgeld.value
                    position["shares"] = None
                    position["ISIN"] = None
                    tagesgeld_matched = True
                continue
            if _is_portfolio_position_scalable_cash(position):
                if fetched_cash is None:
                    to_remove.append((bucket, position))
                    logger.info(
                        "update_scalable_etfs_in_portfolio: removing stale Scalable Cash from %r",
                        bucket,
                    )
                else:
                    position["value"] = fetched_cash.value
                    position["shares"] = None
                    position["ISIN"] = None
                    cash_matched = True
                continue
            pos_isin = position.get("ISIN") or position.get("isin")
            holding = fetched_by_isin.get(pos_isin)
            if holding is None:
                to_remove.append((bucket, position))
                logger.info(
                    "update_scalable_etfs_in_portfolio: removing stale Scalable ISIN %s from %r",
                    pos_isin,
                    bucket,
                )
                continue
            position["value"] = holding.value
            position["shares"] = holding.shares
            matched_isins.add(holding.isin)

    if fetched_tagesgeld is not None and not tagesgeld_matched:
        ctx.portfolio.setdefault(CASH_PORTFOLIO, []).append(
            {
                NAME: _TAGESGELD_NAME,
                ISIN: None,
                SHARES: None,
                VALUE: fetched_tagesgeld.value,
                BROKER: _SCALABLE,
                DMEM: None,
                DMEM_OTHER: None,
                USAVN: None,
            }
        )
        logger.info(
            "update_scalable_etfs_in_portfolio: added Tagesgeld to %r (value=%s)",
            CASH_PORTFOLIO,
            fetched_tagesgeld.value,
        )

    if fetched_cash is not None and not cash_matched:
        ctx.portfolio.setdefault(CASH_PORTFOLIO, []).append(
            {
                NAME: _CASH_NAME,
                ISIN: None,
                SHARES: None,
                VALUE: fetched_cash.value,
                BROKER: _SCALABLE,
                DMEM: None,
                DMEM_OTHER: None,
                USAVN: None,
            }
        )
        logger.info(
            "update_scalable_etfs_in_portfolio: added Cash to %r (value=%s)",
            CASH_PORTFOLIO,
            fetched_cash.value,
        )

    for holding in fetched_by_isin.values():
        if holding.isin in matched_isins:
            continue
        bucket = bucket_for_isin(ctx, holding.isin)
        ctx.portfolio.setdefault(bucket, []).append(
            {
                NAME: holding.name,
                ISIN: holding.isin,
                SHARES: holding.shares,
                VALUE: holding.value,
                BROKER: _SCALABLE,
                DMEM: 1,
                DMEM_OTHER: 1,
                USAVN: 0,
            }
        )
        logger.info(
            "update_scalable_etfs_in_portfolio: added missing ISIN %s to %r (value=%s)",
            holding.isin,
            bucket,
            holding.value,
        )

    cache_broker_quotes(
        ctx,
        {holding.isin: holding.price for holding in fetched_by_isin.values()}
    )
    # Staged splits are trusted as-is: a holdings update changes values,
    # shares and composition, never the ETF's own country/sector mix, so
    # there is nothing to wipe and nothing to refetch here. Splits refresh
    # on explicit --fetch-geosplit/--fetch-sectorsplit runs.

    for bucket, position in to_remove:
        ctx.portfolio[bucket].remove(position)

    return matched_isins
