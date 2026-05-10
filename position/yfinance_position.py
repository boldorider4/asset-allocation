from __future__ import annotations

import time
import yfinance as yf

from position.position import Position


class YFinancePosition(Position):
    """
    Yahoo Finance via yfinance (ISIN as ticker symbol where supported).
    Quotes are returned in EUR: for USD-listed instruments, Yahoo's
    ``EURUSD=X`` spot (USD per 1 EUR) is stored in ``_eur_usd_rate`` and
    ``_fast_info_price`` / ``_history_last_close`` divide raw USD by that rate.
    """

    _EURUSD_SYMBOL = "EURUSD=X"
    _RETRIES = 1
    _DELAY_S = 1

    def __init__(
        self, isin: str,
        name: str | None = None,
        short_name: str | None = None,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        dmem_other: float | None = None,
        last_price: float | None = None,
        cached_countries: dict[str, float] | None = None,
        value_scale: float = 1.0,
    ) -> None:
        #print(f"YFinancePosition __init__: isin={isin}, last_price={last_price}")
        self._ticker: yf.Ticker | None = None
        self._listing_currency: str | None = None
        self._eur_usd_rate: float | None = None
        super().__init__(
            isin,
            name=name,
            short_name=short_name,
            shares=shares,
            value=value,
            broker=broker,
            dmem=dmem,
            usavn=usavn,
            dmem_other=dmem_other,
            last_price=last_price,
            cached_countries=cached_countries,
            value_scale=value_scale,
        )

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
        from position.factory import factory as _factory

        fx = _factory(self._EURUSD_SYMBOL, value_scale=1.0)
        return fx.last_price

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

    def _compute_dev_vs_em_market(self) -> float:
        """Compute developed markets vs. emerging markets allocation."""
        # TODO: implement this
        return self._dmem

    def _compute_us_vs_exus_market(self) -> float:
        """Compute us vs. non-us allocation within developed markets."""
        # TODO: implement this
        return self._usavn

    def _fast_info_price(self) -> float | None:
        for attempt in range(self._RETRIES):
            try:
                self._ticker = yf.Ticker(self._isin)
                break
            except YFRateLimitError as e:
                if attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                raise RuntimeError(
                    f"Yahoo Finance rate limit after {self._RETRIES} attempts "
                    f"for ISIN {self._isin}"
                ) from e

        if self._ticker is None:
            raise RuntimeError(f"Could not construct Yahoo ticker for ISIN {self._isin}")
        if self._isin != "EURUSD=X":
            self._init_eur_scaling()
        else:
            self._eur_usd_rate = 1.0

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


if __name__ == "__main__":
    _sample = "IE000BI8OT95"
    _y = YFinancePosition(_sample)
    try:
        print(
            f"YFinance {_sample} last={_y.last_price():.4f} "
        )
    except Exception as ex:
        print(f"YFinance {_sample} (optional): {ex!r}")