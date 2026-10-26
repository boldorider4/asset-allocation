# SPDX-License-Identifier: AGPL-3.0-or-later
"""The JustETF sitemap ISIN scraper."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scrape_justetf_isins import (  # noqa: E402
    _sitemap_locations,
    main as scraper_main,
    scrape_justetf_isins,
)


class _Response:
    def __init__(self, data: bytes | list[bytes]) -> None:
        self._lines = data if isinstance(data, list) else [data]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b"".join(self._lines)

    def __iter__(self):
        return iter(self._lines)


def _opener(documents: dict[str, bytes | list[bytes]]):
    calls: list[tuple[str, float]] = []

    def open_url(request, timeout: float = 0):
        calls.append((request.full_url, timeout))
        return _Response(documents[request.full_url])

    return open_url, calls


class TestSitemapLocations(unittest.TestCase):
    def test_reads_loc_elements(self) -> None:
        index = (
            b"<sitemapindex>"
            b"<sitemap><loc>https://example.test/a.xml</loc></sitemap>"
            b"<sitemap><loc> https://example.test/b.xml </loc></sitemap>"
            b"</sitemapindex>"
        )
        self.assertEqual(
            _sitemap_locations(index),
            ["https://example.test/a.xml", "https://example.test/b.xml"],
        )


class TestScrapeJustetfIsins(unittest.TestCase):
    def test_collects_unique_profile_isins_from_all_sitemaps(self) -> None:
        index_url = "https://example.test/sitemap_index.xml"
        one = "https://example.test/sitemap1.xml"
        two = "https://example.test/sitemap2.xml"
        index = (
            f"<sitemapindex><sitemap><loc>{one}</loc></sitemap>"
            f"<sitemap><loc>{two}</loc></sitemap></sitemapindex>"
        ).encode()
        documents = {
            index_url: index,
            one: [
                b'<loc>https://www.justetf.com/en/etf-profile.html?isin=IE00BKM4GZ66</loc>\n',
                b'<loc>https://www.justetf.com/de/etf-profile.html?isin=LU0000000001</loc>\n',
                b'<loc>https://www.justetf.com/en/other.html?isin=IE00IGNORED1</loc>\n',
                b'<loc>https://www.justetf.com/en/etf-profile.html?isin=IE00BAD</loc>\n',
            ],
            two: [
                b'<loc>https://www.justetf.com/en/etf-profile.html?isin=IE00BKM4GZ66</loc>\n',
                b'<loc>https://www.justetf.com/fr/etf-profile.html?isin=IE00NEW00001</loc>\n',
            ],
        }
        open_url, calls = _opener(documents)
        messages: list[str] = []
        isins = scrape_justetf_isins(
            index_url=index_url,
            timeout=7.5,
            open_url=open_url,
            emit=messages.append,
        )
        self.assertEqual(
            isins, ["IE00BKM4GZ66", "IE00NEW00001", "LU0000000001"]
        )
        self.assertEqual(
            [url for url, _timeout in calls], [index_url, one, two]
        )
        self.assertTrue(all(timeout == 7.5 for _url, timeout in calls))
        self.assertEqual(len(messages), 2)
        self.assertIn(one, messages[0])

    def test_network_error_propagates(self) -> None:
        def open_url(_request, timeout: float = 0):
            raise OSError("offline")

        with self.assertRaisesRegex(OSError, "offline"):
            scrape_justetf_isins(open_url=open_url)

    def test_main_prints_one_comma_separated_line(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch(
                "scripts.scrape_justetf_isins.scrape_justetf_isins",
                return_value=["IE00A", "IE00B"],
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = scraper_main([])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "IE00A,IE00B\n")
        self.assertIn("scraped 2 unique ISINs", stderr.getvalue())

    def test_main_fails_when_nothing_is_found(self) -> None:
        stderr = io.StringIO()
        with (
            patch(
                "scripts.scrape_justetf_isins.scrape_justetf_isins",
                return_value=[],
            ),
            redirect_stderr(stderr),
        ):
            code = scraper_main([])
        self.assertEqual(code, 1)
        self.assertIn("no JustETF ISINs found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
