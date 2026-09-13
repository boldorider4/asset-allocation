import argparse
import configparser
import logging
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from allocation import main as run_update
from logger import attach_color_stderr_handler_for_module, configure_cli_logging
from utils import (
    set_assets_file,
    set_fetch_geosplit,
    set_fetch_oskar,
    set_fetch_prices,
    set_fetch_scalable,
    set_fetch_traderepublic,
    set_incognito,
)

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.ini"
VISUALIZER_DIR = REPO_ROOT / "_visualizer"
SERVE_PID_FILE = VISUALIZER_DIR / ".serve.pid"

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


def server_port(config_path: Path = CONFIG_PATH) -> int:
    parser = configparser.ConfigParser()
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path}")
    parser.read(config_path, encoding="utf-8")
    return parser.getint("server", "port")


def cmd_update(args: argparse.Namespace) -> None:
    if args.fetch_prices:
        set_fetch_prices(True)
    if args.fetch_geosplit:
        set_fetch_geosplit(True)
    if args.fetch_oskar:
        set_fetch_oskar(True)
    if args.fetch_scalable:
        set_fetch_scalable(True)
    if args.fetch_tr:
        set_fetch_traderepublic(True)
    if args.assets_file:
        set_assets_file(args.assets_file)
    if args.incognito:
        set_incognito(True)
    run_update()


def cmd_serve(_args: argparse.Namespace) -> None:
    port = server_port()
    visualizer = VISUALIZER_DIR
    visualizer.mkdir(parents=True, exist_ok=True)
    (visualizer / "data").mkdir(exist_ok=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--directory",
            str(visualizer),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    SERVE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    logger.info(
        "Serving %s in the background on http://127.0.0.1:%s (pid %s)",
        visualizer,
        port,
        proc.pid,
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
        "--assets-file",
        type=Path,
        help="Path to the assets JSON file.",
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
    update.set_defaults(func=cmd_update)

    serve = subparsers.add_parser(
        "serve",
        help="Serve _visualizer over HTTP in the background (port from config.ini).",
    )
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    configure_cli_logging(getattr(logging, args.log_level))
    args.func(args)


if __name__ == "__main__":
    main()
