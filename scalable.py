"""
Scalable Capital positions via the ``sc`` CLI (login, holdings, overnight, logout).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from common import CASH_PORTFOLIO
from logger import attach_color_stderr_handler_for_module
from utils import bucket_for_isin, portfolio as global_portfolio

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_SCALABLE = "scalable"
_SC_BIN = "sc"
_LOGIN_TIMEOUT_S = 300
_CMD_TIMEOUT_S = 60

_TAGESGELD_NAME = "Tagesgeld"
_TAGESGELD_FETCH_KEY = "__SCALABLE_TAGESGELD__"


@dataclass(frozen=True)
class ScalableHolding:
    """One broker holding or overnight cash row from ``sc``."""

    isin: str | None
    name: str
    shares: float | None
    value: float
    price: float | None
    is_tagesgeld: bool = False


global global_scalable_holdings
global_scalable_holdings: dict[str, ScalableHolding] = {}


class Scalable:
    """
    Session wrapper around ``sc``: login (streamed for device authorization),
    then holdings/overnight commands, then logout.
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

    def login(self) -> None:
        logger.info("scalable: starting sc login")
        self._sc = subprocess.Popen(
            [self._sc_bin, "login", "--local-read-only"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        chunks: list[str] = []
        try:
            assert self._sc.stdout is not None
            for line in self._sc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                chunks.append(line)
            rc = self._sc.wait(timeout=_LOGIN_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            self._sc.kill()
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
                "isin": isin,
                "name": name,
                "shares": shares,
                "value": value,
                "price": price,
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
        "isin": None,
        "name": _TAGESGELD_NAME,
        "shares": None,
        "value": balance,
        "price": None,
        "is_tagesgeld": True,
    }


def holdings_to_rows(holdings: list[dict[str, Any]]) -> dict[str, ScalableHolding]:
    rows: dict[str, ScalableHolding] = {}
    for item in holdings:
        isin = item["isin"]
        rows[isin] = ScalableHolding(
            isin=isin,
            name=item["name"],
            shares=item["shares"],
            value=item["value"],
            price=item["price"],
            is_tagesgeld=False,
        )
    return rows


def fetch_scalable_etfs(*, sc_bin: str = _SC_BIN) -> dict[str, ScalableHolding]:
    """
    Login with ``sc``, scrape broker holdings and overnight Tagesgeld, then logout.
    Stdout is streamed during login so the activation URL stays visible.
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
                name=tagesgeld["name"],
                shares=None,
                value=tagesgeld["value"],
                price=None,
                is_tagesgeld=True,
            )
        else:
            logger.info("scalable: overnight scan returned no Tagesgeld")
    finally:
        session.logout()
    return rows


def _is_portfolio_position_scalable_tagesgeld(position: dict[str, Any]) -> bool:
    pos_name = position.get("name") or position.get("Name") or ""
    pos_broker = position.get("broker") or position.get("Broker")
    return pos_name == _TAGESGELD_NAME and pos_broker == _SCALABLE


def update_scalable_etfs_in_portfolio() -> None:
    global global_scalable_holdings
    global_scalable_holdings = fetch_scalable_etfs()
    fetched = global_scalable_holdings
    if not fetched:
        logger.warning(
            "update_scalable_etfs_in_portfolio: no Scalable holdings fetched; "
            "leaving portfolio unchanged",
        )
        return

    fetched_by_isin = {
        holding.isin: holding
        for holding in fetched.values()
        if not holding.is_tagesgeld and holding.isin
    }
    fetched_tagesgeld = fetched.get(_TAGESGELD_FETCH_KEY)
    to_remove: list[tuple[str, dict[str, Any]]] = []
    matched_isins: set[str] = set()
    tagesgeld_matched = False

    for bucket, positions in global_portfolio.items():
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
                    position.pop("price", None)
                    tagesgeld_matched = True
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
            position["price"] = holding.price
            matched_isins.add(holding.isin)

    if fetched_tagesgeld is not None and not tagesgeld_matched:
        global_portfolio.setdefault(CASH_PORTFOLIO, []).append(
            {
                "name": _TAGESGELD_NAME,
                "ISIN": None,
                "shares": None,
                "value": fetched_tagesgeld.value,
                "broker": _SCALABLE,
                "dmem": None,
                "dmem_other": None,
                "usavn": None,
            }
        )
        logger.info(
            "update_scalable_etfs_in_portfolio: added Tagesgeld to %r (value=%s)",
            CASH_PORTFOLIO,
            fetched_tagesgeld.value,
        )

    for holding in fetched_by_isin.values():
        if holding.isin in matched_isins:
            continue
        bucket = bucket_for_isin(holding.isin)
        global_portfolio.setdefault(bucket, []).append(
            {
                "name": holding.name,
                "ISIN": holding.isin,
                "shares": holding.shares,
                "value": holding.value,
                "price": holding.price,
                "broker": _SCALABLE,
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
        logger.info(
            "update_scalable_etfs_in_portfolio: added missing ISIN %s to %r (value=%s)",
            holding.isin,
            bucket,
            holding.value,
        )

    for bucket, position in to_remove:
        global_portfolio[bucket].remove(position)
