# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
import configparser
import logging
import os
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cli.update import main as run_update
from cli.context import AppConfig, RuntimeContext, ServerConfig
from cli.logger import attach_color_stderr_handler_for_module, configure_cli_logging
from cli.stores import StoreLocationMissing, db_file

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


def _package_version() -> str:
    try:
        return version("asset-allocation")
    except PackageNotFoundError:
        print(
            "error: asset-allocation is not installed; run `pip install -e .` from the project root",
            file=sys.stderr,
        )
        sys.exit(1)


__version__ = _package_version()


def config_path() -> Path:
    return AppConfig.config_path()


def load_server_config(path: Path | None = None) -> ServerConfig:
    return AppConfig.server_from_ini(path)


def server_port(path: Path | None = None) -> int:
    return load_server_config(path).port


def cmd_update(args: argparse.Namespace) -> None:
    config = AppConfig.from_cli(args, ini_path=getattr(args, "config", None))
    configure_cli_logging(getattr(logging, config.log_level))
    # The database must have a location: a flag or an ini key.
    db_file(
        ini_path=getattr(args, "config", None),
        flag_value=getattr(args, "db_file", None),
    )
    ctx = RuntimeContext(config=config)
    run_update(ctx)


def _pid_is_server(pid: int) -> bool:
    """True when /proc shows *pid* as a visualizer server process."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="replace")
    except OSError:
        return False
    return "visual.web.backend.serve" in cmdline or "http.server" in cmdline


def _server_argv(pid: int) -> list[str]:
    """Argv of *pid* from /proc, or [] when unreadable."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            parts = f.read().split(b"\0")
    except OSError:
        return []
    return [part.decode(errors="replace") for part in parts if part]


def _find_server_pids(port: int) -> list[int]:
    """Pids running our visualizer server bound to *port* (via /proc scan)."""
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        argv = _server_argv(int(entry))
        if not argv:
            continue
        if not any("visual.web.backend.serve" in arg for arg in argv):
            continue
        if str(port) not in argv:
            continue
        found.append(int(entry))
    return found


def cmd_stop(_args: argparse.Namespace) -> None:
    # Resolves the pid file from the same config `serve` used, so custom
    # --config files and relative `directory` values agree on location.
    cfg = load_server_config(getattr(_args, "config", None))
    pid_file = cfg.directory / ".serve.pid"
    if not pid_file.is_file():
        logger.warning("No visualizer server pid file at %s; nothing to stop.", pid_file)
        return
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        logger.warning("Ignoring malformed pid file at %s.", pid_file)
        pid_file.unlink(missing_ok=True)
        return
    try:
        if _pid_is_server(pid):
            os.kill(pid, signal.SIGTERM)
            logger.info("Stopped visualizer server (pid %s).", pid)
        else:
            logger.warning("Visualizer server pid %s is not running.", pid)
    except ProcessLookupError:
        logger.warning("Visualizer server pid %s is not running.", pid)
    except PermissionError:
        logger.warning("No permission to stop pid %s; leaving pid file.", pid)
        return
    pid_file.unlink(missing_ok=True)
    # The pid file may be stale (dead pid) while a server started by other
    # means still holds the port. Sweep for leftovers so a restart really
    # restarts instead of leaving a wedged server in place.
    for other in _find_server_pids(cfg.port):
        try:
            os.kill(other, signal.SIGTERM)
            logger.info("Stopped leftover visualizer server (pid %s).", other)
        except ProcessLookupError:
            continue
        except PermissionError:
            logger.warning("No permission to stop leftover server pid %s.", other)


