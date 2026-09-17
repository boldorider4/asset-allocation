"""Dashboard endpoint for the web visualizer.

Serves the visualizer directory like ``python -m http.server``, plus a
``/dashboard`` route with two data roots:

* ``GET /dashboard`` (or ``?incognito=false``) → ``index.html`` with ``data/*``
  resolved from ``data/clear/``.
* ``GET /dashboard?incognito=true`` → same ``index.html``, ``data/*`` resolved
  from ``data/incognito/``.

The gallery frontend is untouched: its ``data/`` fetches carry no query
string, so the mode is taken from the ``Referer`` header (same-origin
localhost dashboard loads always send it). Requests without a dashboard
Referer are served literally, and unknown ``incognito`` values fall back to
``clear``. Remapped paths stay jailed inside the served directory via the
standard library translation.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import logging
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

DASHBOARD_PATHS = ("/dashboard", "/dashboard/")
GALLERY_PATHS = ("/", "/index.html", "/index.htm", "/dashboard", "/dashboard/")
DATA_PREFIX = "/data"
CLEAR_DIR = "clear"
INCOGNITO_DIR = "incognito"


def incognito_flag(query_string: str) -> bool:
    """True only for an explicit ``?incognito=true`` (case-insensitive)."""
    values = parse_qs(query_string).get("incognito", [])
    return bool(values) and values[0].strip().lower() == "true"


def referer_incognito(headers) -> bool | None:
    """
    Mode for a ``data/*`` request from its ``Referer`` header: True for an
    explicit ``?incognito=true`` gallery load, False for any other gallery
    load (unknown values fall back to ``clear``), None when there is no
    usable Referer (serve the path literally).
    """
    referer = headers.get("Referer") or ""
    if not referer:
        return None
    try:
        parts = urlsplit(referer)
    except Exception:
        return None
    if (parts.path.rstrip("/") or "/") not in GALLERY_PATHS:
        return None
    return incognito_flag(parts.query)


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Static visualizer server with ``/dashboard`` + clear/incognito roots."""

    server_version = "asalloc-dashboard/1.0"

    def translate_path(self, path: str) -> str:
        url_path = urlsplit(path).path
        if url_path in DASHBOARD_PATHS:
            return super().translate_path("/index.html")
        if url_path == DATA_PREFIX or url_path.startswith(DATA_PREFIX + "/"):
            mode = referer_incognito(self.headers)
            if mode is not None:
                rest = url_path[len(DATA_PREFIX):].lstrip("/")
                subdir = INCOGNITO_DIR if mode else CLEAR_DIR
                url_path = f"{DATA_PREFIX}/{subdir}/{rest}" if rest else f"{DATA_PREFIX}/{subdir}/"
                return super().translate_path(url_path)
        return super().translate_path(path)


def serve_forever(*, address: str, port: int, directory: str) -> None:
    """Serve *directory* until interrupted (runs in the foreground)."""
    handler = functools.partial(DashboardHandler, directory=directory)
    with http.server.ThreadingHTTPServer((address, port), handler) as httpd:
        logger.info("Serving %s on http://%s:%s/dashboard", directory, address, port)
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="asalloc visualizer dashboard server.")
    parser.add_argument("--bind", default="localhost", help="Address to bind (default: localhost).")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on.")
    parser.add_argument("--directory", required=True, help="Visualizer directory to serve.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    serve_forever(address=args.bind, port=args.port, directory=args.directory)


if __name__ == "__main__":
    main()
