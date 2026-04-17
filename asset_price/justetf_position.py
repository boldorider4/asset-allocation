from __future__ import annotations
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
    # Holdings > Countries: seed the session from the profile URL users open for that block
    # (fragment is not sent on the wire; it only matches the site's in-page anchor).
    _COUNTRY_PAGE_URL = "https://www.justetf.com/en/etf-profile.html"
    _COUNTRY_PROFILE_FRAGMENT = "holdingsSection-countries-loadMoreCountries"
    # justETF used to expose Wicket ids like ``holdingsSection-countries``; newer
    # profiles render the countries table with data-testids only (no load-more
    # anchor). Accept either so we still parse the seed HTML when Wicket AJAX
    # is absent or returns access-denied redirects.
    _COUNTRY_SECTION_MARKERS = (
        "holdingsSection-countries",
        "etf-holdings_countries_table",
    )
    _COUNTRY_DIST_WICKET = "0-1.0-holdingsSection-countries-loadMoreCountries"
    _COUNTRY_DIST_PARAMS = {"_wicket": "1"}
    _COUNTRY_ROW_RE = re.compile(
        r'data-testid="tl_etf-holdings_countries_value_name"\s*>([^<]+)</td>'
        r'.*?data-testid="tl_etf-holdings_countries_value_percentage"\s*>'
        r'([\d.,]+)\s*%</span>',
        re.DOTALL,
    )
    _RETRIES = 10
    _DELAY_S = 0.1

    def __init__(
        self, isin: str,
        name: str | None = None,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        dmem_other: float | None = None,
        last_price: float | None = None,
    ) -> None:
        self._chart: dict | None = None
        super().__init__(isin, name, shares, value, broker, dmem, usavn, dmem_other, last_price)

    def _http_chart_json(self, *, currency: str) -> dict:
        params = dict(self._CHART_PARAMS, currency=currency)
        query = urllib.parse.urlencode(params)
        url = f"{self._CHART_URL.format(isin=self._isin)}?{query}"
        req = urllib.request.Request(url, headers=self._HEADERS, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def _countries_from_html_table(self, html: str) -> list[dict[str, float | str]]:
        rows: list[dict[str, float | str]] = []
        for name, pct_s in self._COUNTRY_ROW_RE.findall(html):
            rows.append(
                {
                    "name": name.strip(),
                    "weight_pct": float(pct_s.replace(",", "")),
                }
            )
        return rows

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        """
        Load country weights from justETF (profile page cookie + Wicket AJAX).

        First GET seeds cookies from the profile page (any ``#fragment`` in the
        browser URL is not sent on the wire). Country holdings are detected when
        the HTML contains legacy Wicket ids (``holdingsSection-countries``) or the
        current ``etf-holdings_countries_table`` markup.

        Second GET is the Wicket ``loadMoreCountries`` AJAX URL when that path
        still exists (expanded table); many profiles now ship the full table in
        the seed HTML and/or respond with an access redirect to the AJAX URL.

        Despite the name, the AJAX payload is XML with an HTML table in CDATA; we
        parse that into the same shape you would get from a JSON allocation API.

        If the profile has no country-holdings block (e.g. some bond or commodity
        products), returns an empty list and does not call the Wicket URL.
        """
        params = dict(self._COUNTRY_DIST_PARAMS, isin=self._isin)
        dist_query = urllib.parse.urlencode(params)
        dist_url = f"{self._COUNTRY_PAGE_URL}?{self._COUNTRY_DIST_WICKET}&{dist_query}"
        seed_query = urllib.parse.urlencode({"isin": self._isin})
        seed_base = f"{self._COUNTRY_PAGE_URL}?{seed_query}"
        seed_url = f"{seed_base}#{self._COUNTRY_PROFILE_FRAGMENT}"

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        seed_headers = {
            **self._HEADERS,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
        req_seed = urllib.request.Request(seed_url, headers=seed_headers, method="GET")
        with opener.open(req_seed, timeout=30) as resp:
            seed_html = resp.read().decode("utf-8", errors="replace")

        if not any(m in seed_html for m in self._COUNTRY_SECTION_MARKERS):
            return []

        wicket_headers = {
            **self._HEADERS,
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Wicket-Ajax": "true",
            "Wicket-Ajax-BaseURL": f"en/etf-profile.html?isin={self._isin}",
            # Referer omits the fragment (typical for browsers; RFC 7231).
            "Referer": seed_base,
        }
        req_dist = urllib.request.Request(dist_url, headers=wicket_headers, method="GET")
        try:
            with opener.open(req_dist, timeout=30) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            return self._countries_from_html_table(seed_html)

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return self._countries_from_html_table(seed_html)
        for comp in root.findall(".//component"):
            fragment = comp.text or ""
            if "etf-holdings_countries_table" in fragment:
                parsed = self._countries_from_html_table(fragment)
                if parsed:
                    return parsed
        return self._countries_from_html_table(seed_html)

    def _fetch_countries_with_retries(self) -> list[dict[str, float | str]]:
        for attempt in range(self._RETRIES):
            try:
                return self._http_country_dist_json()
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                raise RuntimeError(
                    f"JustETF HTTP {e.code} while fetching country dist for {self._isin}"
                ) from e
            except urllib.error.URLError:
                if attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                raise
        raise RuntimeError(
            f"JustETF country fetch failed for {self._isin} after {self._RETRIES} attempts"
        )

    def countries(self) -> list[dict[str, float | str]]:
        """Country allocation (name + weight_pct) from the Holdings section."""
        if self._countries is None and self._isin is not None:
            self._countries = self._fetch_countries_with_retries()
        return self._countries

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
    # Amundi Equity World UCITS ETF (Acc)
    # print("*************** Amundi Equity World UCITS ETF (Acc) ***************")
    # _sample = "IE000BI8OT95"
    # _j = JustETFPosition(_sample, dmem_other=1)
    # print(f"JustETF {_sample} last={_j.last_price:.4f}")

    # countries = _j.countries()
    # for _row in countries:
    #     print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    # print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    # print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # # Scalable AC World Xtrackers UCITS ETF (Acc)
    # print("*************** Scalable AC World Xtrackers UCITS ETF (Acc) ***************")
    # _sample = "LU2903252349"
    # _j = JustETFPosition(_sample, dmem_other=.5)
    # print(f"JustETF {_sample} last={_j.last_price:.4f}")

    # countries = _j.countries()
    # for _row in countries:
    #     print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    # print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    # print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # iShares MSCI EM CTB Enhanced ESG UCITS ETF
    print("*************** iShares MSCI EM CTB Enhanced ESG UCITS ETF ***************")
    _sample = "IE00BHZPJ239"
    _j = JustETFPosition(_sample, dmem_other=0)
    print(f"JustETF {_sample} last={_j.last_price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # Xtrackers MSCI World ex-USA UCITS ETF
    print("*************** Xtrackers MSCI World ex-USA UCITS ETF ***************")
    _sample = "IE0006WW1TQ4"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.last_price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # EUWAX Gold II
    print("*************** EUWAX Gold II ***************")
    _sample = "DE000EWG2LD7"
    _j = JustETFPosition(_sample, dmem_other=0)
    print(f"JustETF {_sample} last={_j.last_price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF
    print("*************** State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF ***************")
    _sample = "IE00B4YBJ215"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.last_price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # Xtrackers II EUR Overnight Rate Swap UCITS ETF (Acc)
    print("*************** Xtrackers II EUR Overnight Rate Swap UCITS ETF (Acc) ***************")
    _sample = "LU0290358497"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.last_price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")