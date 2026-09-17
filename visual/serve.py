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
import json
import logging
from urllib.parse import parse_qs, urlsplit

from visual.constituents import load_constituents, render_constituents_page

logger = logging.getLogger(__name__)

DASHBOARD_PATHS = ("/dashboard", "/dashboard/")
CONSTITUENTS_PATHS = ("/constituents", "/constituents/")
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

    def _redirect_root(self) -> bool:
        """302 ``/`` to ``/dashboard``, preserving any query string."""
        if urlsplit(self.path).path != "/":
            return False
        query = urlsplit(self.path).query
        target = "/dashboard" + (f"?{query}" if query else "")
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()
        return True

    def __init__(self, *args, assets_file=None, cache_file=None, **kwargs):
        self._assets_file = assets_file
        self._cache_file = cache_file
        super().__init__(*args, **kwargs)

    def _serve_constituents(self) -> bool:
        """Render the constituents page; 502 with a plain reason on failure."""
        if urlsplit(self.path).path not in CONSTITUENTS_PATHS:
            return False
        try:
            if not self._assets_file or not self._cache_file:
                raise RuntimeError("assets file not configured")
            body = render_constituents_page(
                load_constituents(self._assets_file, self._cache_file)
            ).encode("utf-8")
            status, content_type = 200, "text/html; charset=utf-8"
        except Exception as exc:
            logger.warning("constituents unavailable: %s", exc)
            body = f"constituents unavailable: {exc}".encode("utf-8")
            status, content_type = 502, "text/plain; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
        return True

    def _store_constituent(self) -> bool:
        """Handle ``POST /api/constituents``; plain-text statuses on failure."""
        from visual.constituents import store_constituent_value

        if urlsplit(self.path).path != "/api/constituents":
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 65536:
            self._plain_status(400, "empty or oversized request body")
            return True
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            self._plain_status(400, f"invalid JSON: {exc}")
            return True
        if not isinstance(payload, dict):
            self._plain_status(400, "body must be a JSON object")
            return True
        try:
            if not self._assets_file:
                raise RuntimeError("assets file not configured")
            value = store_constituent_value(
                self._assets_file,
                payload.get("bucket"),
                payload.get("index"),
                payload.get("field"),
                payload.get("value"),
            )
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("constituents store failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps({"value": value}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _plain_status(self, status: int, reason: str) -> None:
        body = reason.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._redirect_root() and not self._serve_constituents():
            super().do_GET()

    def do_HEAD(self) -> None:
        if not self._redirect_root() and not self._serve_constituents():
            super().do_HEAD()

    def do_POST(self) -> None:
        if not self._store_constituent():
            self._plain_status(404, "unknown endpoint")

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


def serve_forever(
    *,
    address: str,
    port: int,
    directory: str,
    assets_file: str | None = None,
    cache_file: str | None = None,
) -> None:
    """Serve *directory* until interrupted (runs in the foreground)."""
    handler = functools.partial(
        DashboardHandler,
        directory=directory,
        assets_file=assets_file,
        cache_file=cache_file,
    )
    with http.server.ThreadingHTTPServer((address, port), handler) as httpd:
        logger.info("Serving %s on http://%s:%s/dashboard", directory, address, port)
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="asalloc visualizer dashboard server.")
    parser.add_argument("--bind", default="localhost", help="Address to bind (default: localhost).")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on.")
    parser.add_argument("--directory", required=True, help="Visualizer directory to serve.")
    parser.add_argument("--assets-file", default=None, help="Assets JSON file backing /constituents.")
    parser.add_argument("--cache-file", default=None, help="Cache JSON file backing /constituents prices.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    serve_forever(
        address=args.bind,
        port=args.port,
        directory=args.directory,
        assets_file=args.assets_file,
        cache_file=args.cache_file,
    )


if __name__ == "__main__":
    main()
