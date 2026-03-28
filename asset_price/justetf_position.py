from __future__ import annotations
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from asset_price.position import Position

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

    def __init__(
        self, isin: str,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        last_price: float | None = None,
    ) -> None:
        self._chart: dict | None = None
        super().__init__(isin, shares, value, broker, dmem, usavn, last_price)

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
    print(f"JustETF {_sample} last={_j.last_price():.4f}")