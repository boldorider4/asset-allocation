# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
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


def cmd_serve(args: argparse.Namespace) -> None:
    from cli.context import DEFAULT_ASSETS_PATH, DEFAULT_CACHE_PATH

    cfg = load_server_config(getattr(args, "config", None))
    if getattr(args, "config", None) is not None:
        # Propagate to the server child (and its update workers), which
        # read ini defaults from the environment.
        os.environ["ASALLOC_CONFIG"] = str(args.config)
    try:
        ini_cfg = AppConfig.from_ini(getattr(args, "config", None))
    except (FileNotFoundError, ValueError):
        ini_cfg = AppConfig()
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
    assets_file = Path(args.assets_file) if args.assets_file else DEFAULT_ASSETS_PATH
    cache_file = Path(args.cache_file) if args.cache_file else DEFAULT_CACHE_PATH
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "visual.web.backend.serve",
            "--port",
            str(cfg.port),
            "--bind",
            cfg.address,
            "--directory",
            str(cfg.directory),
            "--assets-file",
            str(assets_file),
            "--cache-file",
            str(cache_file),
            "--plotter-data-dir",
            ini_data_dir,
        ],
        cwd=cfg.directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def _add_config_flag(parser: argparse.ArgumentParser, *, default=None) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=default,
        help=(
            "Path to config.ini (overrides $ASALLOC_CONFIG and the built-in "
            "default). May be given before or after the subcommand."
        ),
    )


def _add_update_flags(update: argparse.ArgumentParser) -> None:
    update.add_argument(
        "--fetch-prices",
        action="store_true",
        default=None,
        help=(
            "Scrape live JustETF/Yahoo quotes and refresh the price in "
            "cache.json. Holdings values then come from shares × quote, and "
            "that value is written back to the assets file for brokers that "
            "were not live-scraped. When combined with "
            "--fetch-scalable / --fetch-tr / --fetch-oskar, the scraped "
            "holdings value is favored over shares × quote. Without this "
            "flag, a broker value from the assets file prevails over "
            "shares × cached price. The assets file never stores price: "
            "only shares and value."
        ),
    )
    update.add_argument(
        "--fetch-geosplit",
        action="store_true",
        default=None,
        help=(
            "Scrape country allocations (JustETF) and write them to cache.json. "
            "Without this flag, country weights are read from cache."
        ),
    )
    update.add_argument(
        "--fetch-sectorsplit",
        action="store_true",
        default=None,
        help=(
            "Scrape sector allocations (JustETF) and write them to cache.json. "
            "Without this flag, sector weights are read from cache."
        ),
    )
    update.add_argument(
        "--assets-file",
        type=Path,
        dest="assets_file",
        default=None,
        help="Path to the assets JSON file (default: assets.json next to the package).",
    )
    update.add_argument(
        "--cache-file",
        type=Path,
        dest="cache_file",
        default=None,
        help="Path to the cache JSON file (default: cache.json next to the package).",
    )
    update.add_argument(
        "--isin-registry-file",
        type=Path,
        dest="isin_file",
        default=None,
        help="Path to the ISIN registry JSON file (default: from config.ini).",
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
        help="Load the assets file, optionally scrape brokers/prices, and write charts.",
    )
    _add_config_flag(update, default=argparse.SUPPRESS)
    _add_update_flags(update)
    update.set_defaults(func=cmd_update)

    serve = subparsers.add_parser(
        "serve",
        help="Serve the visualizer directory over HTTP in the background (address and port from config.ini).",
    )
    _add_config_flag(serve, default=argparse.SUPPRESS)
    serve.add_argument(
        "--assets-file",
        type=Path,
        dest="assets_file",
        default=None,
        help="Assets JSON file backing /constituents (default: assets.json next to the package).",
    )
    serve.add_argument(
        "--cache-file",
        type=Path,
        dest="cache_file",
        default=None,
        help="Cache JSON file backing /constituents prices (default: cache.json next to the package).",
    )
    serve.set_defaults(func=cmd_serve)

    stop_serve = subparsers.add_parser(
        "stop-serve",
        help="Stop the background visualizer server started by serve (pid file from config.ini).",
    )
    _add_config_flag(stop_serve, default=argparse.SUPPRESS)
    stop_serve.set_defaults(func=cmd_stop)

    args = parser.parse_args(argv)
    configure_cli_logging(getattr(logging, getattr(args, "log_level", None) or "ERROR"))
    args.func(args)


if __name__ == "__main__":
    main()
