import argparse
import logging
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from allocation import main as run_update
from context import AppConfig, RuntimeContext, ServerConfig
from logger import attach_color_stderr_handler_for_module, configure_cli_logging

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
    config = AppConfig.from_cli(args)
    ctx = RuntimeContext(config=config)
    run_update(ctx)


def cmd_serve(_args: argparse.Namespace) -> None:
    cfg = load_server_config()
    cfg.directory.mkdir(parents=True, exist_ok=True)
    (cfg.directory / "data").mkdir(exist_ok=True)
    pid_file = cfg.directory / ".serve.pid"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(cfg.port),
            "--bind",
            cfg.address,
            "--directory",
            str(cfg.directory),
        ],
        cwd=cfg.directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    logger.info(
        "Serving %s in the background on http://%s:%s (pid %s)",
        cfg.directory,
        cfg.address,
        cfg.port,
        proc.pid,
    )


def _add_update_flags(update: argparse.ArgumentParser) -> None:
    update.add_argument(
        "--fetch-prices",
        action="store_true",
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
        help=(
            "Scrape country allocations (JustETF) and write them to cache.json. "
            "Without this flag, country weights are read from cache."
        ),
    )
    update.add_argument(
        "--fetch-sectorsplit",
        action="store_true",
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
        "--position-source",
        choices=("justetf", "yfinance"),
        default="justetf",
        help="Price/split source for positions (default: justetf).",
    )
    update.add_argument(
        "--fetch-oskar",
        action="store_true",
        help="Log into Oskar and scrape ETF positions. Missing share counts are estimated from holdings value / quote (cached, or freshly fetched if --fetch-prices is also set).",
    )
    update.add_argument(
        "--fetch-scalable",
        action="store_true",
        help="Log into Scalable via sc and scrape broker holdings.",
    )
    update.add_argument(
        "--fetch-tr",
        action="store_true",
        help="Log into Trade Republic via pytr and scrape broker holdings.",
    )
    update.add_argument(
        "--incognito",
        action="store_true",
        help="Show fake values for asset allocation.",
    )
    update.add_argument(
        "--plot",
        choices=("web", "pie-chart"),
        default="web",
        help="Chart backend: web writes *.raw files; pie-chart opens matplotlib windows.",
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
        default="INFO",
        help="Logging level for stderr (default: INFO). Use DEBUG for verbose OSKAR steps.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser(
        "update",
        help="Load the assets file, optionally scrape brokers/prices, and write charts.",
    )
    _add_update_flags(update)
    update.set_defaults(func=cmd_update)

    serve = subparsers.add_parser(
        "serve",
        help="Serve the visualizer directory over HTTP in the background (address and port from config.ini).",
    )
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    configure_cli_logging(getattr(logging, args.log_level))
    args.func(args)


if __name__ == "__main__":
    main()
