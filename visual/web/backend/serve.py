# SPDX-License-Identifier: AGPL-3.0-or-later
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
import itertools
import json
import logging
import multiprocessing
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .constituents import load_constituents, render_constituents_page

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
# Upper bound for one endpoint-triggered update (fat updates take minutes).
# When the deadline passes, the worker is terminated and the job is closed
# so the sync button can never spin forever.
UPDATE_TIMEOUT_S = 600.0

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
    result_conn,
    cancel_event,
) -> None:
    """Child-process entry point: build a fresh context and run the update.

    Only picklable arguments cross the boundary; the ``RuntimeContext``
    and log suppression are constructed here. Sends ``{"ok", "error"}``
    down the pipe; a killed child sends nothing and the parent reads the
    exit code instead. The send lands in the OS pipe buffer, so it
    survives the child dying with no flush dance.
    """
    from allocation import main as run_update
    from context import AppConfig, RuntimeContext, ServerConfig
    from position.factory import UpdateCancelled

    config = AppConfig(
        assets_file=Path(assets_file),
        cache_file=Path(cache_file),
        fetch_prices=fat,
        fetch_geosplit=fat,
        fetch_sectorsplit=fat,
        plot_clear=True,
        plot_incognito=fat,
        server=ServerConfig(port=0, address="localhost", directory=Path(directory)),
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

    def _serve_update_status(self) -> bool:
        """Serve ``GET /api/update`` with the run state for the sync button."""
        if urlsplit(self.path).path != "/api/update":
            return False
        body = json.dumps(_update_status_payload()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
        return True

    def _serve_constituents(self) -> bool:
        """Render the constituents page; 502 with a plain reason on failure."""
        parts = urlsplit(self.path)
        if parts.path not in CONSTITUENTS_PATHS:
            return False
        try:
            if not self._assets_file or not self._cache_file:
                raise RuntimeError("assets file not configured")
            body = render_constituents_page(
                load_constituents(self._assets_file, self._cache_file),
                incognito=incognito_flag(parts.query),
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
        from .constituents import store_constituent_value

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
            )
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

    def _reorder_constituents(self) -> bool:
        """Handle ``POST /api/constituents/order``; plain-text statuses on failure."""
        from .constituents import reorder_constituents

        if urlsplit(self.path).path != "/api/constituents/order":
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
            result = reorder_constituents(
                self._assets_file,
                payload.get("bucket"),
                payload.get("order"),
            )
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
            )
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
            or self._serve_update_status()
            or self._serve_brokers()
            or self._serve_constituents()
        ):
            super().do_GET()

    def do_HEAD(self) -> None:
        if not (
            self._redirect_root()
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
        job_id: int | None = None
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
                    send_conn,
                    cancel_event,
                ),
            )
            deadline = time.monotonic() + UPDATE_TIMEOUT_S
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
            # Wait for the process to finish, but never forever: a hung
            # worker is terminated at the deadline so the job always closes.
            proc.join(timeout=max(1.0, deadline - time.monotonic()))
            if proc.is_alive():
                if _job_terminated(job_id):
                    _terminate_process(proc)
                    _finish_update_job(job_id, False, "update cancelled: cancelled by user")
                    self._json_status(409, {"error": "update cancelled: cancelled by user"})
                    return True
                else:
                    _terminate_process(proc)
                    _finish_update_job(job_id, False, "update timed out")
                    self._plain_status(500, "update timed out")
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
            if job_id is not None:
                # Never leave a job behind: an uncleared job reports
                # updating:true forever and wedges the sync button.
                try:
                    _finish_update_job(job_id, False, f"update failed: {exc}")
                except Exception as finish_exc:
                    logger.warning("update job cleanup failed: %s", finish_exc)
            try:
                self._plain_status(500, f"update failed: {exc}")
            except Exception:
                pass
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
            )
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
            self._store_constituent()
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
