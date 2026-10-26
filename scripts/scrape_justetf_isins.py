#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scrape every JustETF profile ISIN from the public sitemaps.

Usage::

    python scripts/scrape_justetf_isins.py

The result is printed as one sorted, comma-separated line, ready to pass
to ``scripts/rescan_isin_issuers.py``. Progress and the final count go
to stderr. No database is touched.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from collections.abc import Callable
from typing import Any

SITEMAP_INDEX_URL = "https://www.justetf.com/sitemap_index.xml"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_LOC_RE = re.compile(rb"<loc>\s*(.*?)\s*</loc>", re.DOTALL)
_PROFILE_ISIN_RE = re.compile(rb"/etf-profile\.html\?isin=([A-Z0-9]{12})")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT}, method="GET"
    )


def _sitemap_locations(index_xml: bytes) -> list[str]:
    """The sitemap URLs listed by a sitemap index document."""
    return [
        match.group(1).decode("utf-8", errors="replace").strip()
        for match in _LOC_RE.finditer(index_xml)
    ]


def scrape_justetf_isins(
    *,
    index_url: str = SITEMAP_INDEX_URL,
    timeout: float = 120.0,
    open_url: Callable[..., Any] = urllib.request.urlopen,
    emit: Callable[[str], None] = lambda _message: None,
) -> list[str]:
    """Return every unique JustETF profile ISIN, sorted."""
    with open_url(_request(index_url), timeout=timeout) as response:
        locations = _sitemap_locations(response.read())

    isins: set[str] = set()
    for sitemap_url in locations:
        emit(f"scanning {sitemap_url}")
        with open_url(_request(sitemap_url), timeout=timeout) as response:
            for line in response:
                for match in _PROFILE_ISIN_RE.finditer(line):
                    isins.add(match.group(1).decode("ascii"))
    return sorted(isins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape all unique JustETF profile ISINs and print them "
            "comma-separated."
        )
    )
    parser.add_argument(
        "--sitemap-index-url",
        default=SITEMAP_INDEX_URL,
        help="JustETF sitemap index URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Network timeout in seconds (default: 120).",
    )
    args = parser.parse_args(argv)

    try:
        isins = scrape_justetf_isins(
            index_url=args.sitemap_index_url,
            timeout=args.timeout,
            emit=lambda message: print(message, file=sys.stderr),
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not isins:
        print("error: no JustETF ISINs found", file=sys.stderr)
        return 1
    print(",".join(isins))
    print(f"scraped {len(isins)} unique ISINs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
