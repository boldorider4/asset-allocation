# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard endpoint for the web visualizer.

Serves the visualizer directory like ``python -m http.server``, plus a
``/dashboard`` route serving ``index.html``. Chart payloads under ``data/``
are served literally from the single plotter output dir (no per-mode
roots): ``?incognito=true`` only toggles the dashboard button state and
is carried through the constituents round trip, never the chart data.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import itertools
import json
import logging
import multiprocessing
import posixpath
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .constituents import (
    assets_positions_left,
    load_constituents,
    render_constituents_page,
)
from .session import (
    Session,
    clear_cookie_header,
    cookie_header,
    login as start_session,
    logout as end_session,
    new_session_id,
    read_session,
    session_id_from_cookie,
)
from cli.common import DEFAULT_USER_NAME
from storage.repositories import UnknownUser
from storage.wiring import BACKEND_JSON, DEFAULT_POSTGRES_DSN
from utils import clear_chart_files

logger = logging.getLogger(__name__)

# At most one endpoint-triggered update runs per server process. Guarded by
# ``_update_lock``; timer/systemd runs live in other processes and are
# unaffected by cancellation here.
_update_lock = threading.Lock()
_update_job: dict | None = None
_update_ids = itertools.count(1)
# Outcome of the most recent finished endpoint update (``None`` until the
# first run completes): ``{"ok": bool, "error": str}``.
_update_last: dict | None = None

# Systemd units quiesced around every endpoint-triggered update so a timer
# run can never overlap it. Restored afterwards (timer only, by design).
_UPDATE_SERVICE = "asalloc-update.service"
_UPDATE_TIMER = "asalloc-update.timer"

# Child-process machinery: updates run in a spawned worker (fresh
# interpreter, no fork-from-threads hazards) so a cancel can SIGTERM it.
# SIGKILL follows if it outlives the terminate grace period.
_mp_ctx = multiprocessing.get_context("spawn")
TERMINATE_GRACE_S = 5.0
KILL_GRACE_S = 5.0

DASHBOARD_PATHS = ("/dashboard", "/dashboard/")
CONSTITUENTS_PATHS = ("/constituents", "/constituents/")
LOGIN_PATHS = ("/login", "/login/")
DATA_PREFIX = "/data"
# On-disk chart layout under the served directory. The URL prefix stays
# /data (frontend contract); the dir name is overridable per handler
# so instances can serve renamed trees.
DATA_DIRNAME = "data"


def incognito_flag(query_string: str) -> bool:
    """True only for an explicit ``?incognito=true`` (case-insensitive).

    Used solely to keep the dashboard/constituents button state
    consistent across the round trip; chart data ignores it.
    """
    values = parse_qs(query_string).get("incognito", [])
    return bool(values) and values[0].strip().lower() == "true"