def cmd_add_user(args: argparse.Namespace) -> None:
    """Add a user account to the SQLite users table."""
    from cli.users import add_user_account

    try:
        cfg = AppConfig.from_ini(args.config)
    except (FileNotFoundError, ValueError, configparser.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    user = str(args.user).strip()
    if not user:
        print("error: --user must not be empty", file=sys.stderr)
        sys.exit(2)
    try:
        database = db_file(ini_path=args.config)
    except StoreLocationMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    password = args.password
    try:
        created = add_user_account(database, user, password)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if created:
        print(f"added user {user!r} to {database}")
    else:
        print(f"user {user!r} already exists in {database} (password updated)")


def cmd_serve(args: argparse.Namespace) -> None:
    cfg = load_server_config(getattr(args, "config", None))
    if getattr(args, "config", None) is not None:
        # Propagate to the server child (and its update workers), which
        # read ini defaults from the environment.
        os.environ["ASALLOC_CONFIG"] = str(args.config)
    ini_cfg = AppConfig.from_ini(getattr(args, "config", None))
    ini_data_dir = ini_cfg.server.data_dir
    cfg.directory.mkdir(parents=True, exist_ok=True)
    (cfg.directory / ini_data_dir).mkdir(exist_ok=True)
    pid_file = cfg.directory / ".serve.pid"
    already = [pid for pid in _find_server_pids(cfg.port)]
    if already:
        logger.error(
            "A visualizer server is already running on port %s (pid %s); "
            "stop it first with `asalloc stop-serve`.",
            cfg.port,
            ", ".join(str(pid) for pid in already),
        )
        sys.exit(1)
    # The database comes from the ini ([server] db_file); an explicit
    # --db-file always wins.
    ini_path = getattr(args, "config", None)
    try:
        database = db_file(
            ini_path=ini_path, flag_value=getattr(args, "db_file", None)
        )
    except StoreLocationMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    user = getattr(args, "user", None) or ini_cfg.user
    child_cmd = [
        sys.executable,
        "-m",
        "visual.web.backend.serve",
        "--port",
        str(cfg.port),
        "--bind",
        cfg.address,
        "--directory",
        str(cfg.directory),
        "--user",
        str(user),
        "--db-file",
        str(database),
        "--plotter-data-dir",
        ini_data_dir,
    ]
    proc = subprocess.Popen(
        child_cmd,
        cwd=cfg.directory,
        # The server (and the update workers it spawns) inherit our
        # stdout/stderr: under systemd that is the journal, so a failed
        # request is visible instead of vanishing into /dev/null.
        start_new_session=True,
    )
    # Fail fast when the server dies immediately (busy port, bad config,
    # broken code): otherwise the pid file points at a dead process and
    # later restarts silently do nothing.
    grace_end = time.monotonic() + 3.0
    while time.monotonic() < grace_end:
        if proc.poll() is not None:
            logger.error(
                "Visualizer server exited immediately (exit %s); not writing pid file.",
                proc.returncode,
            )
            sys.exit(1)
        time.sleep(0.2)
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    logger.info(
        "Serving %s in the background on http://%s:%s/dashboard (pid %s)",
        cfg.directory,
        cfg.address,
        cfg.port,
        proc.pid,
    )


def cmd_clear_sessions(args: argparse.Namespace) -> None:
    """Drop the browser sessions (everybody logs in again).

    The sessions live in the ``sessions`` table of the database. The
    location comes from the same config.ini the server reads, so the
    command always clears the sessions the running server uses.
    """
    from visual.web.backend.session import clear_sessions

    ini_path = getattr(args, "config", None)
    try:
        cfg = load_server_config(ini_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        database = db_file(
            ini_path=ini_path,
            flag_value=getattr(args, "db_file", None),
        )
    except StoreLocationMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    expired_only = bool(getattr(args, "expired_only", False))
    try:
        cleared = clear_sessions(database, expired_only=expired_only)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: could not clear the sessions: {exc}", file=sys.stderr)
        sys.exit(1)
    what = "expired session(s)" if expired_only else "session(s)"
    print(f"cleared {cleared} {what}")


def _add_config_flag(
    parser: argparse.ArgumentParser, *, default=None, required: bool = False
) -> None:
    help_text = (
        "Path to config.ini (overrides $ASALLOC_CONFIG and the built-in "
        "default). May be given before or after the subcommand."
    )
    if required:
        help_text = "Path to config.ini (required by this command)."
    parser.add_argument(
        "--config",
        type=Path,
        default=default,
        required=required,
        help=help_text,
    )


def _add_update_flags(update: argparse.ArgumentParser) -> None:
    update.add_argument(
        "--fetch-prices",
        action="store_true",
        default=None,
        help=(
            "Scrape live JustETF/Yahoo quotes and refresh the cached price. Holdings values then come from shares × quote, and "
            "that value is written back to the store for brokers that "
            "were not live-scraped. When combined with "
            "--fetch-scalable / --fetch-tr / --fetch-oskar, the scraped "
            "holdings value is favored over shares × quote. Without this "
            "flag, a broker value from the store prevails over "
            "shares × cached price. The store never stores price: "
            "only shares and value."
        ),
    )
    update.add_argument(
        "--fetch-geosplit",
        action="store_true",
        default=None,
        help=(
            "Scrape country allocations (JustETF) and write them to the cache. "
            "Without this flag, country weights are read from cache."
        ),
    )
    update.add_argument(
        "--fetch-sectorsplit",
        action="store_true",
        default=None,
        help=(
            "Scrape sector allocations (JustETF) and write them to the cache. "
            "Without this flag, sector weights are read from cache."
        ),
    )
    update.add_argument(
        "--user",
        type=str,
        default=None,
        help="Portfolio user whose assets the run reads and writes (default: from config.ini [update] user, else 'default').",
    )
    update.add_argument(
        "--db-file",
        type=Path,
        dest="db_file",
        default=None,
        help=(
            "SQLite database with every store (default: from config.ini "
            "[server] db_file)."
        ),
    )
    update.add_argument(
        "--position-source",
        choices=("justetf", "yfinance"),
        default="justetf",
        help="Price/split source for positions (default: justetf).",
    )
    update.add_argument(
        "--fetch-oskar",
        action="store_true",
        default=None,
        help="Log into Oskar and scrape ETF positions. Missing share counts are estimated from holdings value / quote (cached, or freshly fetched if --fetch-prices is also set).",
    )
    update.add_argument(
        "--fetch-scalable",
        action="store_true",
        default=None,
        help="Log into Scalable via a headless browser device login and scrape "
        "broker holdings. Email/password are prompted in the terminal, 2FA "
        "stays on your phone; needs an interactive terminal.",
    )
    update.add_argument(
        "--fetch-tr",
        action="store_true",
        default=None,
        help="Log into Trade Republic via pytr and scrape broker holdings.",
    )
    update.add_argument(
        "--plot",
        choices=("web", "pie-chart"),
        default=None,
        help="Chart backend: web writes *.raw files; pie-chart opens matplotlib windows (default: config.ini [plotter] type, else web).",
    )
    update.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=argparse.SUPPRESS,
        help="Logging level for stderr (overrides the global --log-level).",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Portfolio allocation report.",
        epilog=f"version {__version__}",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level for stderr (default: config.ini [update] log_level, else INFO). Use DEBUG for verbose OSKAR steps.",
    )
    _add_config_flag(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser(
        "update",
        help="Load the database, optionally scrape brokers/prices, and write charts.",
        epilog=(
            "Every store lives in one SQLite database (--db-file or "
            "[server] db_file in config.ini)."
        ),
    )
    _add_config_flag(update, default=argparse.SUPPRESS)
    _add_update_flags(update)
    update.set_defaults(func=cmd_update)

    serve = subparsers.add_parser(
        "serve",
        help="Serve the visualizer directory over HTTP in the background (address and port from config.ini).",
        epilog=(
            "Every store lives in one SQLite database (--db-file or "
            "[server] db_file in config.ini)."
        ),
    )
    _add_config_flag(serve, default=argparse.SUPPRESS)
    serve.add_argument(
        "--user",
        type=str,
        default=None,
        help="Default portfolio user for the served views (default: from config.ini [update] user, else 'default'). The ?user= query selects another one.",
    )
    serve.add_argument(
        "--db-file",
        type=Path,
        dest="db_file",
        default=None,
        help=(
            "SQLite database with every store (default: from "
            "config.ini [server] db_file)."
        ),
    )
    serve.set_defaults(func=cmd_serve)

    add_user = subparsers.add_parser(
        "add-user",
        help="Add a user account to the SQLite users table.",
    )
    _add_config_flag(add_user, required=True)
    add_user.add_argument(
        "--user",
        type=str,
        required=True,
        help="User name to add (the account key).",
    )
    add_user.add_argument(
        "--password",
        type=str,
        default=None,
        help="Password for the account (default: empty for a new account, kept for an existing one).",
    )
    add_user.set_defaults(func=cmd_add_user)

    stop_serve = subparsers.add_parser(
        "stop-serve",
        help="Stop the background visualizer server started by serve (pid file from config.ini).",
    )
    _add_config_flag(stop_serve, default=argparse.SUPPRESS)
    stop_serve.set_defaults(func=cmd_stop)

    clear_sessions_parser = subparsers.add_parser(
        "clear-sessions",
        help="Drop the browser sessions (everybody has to log in again).",
        epilog=(
            "The sessions live in the 'sessions' table of the SQLite "
            "database. The location comes from config.ini, like the "
            "server's."
        ),
    )
    _add_config_flag(clear_sessions_parser, default=argparse.SUPPRESS)
    clear_sessions_parser.add_argument(
        "--db-file",
        type=Path,
        dest="db_file",
        default=None,
        help=(
            "SQLite database with every store (default: from config.ini "
            "[server] db_file)."
        ),
    )
    clear_sessions_parser.add_argument(
        "--expired-only",
        action="store_true",
        help="Only drop the sessions that are past their expiry.",
    )
    clear_sessions_parser.set_defaults(func=cmd_clear_sessions)

    args = parser.parse_args(argv)
    configure_cli_logging(getattr(logging, getattr(args, "log_level", None) or "ERROR"))
    args.func(args)


if __name__ == "__main__":
    main()
