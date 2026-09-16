from __future__ import annotations

import http.cookiejar
import logging
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from position.position import Position, fold_unknown_sector_label
from logger import attach_color_stderr_handler_for_module

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

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
    # Holdings > Sectors: same profile page; the full table behind "Show more"
    # is served by the Wicket ``loadMoreSectors`` AJAX URL (the seed HTML only
    # carries the first rows). Markers/testids mirror the countries block.
    _SECTOR_PROFILE_FRAGMENT = "holdingsSection-sectors-loadMoreSectors"
    _SECTOR_SECTION_MARKERS = (
        "holdingsSection-sectors",
        "etf-holdings_sectors_table",
    )
    _SECTOR_DIST_WICKET = "0-1.0-holdingsSection-sectors-loadMoreSectors"
    _SECTOR_DIST_PARAMS = {"_wicket": "1"}
    _SECTOR_ROW_RE = re.compile(
        r'data-testid="tl_etf-holdings_sectors_value_name"\s*>([^<]+)</td>'
        r'.*?data-testid="tl_etf-holdings_sectors_value_percentage"\s*>'
        r'([\d.,]+)\s*%</span>',
        re.DOTALL,
    )
    # Raw JustETF sector labels -> canonical staple names (see
    # ``position.position._LIST_OF_STAPLE_SECTORS``). Unlisted labels pass
    # through unchanged so new variants surface via the base-class warning.
    _SECTOR_CANONICAL_NAMES = {
        "Financials": "Finance",
        "Communication Services": "Telecommunication",
        "Consumer Non-Cyclicals": "Consumer",
        "Consumer Staples": "Consumer",
        "Consumer Cyclicals": "Consumer",
        "Consumer Discretionary": "Consumer",
        "Consumer Services": "Consumer",
    }
    _RETRIES = 10
    _DELAY_S = 0.1

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
        cached_countries: dict[str, float] | None = None,
        cached_sectors: dict[str, float] | None = None,
        value_scale: float = 1.0,
        price: float | None = None,
        prefer_scrape_value: bool = False,
    ) -> None:
        self._chart: dict | None = None
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
            cached_countries=cached_countries,
            cached_sectors=cached_sectors,
            value_scale=value_scale,
            price=price,
            prefer_scrape_value=prefer_scrape_value,
        )

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

    def _holdings_seed_page(
        self, *, profile_fragment: str
    ) -> tuple[str, str, urllib.request.OpenerDirector]:
        """
        GET the profile page (seeds the session cookies).

        Any ``#fragment`` is never sent on the wire; it only matches the
        site's in-page anchor. Returns ``(seed_html, seed_base, opener)``.
        """
        seed_query = urllib.parse.urlencode({"isin": self._isin})
        seed_base = f"{self._COUNTRY_PAGE_URL}?{seed_query}"
        seed_url = f"{seed_base}#{profile_fragment}"

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        seed_headers = {
            **self._HEADERS,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
        logger.info("JustETF: fetching seed HTML from %s", seed_url)
        req_seed = urllib.request.Request(seed_url, headers=seed_headers, method="GET")
        with opener.open(req_seed, timeout=30) as resp:
            seed_html = resp.read().decode("utf-8", errors="replace")
        return seed_html, seed_base, opener

    def _holdings_wicket_fragment(
        self,
        *,
        opener: urllib.request.OpenerDirector,
        dist_wicket: str,
        dist_params: dict[str, str],
        seed_base: str,
        table_marker: str,
        label: str,
    ) -> str | None:
        """
        Full holdings table from the Wicket ``loadMore`` AJAX payload.

        Despite the request name, the payload is XML with an HTML table in
        CDATA. Returns the table fragment, or ``None`` when the request fails,
        the XML does not parse, or no table is present (the caller then falls
        back to the seed HTML).
        """
        dist_query = urllib.parse.urlencode(dict(dist_params, isin=self._isin))
        dist_url = f"{self._COUNTRY_PAGE_URL}?{dist_wicket}&{dist_query}"
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
        except urllib.error.HTTPError as e:
            logger.warning(
                "JustETF %s Wicket request failed for %s (HTTP %s); using profile HTML",
                label,
                self._isin,
                e.code,
            )
            return None

        logger.info("JustETF: XML text parsed successfully")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning(
                "JustETF %s XML parse failed for %s; using profile HTML",
                label,
                self._isin,
            )
            return None
        for comp in root.findall(".//component"):
            fragment = comp.text or ""
            if table_marker in fragment:
                return fragment
        return None

    def _http_holdings_dist_json(
        self,
        *,
        label: str,
        plural: str,
        section_markers: tuple[str, ...],
        profile_fragment: str,
        dist_wicket: str,
        dist_params: dict[str, str],
        table_marker: str,
        parse_rows: Callable[[str], list[dict[str, float | str]]],
    ) -> list[dict[str, float | str]]:
        """
        Load ``label`` weights from justETF (profile page cookie + Wicket AJAX).

        First GET seeds cookies from the profile page. Holdings are detected
        when the HTML contains legacy Wicket ids or the current
        ``data-testid`` table markup.

        Second GET is the Wicket ``loadMore`` AJAX URL when that path still
        exists (expanded table); many profiles now ship (part of) the table in
        the seed HTML and/or respond with an access redirect to the AJAX URL.

        If the profile has no such holdings block (e.g. some bond or commodity
        products), returns an empty list and does not call the Wicket URL.
        """
        seed_html, seed_base, opener = self._holdings_seed_page(
            profile_fragment=profile_fragment
        )

        if not any(m in seed_html for m in section_markers):
            logger.warning("JustETF: no %s section markers found in seed HTML", label)
            return []

        fragment = self._holdings_wicket_fragment(
            opener=opener,
            dist_wicket=dist_wicket,
            dist_params=dist_params,
            seed_base=seed_base,
            table_marker=table_marker,
            label=label,
        )
        if fragment is None:
            logger.warning("JustETF: using profile HTML to parse %s", plural)
            return parse_rows(seed_html)
        logger.info("JustETF: found %s in fragment", plural)
        parsed = parse_rows(fragment)
        if parsed:
            return parsed
        logger.info("JustETF: no %s found in fragment, using profile HTML", plural)
        return parse_rows(seed_html)

    def _fetch_holdings_with_retries(
        self,
        fetch_fn: Callable[[], list[dict[str, float | str]]],
        *,
        label: str,
        plural: str,
    ) -> list[dict[str, float | str]]:
        """Retry ``fetch_fn`` on 429/5xx/URLError; wrap terminal failures."""
        logger.info("JustETF: fetching %s with retries %d", plural, self._RETRIES)
        for attempt in range(self._RETRIES):
            try:
                logger.info("JustETF: attempt %d", attempt)
                return fetch_fn()
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                logger.error("JustETF: HTTP error %d", e.code)
                raise RuntimeError(
                    f"JustETF HTTP {e.code} while fetching {label} dist for {self._isin}"
                ) from e
            except urllib.error.URLError:
                if attempt + 1 < self._RETRIES:
                    time.sleep(self._DELAY_S)
                    continue
                logger.error("JustETF: URL error")
                raise
        logger.error("JustETF: %s fetch failed after %d attempts", label, self._RETRIES)
        raise RuntimeError(
            f"JustETF {label} fetch failed for {self._isin} after {self._RETRIES} attempts"
        )

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        """
        Load country weights from justETF (profile page cookie + Wicket AJAX).

        Country holdings are detected when the HTML contains legacy Wicket ids
        (``holdingsSection-countries``) or the current
        ``etf-holdings_countries_table`` markup; see
        :meth:`_http_holdings_dist_json` for the shared flow.
        """
        return self._http_holdings_dist_json(
            label="country",
            plural="countries",
            section_markers=self._COUNTRY_SECTION_MARKERS,
            profile_fragment=self._COUNTRY_PROFILE_FRAGMENT,
            dist_wicket=self._COUNTRY_DIST_WICKET,
            dist_params=self._COUNTRY_DIST_PARAMS,
            table_marker="etf-holdings_countries_table",
            parse_rows=self._countries_from_html_table,
        )

    def _fetch_countries_with_retries(self) -> list[dict[str, float | str]]:
        # Bound method keeps dispatching to subclass overrides (e.g. iShares CSV).
        return self._fetch_holdings_with_retries(
            self._http_country_dist_json, label="country", plural="countries"
        )

    def countries(self) -> list[dict[str, float | str]]:
        """Country allocation (name + weight_pct) from the Holdings section."""
        if self._countries is None and self._isin is not None:
            try:
                self._countries = self._fetch_countries_with_retries()
            except (RuntimeError, urllib.error.URLError) as e:
                self._countries = []
                logger.warning(
                    "JustETF: country fetch failed for %s (%s); using empty country list",
                    self._isin,
                    e,
                )
        return self._countries

    @classmethod
    def _canonical_sector_name(cls, raw: str) -> str:
        """Aggregate a raw JustETF sector label to its canonical staple name.

        Labels outside the staple taxonomy fold into "Other" so downstream
        layers only ever see definitive sector names.
        """
        stripped = raw.strip()
        return fold_unknown_sector_label(
            cls._SECTOR_CANONICAL_NAMES.get(stripped, stripped)
        )

    def _sectors_from_html_table(self, html: str) -> list[dict[str, float | str]]:
        weights: dict[str, float] = {}
        for name, pct_s in self._SECTOR_ROW_RE.findall(html):
            canonical = self._canonical_sector_name(name)
            weights[canonical] = weights.get(canonical, 0.0) + float(
                pct_s.replace(",", "")
            )
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        """
        Load sector weights from justETF (profile page cookie + Wicket AJAX).

        The seed HTML only carries the first sector rows, while the Wicket
        ``loadMoreSectors`` AJAX payload holds the full table; see
        :meth:`_http_holdings_dist_json` for the shared flow.
        """
        return self._http_holdings_dist_json(
            label="sector",
            plural="sectors",
            section_markers=self._SECTOR_SECTION_MARKERS,
            profile_fragment=self._SECTOR_PROFILE_FRAGMENT,
            dist_wicket=self._SECTOR_DIST_WICKET,
            dist_params=self._SECTOR_DIST_PARAMS,
            table_marker="etf-holdings_sectors_table",
            parse_rows=self._sectors_from_html_table,
        )

    def _fetch_sectors_with_retries(self) -> list[dict[str, float | str]]:
        return self._fetch_holdings_with_retries(
            self._http_sector_dist_json, label="sector", plural="sectors"
        )

    def sectors(self) -> list[dict[str, float | str]] | None:
        """Sector allocation (name + weight_pct) from the Holdings section."""
        if self._sectors is None and self._isin is not None:
            try:
                self._sectors = self._fetch_sectors_with_retries()
            except (RuntimeError, urllib.error.URLError) as e:
                self._sectors = []
                logger.warning(
                    "JustETF: sector fetch failed for %s (%s); using empty sector list",
                    self._isin,
                    e,
                )
        return self._sectors

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
            logger.info("JustETF: latest quote found")
            return float(latest["raw"])
        logger.warning("JustETF chart has no latestQuote for ISIN %s", self._isin)
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
    # print(f"JustETF {_sample} last={_j.price:.4f}")

    # countries = _j.countries()
    # for _row in countries:
    #     print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    # print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    # print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # # Scalable AC World Xtrackers UCITS ETF (Acc)
    # print("*************** Scalable AC World Xtrackers UCITS ETF (Acc) ***************")
    # _sample = "LU2903252349"
    # _j = JustETFPosition(_sample, dmem_other=.5)
    # print(f"JustETF {_sample} last={_j.price:.4f}")

    # countries = _j.countries()
    # for _row in countries:
    #     print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    # print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    # print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # iShares MSCI EM CTB Enhanced ESG UCITS ETF
    print("*************** iShares MSCI EM CTB Enhanced ESG UCITS ETF ***************")
    _sample = "IE00BHZPJ239"
    _j = JustETFPosition(_sample, dmem_other=0)
    print(f"JustETF {_sample} last={_j.price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")
    sectors = _j.sectors()
    for _row in sectors or []:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")

    # Xtrackers MSCI World ex-USA UCITS ETF
    print("*************** Xtrackers MSCI World ex-USA UCITS ETF ***************")
    _sample = "IE0006WW1TQ4"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # EUWAX Gold II
    print("*************** EUWAX Gold II ***************")
    _sample = "DE000EWG2LD7"
    _j = JustETFPosition(_sample, dmem_other=0)
    print(f"JustETF {_sample} last={_j.price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF
    print("*************** State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF ***************")
    _sample = "IE00B4YBJ215"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")

    # Xtrackers II EUR Overnight Rate Swap UCITS ETF (Acc)
    print("*************** Xtrackers II EUR Overnight Rate Swap UCITS ETF (Acc) ***************")
    _sample = "LU0290358497"
    _j = JustETFPosition(_sample, dmem_other=1)
    print(f"JustETF {_sample} last={_j.price:.4f}")

    countries = _j.countries()
    for _row in countries:
        print(f"  {_row['name']}: {_row['weight_pct']:.2f}%")
    print(f"Developed markets vs. emerging markets allocation: {_j._compute_dev_vs_em_market()*100:.2f}%")
    print(f"US vs. non-US allocation within developed markets: {_j._compute_us_vs_exus_market()*100:.2f}%")