def _run_systemctl(*args: str) -> None:
    """Run one ``systemctl --user`` command; raise RuntimeError on failure."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError("systemctl not found; need a systemd Linux host")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"systemctl --user {' '.join(args)} timed out")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"systemctl --user {' '.join(args)} failed: "
            f"{detail or f'exit {proc.returncode}'}"
        )


def _quiesce_update_units() -> None:
    """Stop the update service + timer; both must succeed or nothing runs.

    On a partial failure (service stopped, timer stop failed) the service
    is best-effort restored before raising, so a failed quiesce never
    leaves units stranger than it found them.
    """
    service_stopped = False
    try:
        _run_systemctl("stop", _UPDATE_SERVICE)
        service_stopped = True
        _run_systemctl("stop", _UPDATE_TIMER)
    except RuntimeError:
        if service_stopped:
            try:
                _run_systemctl("start", _UPDATE_SERVICE)
            except RuntimeError as exc:
                logger.warning("update service restore failed: %s", exc)
        raise


def _restore_update_timer() -> None:
    """Restart the update timer after an endpoint-triggered update."""
    _run_systemctl("start", _UPDATE_TIMER)


def _clear_update_job(job_id: int) -> None:
    global _update_job
    with _update_lock:
        if _update_job is not None and _update_job["id"] == job_id:
            _update_job = None


def _record_update_result(ok: bool, error: str) -> None:
    global _update_last
    with _update_lock:
        _update_last = {"ok": ok, "error": error}


def _finish_update_job(job_id: int, ok: bool, error: str) -> None:
    """Record the outcome, restore the timer, then clear the running flag.

    Restoring before clearing keeps the pressed button state covering the
    whole operation. A failed restore is logged but never masks the
    update's own outcome.
    """
    _record_update_result(ok, error)
    try:
        _restore_update_timer()
    except RuntimeError as exc:
        logger.warning("update timer restore failed: %s", exc)
    finally:
        _clear_update_job(job_id)


def _update_status_payload() -> dict:
    with _update_lock:
        updating = _update_job is not None
        last = dict(_update_last) if _update_last is not None else None
    return {
        "updating": updating,
        "last_ok": last["ok"] if last is not None else None,
        "last_error": last["error"] if last is not None else "",
    }


def _update_worker(
    assets_file: str,
    cache_file: str,
    fat: bool,
    directory: str,
    user: str,
    backend: str,
    postgres_dsn: str,
    result_conn,
    cancel_event,
) -> None:
    """Child-process entry point: build a fresh context and run the update.

    Only picklable arguments cross the boundary; the ``RuntimeContext``
    and log suppression are constructed here. Update/plotter defaults
    come from the ini file (``fat`` upgrades them like explicit CLI
    flags); explicit handler arguments always win. Sends ``{"ok", "error"}``
    down the pipe; a killed child sends nothing and the parent reads the
    exit code instead. The send lands in the OS pipe buffer, so it
    survives the child dying with no flush dance.
    """
    from cli.update import main as run_update
    from cli.context import AppConfig, PlotterConfig, RuntimeContext, ServerConfig
    from position.factory import UpdateCancelled

    try:
        ini_cfg = AppConfig.from_ini()
    except Exception as exc:
        logger.warning("update worker: ignoring unreadable config: %s", exc)
        ini_cfg = None
    if ini_cfg is None:
        ini_fetch = (False, False, False)
        ini_plotter = PlotterConfig()
    else:
        ini_fetch = (
            ini_cfg.fetch_prices,
            ini_cfg.fetch_geosplit,
            ini_cfg.fetch_sectorsplit,
        )
        ini_plotter = ini_cfg.plotter_config
    fetch_prices, fetch_geosplit, fetch_sectorsplit = (
        (True, True, True) if fat else ini_fetch
    )
    config = AppConfig(
        assets_file=Path(assets_file),
        cache_file=Path(cache_file),
        fetch_prices=fetch_prices,
        fetch_geosplit=fetch_geosplit,
        fetch_sectorsplit=fetch_sectorsplit,
        user=user,
        server=ServerConfig(
            port=0,
            address="localhost",
            directory=Path(directory),
            backend=backend,
            postgres_dsn=postgres_dsn,
        ),
        plotter_config=ini_plotter,
    )
    ctx = RuntimeContext(config=config)
    ctx.cancel_event = cancel_event
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.ERROR)
    try:
        run_update(ctx)
    except UpdateCancelled as exc:
        result_conn.send({"ok": False, "error": f"update cancelled: {exc}"})
    except Exception as exc:
        result_conn.send({"ok": False, "error": f"update failed: {exc}"})
    else:
        result_conn.send({"ok": True, "error": ""})
    finally:
        root.setLevel(old_level)
        result_conn.close()


def _spawn_update_process(target, args):
    """Start the update worker in a spawned child process (seam for tests)."""
    proc = _mp_ctx.Process(target=target, args=args, daemon=True)
    proc.start()
    return proc


def _process_alive(proc) -> bool:
    try:
        return bool(proc.is_alive())
    except (ValueError, AssertionError, OSError):
        return False


def _terminate_process(proc) -> bool:
    """SIGTERM a runaway update, SIGKILL it past the grace period.

    Returns True when the process is dead on return. Never raises: a
    dead-or-dying process is already what we want.
    """
    for action, grace in (("terminate", TERMINATE_GRACE_S), ("kill", KILL_GRACE_S)):
        try:
            if proc.is_alive():
                getattr(proc, action)()
        except (ProcessLookupError, ValueError, AssertionError, OSError):
            pass
        try:
            proc.join(grace)
        except (ValueError, AssertionError):
            pass
        if not _process_alive(proc):
            return True
    logger.warning("update process survived SIGKILL; leaving it behind")
    return False


def _job_terminated(job_id: int) -> bool:
    with _update_lock:
        job = _update_job
        return job is not None and job["id"] == job_id and job["terminated"]


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Static visualizer server with ``/dashboard`` + a single data root."""

    server_version = "asalloc-dashboard/1.0"

    def end_headers(self) -> None:
        # Revalidate everything, cache nothing blindly: documents,
        # scripts, and chart payloads all change across deploys/updates,
        # and a stale shell (or stale JS) is exactly the "works after
        # clearing the cache" failure class. no-cache (not no-store)
        # keeps back-forward cache eligibility while making staleness
        # impossible: 304 when fresh, full 200 on change.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _redirect_root(self) -> bool:
        """302 ``/`` to the session's landing page, preserving the query."""
        if urlsplit(self.path).path != "/":
            return False
        query = urlsplit(self.path).query
        target = "/dashboard" if self._session() else "/login"
        if query:
            target += f"?{query}"
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()
        return True

    def __init__(
        self,
        *args,
        assets_file=None,
        cache_file=None,
        users_file=None,
        data_dirname=None,
        user=None,
        backend=None,
        postgres_dsn=None,
        **kwargs,
    ):
        self._assets_file = assets_file
        self._cache_file = cache_file
        # The user accounts (the login asks this store, not the assets).
        self._users_file = users_file
        # On-disk chart layout (defaults mirror the served /data tree).
        self._data_dirname = data_dirname or DATA_DIRNAME
        # Fallback user when no session is active (login page default).
        self._default_user = user or DEFAULT_USER_NAME
        # Which backend holds the assets (resolved by the app, not here).
        self._backend = backend or BACKEND_JSON
        self._postgres_dsn = postgres_dsn or DEFAULT_POSTGRES_DSN
        super().__init__(*args, **kwargs)

    def _session(self) -> Session | None:
        """The session of this browser, if it is logged in."""
        return read_session(
            self.directory, self.headers.get("Cookie")
        )

    def _session_user(self) -> str | None:
        """The logged-in user's name, or None."""
        session = self._session()
        return session.user if session else None

    def _require_session(self) -> Session | None:
        """The session, or a 302 to the login page (when unanswered)."""
        session = self._session()
        if session is None:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return None
        return session

    def _serve_login_page(self) -> bool:
        """Redirect a live session away from the login page."""
        if urlsplit(self.path).path not in LOGIN_PATHS:
            return False
        if self._session() is None:
            # No session: the page itself is served as a static file.
            return False
        self.send_response(302)
        self.send_header("Location", "/dashboard")
        self.end_headers()
        return True

    def _clear_empty_user_charts(self, user: str) -> None:
        """A user without positions has no charts: clear their chart dir.

        Self-healing: the charts are derived data, so an emptied
        portfolio must not keep showing the last computed ones. Failures
        are logged, never raised.
        """
        if not self._assets_file:
            return
        try:
            left = assets_positions_left(
                self._assets_file, user, self._backend, self._postgres_dsn
            )
        except Exception as exc:
            logger.warning("could not count positions for %s: %s", user, exc)
            return
        if left > 0:
            return
        cleared = clear_chart_files(
            Path(self.directory) / self._data_dirname / user
        )
        if cleared:
            logger.info(
                "Cleared %d chart file(s) for %s (no positions left)",
                cleared,
                user,
            )

    def _serve_dashboard(self) -> bool:
        """Serve the app shell; without a session it goes to the login page."""
        if urlsplit(self.path).path not in DASHBOARD_PATHS:
            return False
        session = self._session()
        if session is None:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return True
        self._clear_empty_user_charts(session.user)
        return False

    def _serve_session_status(self) -> bool:
        """Serve ``GET /api/session`` with the logged-in user and state."""
        if urlsplit(self.path).path != "/api/session":
            return False
        session = self._session()
        body = json.dumps(
            {
                "user": session.user if session else None,
                "incognito": session.incognito if session else False,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
        return True

    def _login(self) -> bool:
        """Handle ``POST /api/login``: start a session for a known user.

        The user must have an account in the users store (users.json)
        *and* the posted password must match it. A name that only
        exists in the assets store is unknown here and is rejected like
        any other unknown user — and a wrong password is refused the
        same way, so the login cannot be used to probe for user names.

        A known user without a portfolio gets an empty one, so a fresh
        account lands on an empty dashboard.
        """
        if urlsplit(self.path).path != "/api/login":
            return False
        payload = self._read_json_body()
        if payload is None:
            return True
        user = str(payload.get("user") or "").strip()
        if not user:
            self._plain_status(400, "user must not be empty")
            return True
        incognito = bool(payload.get("incognito"))
        if not self._credentials_ok(user, payload.get("password")):
            self._plain_status(404, f"unknown user: {user}")
            return True
        if self._ensure_assets_user(user):
            logger.info("Created an empty portfolio for %r", user)
        session_id = new_session_id()
        start_session(self.directory, session_id, user, incognito=incognito)
        body = json.dumps(
            {
                "ok": True,
                "user": user,
                "incognito": incognito,
                "redirect": "/dashboard?incognito=%s"
                % ("true" if incognito else "false"),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie_header(session_id))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _logout(self) -> bool:
        """Handle ``POST /api/logout``: drop the session."""
        if urlsplit(self.path).path != "/api/logout":
            return False
        session_id = session_id_from_cookie(self.headers.get("Cookie"))
        if session_id:
            end_session(self.directory, session_id)
        body = json.dumps({"ok": True, "redirect": "/login"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", clear_cookie_header())
        self.end_headers()
        self.wfile.write(body)
        return True

    def _credentials_ok(self, user: str, password: Any) -> bool:
        """True when the account exists and the password matches."""
        from .users import users_credentials_ok

        if not self._users_file:
            # No accounts store configured: nobody can log in.
            logger.warning("no users file configured; refusing the login")
            return False
        try:
            return users_credentials_ok(
                self._users_file,
                user,
                "" if password is None else str(password),
                self._backend,
                self._postgres_dsn,
            )
        except Exception as exc:
            # A broken accounts store must not drop the connection: the
            # login is refused and the reason is in the log.
            logger.error("could not read the users store: %s", exc)
            return False

    def _ensure_assets_user(self, user: str) -> bool:
        """Give a known user an (empty) portfolio row; True when created."""
        from .constituents import ensure_assets_user

        if not self._assets_file:
            return False
        try:
            return ensure_assets_user(
                self._assets_file, user, self._backend, self._postgres_dsn
            )
        except Exception as exc:
            # A failed write must not block the login: the dashboard then
            # shows the "no portfolio" state instead of an empty one.
            logger.warning("could not create a portfolio for %r: %s", user, exc)
            return False

    def _serve_update_status(self) -> bool:
        """Serve ``GET /api/update`` with the run state for the sync button."""
        if urlsplit(self.path).path != "/api/update":
            return False
        session = self._require_session()
        if session is None:
            return True
        body = json.dumps(_update_status_payload()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
        return True

    def _serve_constituents(self) -> bool:
        """Render the constituents page; 404 for an unknown user."""
        parts = urlsplit(self.path)
        if parts.path not in CONSTITUENTS_PATHS:
            return False
        session = self._require_session()
        if session is None:
            return True
        # Heal before anything else: a user without positions has no
        # charts, and that must not depend on the cache being set up.
        self._clear_empty_user_charts(session.user)
        try:
            if not self._assets_file or not self._cache_file:
                raise RuntimeError("assets file not configured")
            sections = load_constituents(
                self._assets_file,
                self._cache_file,
                session.user,
                self._backend,
                self._postgres_dsn,
            )
            from .constituents import assets_portfolio_name

            body = render_constituents_page(
                sections,
                incognito=incognito_flag(parts.query),
                portfolio_name=assets_portfolio_name(
                    self._assets_file,
                    session.user,
                    self._backend,
                    self._postgres_dsn,
                ),
            ).encode("utf-8")
            status, content_type = 200, "text/html; charset=utf-8"
        except UnknownUser as exc:
            logger.warning("constituents unavailable: %s", exc)
            body = f"unknown user: {exc.user}".encode("utf-8")
            status, content_type = 404, "text/plain; charset=utf-8"
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
        from .constituents import store_constituent_value

        if urlsplit(self.path).path != "/api/constituents":
            return False
        session = self._require_session()
        if session is None:
            return True
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
            cache: dict[str, Any] = {}
            if self._cache_file:
                try:
                    with open(self._cache_file, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        cache = loaded
                except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                    logger.warning("constituents cache unreadable: %s", exc)
            result = store_constituent_value(
                self._assets_file,
                payload.get("bucket"),
                payload.get("index"),
                payload.get("field"),
                payload.get("value"),
                cache,
                session.user,
                self._backend,
                self._postgres_dsn,
            )
        except UnknownUser as exc:
            self._plain_status(404, f"unknown user: {exc.user}")
            return True
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("constituents store failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _store_portfolio_name(self) -> bool:
        """Handle ``POST /api/constituents/name``; plain-text statuses on failure."""
        from .constituents import store_portfolio_name

        if urlsplit(self.path).path != "/api/constituents/name":
            return False
        session = self._require_session()
        if session is None:
            return True
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
            result = store_portfolio_name(
                self._assets_file,
                payload.get("name"),
                session.user,
                self._backend,
                self._postgres_dsn,
            )
        except UnknownUser as exc:
            self._plain_status(404, f"unknown user: {exc.user}")
            return True
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("portfolio name update failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _reorder_constituents(self) -> bool:
        """Handle ``POST /api/constituents/order``; plain-text statuses on failure."""
        from .constituents import reorder_constituents

        if urlsplit(self.path).path != "/api/constituents/order":
            return False
        session = self._require_session()
        if session is None:
            return True
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
            result = reorder_constituents(
                self._assets_file,
                payload.get("bucket"),
                payload.get("order"),
                session.user,
                self._backend,
                self._postgres_dsn,
            )
        except UnknownUser as exc:
            self._plain_status(404, f"unknown user: {exc.user}")
            return True
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("constituents reorder failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _delete_constituent(self) -> bool:
        """Handle ``POST /api/constituents/delete``; plain-text statuses on failure."""
        from .constituents import delete_constituent

        if urlsplit(self.path).path != "/api/constituents/delete":
            return False
        session = self._require_session()
        if session is None:
            return True
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
            result = delete_constituent(
                self._assets_file,
                payload.get("bucket"),
                payload.get("index"),
                session.user,
                self._backend,
                self._postgres_dsn,
            )
        except UnknownUser as exc:
            self._plain_status(404, f"unknown user: {exc.user}")
            return True
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("constituents delete failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps(result).encode("utf-8")
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

    def _serve_brokers(self) -> bool:
        """Serve ``GET /api/constituents/brokers`` with dropdown data."""
        from .constituents import known_brokers

        if urlsplit(self.path).path != "/api/constituents/brokers":
            return False
        body = json.dumps({"brokers": known_brokers()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        if not (
            self._redirect_root()
            or self._serve_login_page()
            or self._serve_dashboard()
            or self._serve_session_status()
            or self._serve_update_status()
            or self._serve_brokers()
            or self._serve_constituents()
        ):
            super().do_GET()

    def do_HEAD(self) -> None:
        if not (
            self._redirect_root()
            or self._serve_login_page()
            or self._serve_dashboard()
            or self._serve_session_status()
            or self._serve_update_status()
            or self._serve_brokers()
            or self._serve_constituents()
        ):
            super().do_HEAD()

    def _update_mode(self) -> str:
        """Update mode from the optional JSON body; anything but fat is lite."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 65536:
            return "lite"
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            return "lite"
        if isinstance(payload, dict) and payload.get("mode") == "fat":
            return "fat"
        return "lite"

    def _run_update(self) -> bool:
        """Handle ``POST /api/update``: lite refresh, or         fat with ``{"mode": "fat"}``.


        Quiesces the systemd update units first (both stops must succeed or
        nothing runs); then runs the update in a spawned child process while
        this handler thread blocks in ``join``, so the POST still answers
        once the run is over. A cancel SIGTERMs the child (SIGKILL past a
        grace period), which wakes this thread here; the timer is restored
        before the running flag clears, so the sync button stays pressed
        throughout. A second POST while one runs gets 409.
        """
        if urlsplit(self.path).path != "/api/update":
            return False
        session = self._require_session()
        if session is None:
            return True
        try:
            if not self._assets_file or not self._cache_file:
                raise RuntimeError("assets file not configured")
            fat = self._update_mode() == "fat"
            if fat:
                logger.info("programmatic fat update requested")
            global _update_job
            with _update_lock:
                if _update_job is not None:
                    self._json_status(409, {"error": "update already in progress"})
                    return True
                job_id = next(_update_ids)
                cancel_event = _mp_ctx.Event()
                recv_conn, send_conn = _mp_ctx.Pipe(duplex=False)
                _update_job = {
                    "id": job_id,
                    "cancel": cancel_event,
                    "process": None,
                    "terminated": False,
                }
            try:
                _quiesce_update_units()
            except RuntimeError as exc:
                logger.warning("update quiesce failed: %s", exc)
                _clear_update_job(job_id)
                self._plain_status(500, f"update not started: {exc}")
                return True
            proc = _spawn_update_process(
                _update_worker,
                (
                    str(self._assets_file),
                    str(self._cache_file),
                    fat,
                    str(self.directory),
                    session.user,
                    self._backend,
                    self._postgres_dsn,
                    send_conn,
                    cancel_event,
                ),
            )
            with _update_lock:
                if _update_job is not None and _update_job["id"] == job_id:
                    _update_job["process"] = proc
                    spawned_into_cancel = _update_job["terminated"]
                else:
                    spawned_into_cancel = True
            if spawned_into_cancel:
                # Cancelled while spawning: kill it straight away.
                _terminate_process(proc)
                _finish_update_job(job_id, False, "update cancelled: cancelled by user")
                self._json_status(409, {"error": "update cancelled: cancelled by user"})
                return True
            proc.join()
            if _job_terminated(job_id):
                _finish_update_job(job_id, False, "update cancelled: cancelled by user")
                self._json_status(409, {"error": "update cancelled: cancelled by user"})
                return True
            if proc.exitcode == 0:
                try:
                    # Blocking take: the send lands in the OS pipe buffer,
                    # so this returns as soon as the result exists.
                    result = recv_conn.recv() if recv_conn.poll(30) else None
                except (EOFError, OSError):
                    result = None
                finally:
                    recv_conn.close()
                if not isinstance(result, dict):
                    _finish_update_job(
                        job_id, False, "update failed: worker reported nothing"
                    )
                    self._plain_status(500, "update failed: worker reported nothing")
                    return True
                if result.get("ok"):
                    _finish_update_job(job_id, True, "")
                else:
                    error = result.get("error") or "update failed"
                    _finish_update_job(job_id, False, error)
                    self._plain_status(500, error)
                    return True
            else:
                error = f"update failed: worker exited with code {proc.exitcode}"
                _finish_update_job(job_id, False, error)
                self._plain_status(500, error)
                return True
        except Exception as exc:
            logger.warning("programmatic update failed: %s", exc)
            self._plain_status(500, f"update failed: {exc}")
            return True
        body = json.dumps({"updated": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _json_status(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cancel_update(self) -> bool:
        """Handle ``POST /api/cancel``: SIGTERM a running update, SIGKILL if needed.

        Sets the cooperative flag too (a graceful checkpoint may beat the
        signal), then kills outside the lock so status reads never block.
        The blocked update POST wakes from its join, records the
        cancellation, restores the timer, and answers 409 itself.
        """
        if urlsplit(self.path).path != "/api/cancel":
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > 0:
            self.rfile.read(min(length, 65536))
        with _update_lock:
            job = _update_job
            if job is not None:
                job["cancel"].set()
                job["terminated"] = True
                proc = job["process"]
            else:
                proc = None
        if proc is not None:
            _terminate_process(proc)
        self._json_status(200, {"cancelled": proc is not None})
        return True

    def _read_json_body(self) -> dict | None:
        """Read a JSON object body; 4xx already sent on failure (None)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 65536:
            self._plain_status(400, "empty or oversized request body")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            self._plain_status(400, f"invalid JSON: {exc}")
            return None
        if not isinstance(payload, dict):
            self._plain_status(400, "body must be a JSON object")
            return None
        return payload

    def _check_isin(self) -> bool:
        """Handle ``POST /api/constituents/check-isin`` via JustETF."""
        from position.justetf_position import just_etf_product_url_exists

        if urlsplit(self.path).path != "/api/constituents/check-isin":
            return False
        payload = self._read_json_body()
        if payload is None:
            return True
        raw = payload.get("isin")
        if not isinstance(raw, str) or not raw.strip():
            self._plain_status(400, "isin must be a non-blank string")
            return True
        try:
            exists = just_etf_product_url_exists(raw.strip())
        except Exception as exc:
            logger.warning("constituents isin check failed: %s", exc)
            self._plain_status(502, f"ISIN check unavailable: {exc}")
            return True
        body = json.dumps({"exists": bool(exists)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _add_constituent(self) -> bool:
        """Handle ``POST /api/constituents/add``; plain-text statuses on failure."""
        from .constituents import _render_row, add_constituent

        if urlsplit(self.path).path != "/api/constituents/add":
            return False
        session = self._require_session()
        if session is None:
            return True
        payload = self._read_json_body()
        if payload is None:
            return True
        try:
            if not self._assets_file:
                raise RuntimeError("assets file not configured")
            cache: dict[str, Any] = {}
            if self._cache_file:
                try:
                    with open(self._cache_file, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        cache = loaded
                except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                    logger.warning("constituents cache unreadable: %s", exc)
            display = add_constituent(
                self._assets_file,
                payload.get("bucket"),
                name=payload.get("name"),
                short_name=payload.get("short_name"),
                isin=payload.get("isin"),
                value=payload.get("value"),
                shares=payload.get("shares"),
                broker=payload.get("broker"),
                cache=cache,
                user=session.user,
                backend=self._backend,
                postgres_dsn=self._postgres_dsn,
            )
        except UnknownUser as exc:
            self._plain_status(404, f"unknown user: {exc.user}")
            return True
        except ValueError as exc:
            self._plain_status(400, str(exc))
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("constituents add failed: %s", exc)
            self._plain_status(502, f"constituents unavailable: {exc}")
            return True
        body = json.dumps(
            {"index": display["index"], "row": _render_row(display)}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_POST(self) -> None:
        if not (
            self._login()
            or self._logout()
            or self._store_constituent()
            or self._store_portfolio_name()
            or self._reorder_constituents()
            or self._delete_constituent()
            or self._check_isin()
            or self._add_constituent()
            or self._run_update()
            or self._cancel_update()
        ):
            self._plain_status(404, "unknown endpoint")

    def translate_path(self, path: str) -> str:
        url_path = urlsplit(path).path
        if url_path in DASHBOARD_PATHS:
            return super().translate_path("/index.html")
        if url_path in LOGIN_PATHS:
            return super().translate_path("/login.html")
        if url_path == DATA_PREFIX or url_path.startswith(DATA_PREFIX + "/"):
            # Charts are per user: /data/<file>.raw serves the logged-in
            # user's chart dir.
            user = self._session_user()
            if not user:
                return super().translate_path("/__not_found__")
            rest = url_path[len(DATA_PREFIX):].lstrip("/")
            base = f"/{self._data_dirname}/{user}"
            disk_path = f"{base}/{rest}" if rest else f"{base}/"
            # Jail the remap under the user's data dir: normpath collapses
            # "/data/../index.html" to "/index.html", which would leak the
            # dashboard through /data. Force such escapes to a missing path
            # so they stay 404.
            norm = posixpath.normpath(disk_path)
            if norm != base and not norm.startswith(base + "/"):
                return super().translate_path("/__not_found__")
            return super().translate_path(disk_path)
        return super().translate_path(path)


def serve_forever(
    *,
    address: str,
    port: int,
    directory: str,
    assets_file: str | None = None,
    cache_file: str | None = None,
    users_file: str | None = None,
    data_dirname: str = DATA_DIRNAME,
    user: str = DEFAULT_USER_NAME,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> None:
    """Serve *directory* until interrupted (runs in the foreground)."""
    handler = functools.partial(
        DashboardHandler,
        directory=directory,
        assets_file=assets_file,
        cache_file=cache_file,
        users_file=users_file,
        data_dirname=data_dirname,
        user=user,
        backend=backend,
        postgres_dsn=postgres_dsn,
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
    parser.add_argument("--users-file", default=None, help="Users JSON file holding the accounts that may log in.")
    parser.add_argument("--user", default=DEFAULT_USER_NAME, help="Default portfolio user (default: default); ?user= selects another.")
    parser.add_argument("--backend", default=BACKEND_JSON, help="Assets store backend (json | postgres).")
    parser.add_argument("--postgres-dsn", default=DEFAULT_POSTGRES_DSN, help="Postgres connection string for the postgres backend.")
    parser.add_argument("--plotter-data-dir", default=DATA_DIRNAME, help="On-disk data dir name under --directory (default: data).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    serve_forever(
        address=args.bind,
        port=args.port,
        directory=args.directory,
        assets_file=args.assets_file,
        cache_file=args.cache_file,
        users_file=args.users_file,
        data_dirname=args.plotter_data_dir,
        user=args.user,
        backend=args.backend,
        postgres_dsn=args.postgres_dsn,
    )


if __name__ == "__main__":
    main()
