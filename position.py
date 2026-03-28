"""
Position pricing: abstract base plus Yahoo Finance and JustETF implementations.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

import yfinance as yf
from yfinance.exceptions import YFRateLimitError


class Position(ABC):
    """ISIN-based price lookup: fast quote vs last historical close."""

    def __init__(self, isin: str) -> None:
        self._isin = isin

    @property
    def isin(self) -> str:
        return self._isin

    def last_price(self) -> float:
        p = self._fast_info_price()
        if p is None:
            raise RuntimeError(f"No fast/quote price for ISIN {self._isin}")
        return float(p)

    def price_history(self) -> float:
        p = self._history_last_close()
        if p is None:
            raise RuntimeError(f"No historical close for ISIN {self._isin}")
        return float(p)

    @abstractmethod
    def _fast_info_price(self) -> float | None:
        """Current/quick price (e.g. Yahoo fast_info or JustETF latestQuote)."""
        ...

    @abstractmethod
    def _history_last_close(self) -> float | None:
        """Last available daily close from history/chart series."""
        ...


class YFinancePosition(Position):
    """
    Yahoo Finance via yfinance (ISIN as ticker symbol where supported).
    Quotes are returned in EUR: for USD-listed instruments, Yahoo's
    ``EURUSD=X`` spot (USD per 1 EUR) is stored in ``_eur_usd_rate`` and
    ``_fast_info_price`` / ``_history_last_close`` divide raw USD by that rate.
    """

    _EURUSD_SYMBOL = "EURUSD=X"

    def __init__(self, isin: str) -> None:
        super().__init__(isin)
        self._retries = 10
        self._delay_s = 1
        self._ticker: yf.Ticker | None = None
        self._listing_currency: str | None = None
        self._eur_usd_rate: float | None = None

        for attempt in range(self._retries):
            try:
                self._ticker = yf.Ticker(isin)
                break
            except YFRateLimitError as e:
                if attempt + 1 < self._retries:
                    time.sleep(self._delay_s)
                    continue
                raise RuntimeError(
                    f"Yahoo Finance rate limit after {self._retries} attempts "
                    f"for ISIN {self._isin}"
                ) from e

        if self._ticker is None:
            raise RuntimeError(f"Could not construct Yahoo ticker for ISIN {self._isin}")
        self._init_eur_scaling()

    def _read_listing_currency(self) -> str | None:
            fast = getattr(self._ticker, "fast_info", None)
            if fast is not None:
                for key in ("currency", "currencyCode"):
                    try:
                        c = fast.get(key) if hasattr(fast, "get") else fast[key]
                    except (KeyError, TypeError, AttributeError):
                        c = None
                    if c:
                        return str(c).upper()
            info = self._ticker.info
            if isinstance(info, dict) and info.get("currency"):
                return str(info["currency"]).upper()
            return None

    def _fetch_spot_eur_usd(self) -> float | None:
        """USD per 1 EUR (Yahoo convention for EURUSD=X)."""

        for attempt in range(self._retries):
            try:
                fx = yf.Ticker(self._EURUSD_SYMBOL)
                fast = getattr(fx, "fast_info", None)
                if fast is not None:
                    for key in ("last_price", "lastPrice"):
                        try:
                            lp = fast.get(key) if hasattr(fast, "get") else fast[key]
                        except (KeyError, TypeError, AttributeError):
                            lp = None
                        if lp is not None:
                            return float(lp)
                for period in ("1d", "5d"):
                    hist = fx.history(period=period, interval="1d")
                    if not hist.empty:
                        return float(hist["Close"].iloc[-1])
                return None
            except YFRateLimitError as e:
                if attempt + 1 < self._retries:
                    time.sleep(self._delay_s)
                    continue
                raise RuntimeError(
                    f"Yahoo Finance rate limit after {self._retries} attempts "
                    f"for ISIN {self._isin}"
                ) from e

    def _init_eur_scaling(self) -> None:
        self._listing_currency = self._read_listing_currency()
        if self._listing_currency == "EUR" or self._listing_currency is None:
            self._eur_usd_rate = None
            return
        if self._listing_currency != "USD":
            self._eur_usd_rate = None
            return
        rate = self._fetch_spot_eur_usd()
        if rate is None or rate <= 0:
            raise RuntimeError(
                f"USD-listed ISIN {self._isin} but could not load {self._EURUSD_SYMBOL} for EUR conversion"
            )
        self._eur_usd_rate = rate

    def _quote_to_eur(self, raw: float) -> float:
        """USD listings: ``_eur_usd_rate`` is USD per 1 EUR → EUR = USD / rate."""
        if (
            self._listing_currency == "USD"
            and self._eur_usd_rate is not None
            and self._eur_usd_rate > 0
        ):
            return float(raw) / self._eur_usd_rate
        return float(raw)

    def _fast_info_price(self) -> float | None:
        fast = getattr(self._ticker, "fast_info", None)
        if fast is None:
            return None
        for key in ("last_price", "lastPrice"):
            try:
                lp = fast.get(key) if hasattr(fast, "get") else fast[key]
            except (KeyError, TypeError, AttributeError):
                lp = None
            if lp is not None:
                return self._quote_to_eur(float(lp))
        return None

    def _history_last_close(self) -> float | None:
        for period in ("1d", "5d"):
            hist = self._ticker.history(period=period, interval="1d")
            if not hist.empty:
                return self._quote_to_eur(float(hist["Close"].iloc[-1]))
        return None


class JustETFPosition(Position):
    """
    JustETF performance chart API (same endpoint as the site charts).
    Caches chart JSON for the lifetime of the instance.
    """

    _CHART_URL = "https://www.justetf.com/api/etfs/{isin}/performance-chart"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    _CHART_PARAMS = {
        "locale": "en",
        "valuesType": "MARKET_VALUE",
        "reduceData": "true",
        "includeDividends": "false",
        "features": "DIVIDENDS",
    }
    _RETRIES = 10
    _DELAY_S = 0.1

    def __init__(self, isin: str) -> None:
        super().__init__(isin)
        self._chart: dict | None = None

    def _http_chart_json(self, *, currency: str) -> dict:
        params = dict(self._CHART_PARAMS, currency=currency)
        query = urllib.parse.urlencode(params)
        url = f"{self._CHART_URL.format(isin=self._isin)}?{query}"
        req = urllib.request.Request(url, headers=self._HEADERS, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def _fetch_chart_with_retries(self) -> dict:
        for attempt in range(self._RETRIES):
            try:
                for currency in ("EUR", "USD"):
                    try:
                        return self._http_chart_json(currency=currency)
                    except urllib.error.HTTPError as e:
                        if e.code == 404:
                            continue
                        raise
                raise RuntimeError(
                    f"No JustETF performance data for ISIN {self._isin} "
                    "(tried EUR and USD)"
                )
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                raise RuntimeError(
                    f"JustETF HTTP {e.code} while fetching chart for {self._isin}"
                ) from e
            except urllib.error.URLError:
                if attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                raise
        raise RuntimeError(
            f"JustETF chart fetch failed for {self._isin} after {self._RETRIES} attempts"
        )

    def _chart_data(self) -> dict:
        if self._chart is None:
            self._chart = self._fetch_chart_with_retries()
        return self._chart

    def _fast_info_price(self) -> float | None:
        data = self._chart_data()
        latest = data.get("latestQuote")
        if isinstance(latest, dict) and latest.get("raw") is not None:
            return float(latest["raw"])
        return None

    def _history_last_close(self) -> float | None:
        data = self._chart_data()
        series = data.get("series") or []
        if not series:
            return None
        last = series[-1].get("value") or {}
        if last.get("raw") is None:
            return None
        return float(last["raw"])


if __name__ == "__main__":
    _sample = "IE000BI8OT95"
    _j = JustETFPosition(_sample)
    print(f"JustETF {_sample} last={_j.last_price():.4f} hist={_j.price_history():.4f}")
    _y = YFinancePosition(_sample)
    try:
        print(
            f"YFinance {_sample} last={_y.last_price():.4f} "
            f"hist={_y.price_history():.4f}"
        )
    except Exception as ex:
        print(f"YFinance {_sample} (optional): {ex!r}")
