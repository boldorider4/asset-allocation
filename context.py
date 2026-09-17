"""Explicit runtime configuration and per-run state.

Replaces the former module-level globals (``utils.portfolio``,
``utils.FETCH_*``, ``common.PENDING_*``, ``factory.POSITION_SOURCE``,
``visual.DEFAULT_VISUALIZER``, ``scrape.global_*``) with two dataclasses:

* :class:`AppConfig` — immutable-ish run configuration merged from
  ``config.ini`` + CLI flags (+ ``ASALLOC_CONFIG`` env override).
* :class:`RuntimeContext` — mutable per-run state: portfolio data,
  in-memory cache, pending write-backs, fetched broker results.

Every layer (factory, Position, Portfolio, scrape, allocation) takes a
``RuntimeContext`` explicitly instead of reading module globals, so runs
are isolated and tests can build fresh contexts without leakage.
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.ini"
DEFAULT_ASSETS_PATH = REPO_ROOT / "assets.json"
DEFAULT_CACHE_PATH = REPO_ROOT / "cache.json"

PositionSource = Literal["justetf", "yfinance"]
PlotterKind = Literal["web", "pie-chart"]

_VALID_SOURCES: tuple[str, ...] = ("justetf", "yfinance")
_VALID_PLOTTERS: tuple[str, ...] = ("web", "pie-chart")


@dataclass
class ServerConfig:
    port: int
    address: str
    directory: Path


@dataclass
class AppConfig:
    """Merged run configuration: ``config.ini`` server section + CLI flags."""

    fetch_prices: bool = False
    fetch_geosplit: bool = False
    fetch_sectorsplit: bool = False
    fetch_oskar: bool = False
    fetch_scalable: bool = False
    fetch_traderepublic: bool = False
    plot_clear: bool = False
    plot_incognito: bool = False
    position_source: PositionSource = "justetf"
    plotter: PlotterKind = "web"
    assets_file: Path = field(default_factory=lambda: DEFAULT_ASSETS_PATH)
    cache_file: Path = field(default_factory=lambda: DEFAULT_CACHE_PATH)
    server: ServerConfig = field(
        default_factory=lambda: ServerConfig(
            port=8765,
            address="localhost",
            directory=Path.home() / ".local" / "asalloc" / "visualizer",
        )
    )
    log_level: str = "INFO"

    @property
    def ignore_cache(self) -> bool:
        """``--fetch-prices`` implies skipping cached quotes."""
        return self.fetch_prices

    @classmethod
    def config_path(cls, override: Path | None = None) -> Path:
        if override is not None:
            return override
        env = os.environ.get("ASALLOC_CONFIG")
        if env:
            return Path(env).expanduser()
        return DEFAULT_CONFIG_PATH

    @classmethod
    def server_from_ini(cls, path: Path | None = None) -> ServerConfig:
        cfg_path = cls.config_path(path)
        parser = configparser.ConfigParser()
        if not cfg_path.is_file():
            raise FileNotFoundError(f"config file not found: {cfg_path}")
        parser.read(cfg_path, encoding="utf-8")
        port = parser.getint("server", "port")
        address = parser.get("server", "address", fallback="localhost").strip() or "localhost"
        raw = parser.get(
            "server",
            "directory",
            fallback=str(Path.home() / ".local" / "asalloc" / "visualizer"),
        )
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            directory = (cfg_path.parent / directory).resolve()
        return ServerConfig(port=port, address=address, directory=directory)

    @classmethod
    def from_ini(cls, path: Path | None = None) -> AppConfig:
        return cls(server=cls.server_from_ini(path))

    @classmethod
    def from_cli(
        cls, args: argparse.Namespace, ini_path: Path | None = None
    ) -> AppConfig:
        """Build from parsed ``update`` args; server section from ini."""
        try:
            server = cls.server_from_ini(ini_path)
        except FileNotFoundError:
            server = ServerConfig(
                port=8765,
                address="localhost",
                directory=Path.home() / ".local" / "asalloc" / "visualizer",
            )
        source = getattr(args, "position_source", "justetf")
        if source not in _VALID_SOURCES:
            raise ValueError(f"unknown position source {source!r}")
        plotter = getattr(args, "plot", "web")
        if plotter not in _VALID_PLOTTERS:
            raise ValueError(f"unknown plotter {plotter!r}")
        assets = getattr(args, "assets_file", None)
        cache = getattr(args, "cache_file", None)
        return cls(
            fetch_prices=bool(getattr(args, "fetch_prices", False)),
            fetch_geosplit=bool(getattr(args, "fetch_geosplit", False)),
            fetch_sectorsplit=bool(getattr(args, "fetch_sectorsplit", False)),
            fetch_oskar=bool(getattr(args, "fetch_oskar", False)),
            fetch_scalable=bool(getattr(args, "fetch_scalable", False)),
            fetch_traderepublic=bool(getattr(args, "fetch_tr", False)),
            plot_clear=bool(getattr(args, "plot_clear", False)),
            plot_incognito=bool(getattr(args, "plot_incognito", False)),
            position_source=source,  # type: ignore[arg-type]
            plotter=plotter,  # type: ignore[arg-type]
            assets_file=Path(assets) if assets else DEFAULT_ASSETS_PATH,
            cache_file=Path(cache) if cache else DEFAULT_CACHE_PATH,
            server=server,
            log_level=getattr(args, "log_level", "INFO"),
        )


@dataclass
class RuntimeContext:
    """Mutable per-run state; one instance per run/test, never shared."""

    config: AppConfig = field(default_factory=AppConfig)
    portfolio: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    cache_loaded: bool = False
    cache_dirty: bool = False
    pending_oskar_shares: dict[tuple[str, float], float] = field(default_factory=dict)
    pending_fetched_values: dict[tuple[str, str | None], float] = field(
        default_factory=dict
    )
    oskar_etfs: dict[str, Any] = field(default_factory=dict)
    scalable_holdings: dict[str, Any] = field(default_factory=dict)
    traderepublic_holdings: dict[str, Any] = field(default_factory=dict)
    # Display-only value scaler for incognito plots (computed once per run;
    # stored clear values are never mutated).
    value_factor: float = 1.0
    # Cooperative cancellation for endpoint-triggered runs. Checked per
    # position in ``position.factory``; ``None`` means non-cancellable.
    cancel_event: threading.Event | None = None

    # -- portfolio --
    def load_portfolio(self, path: Path | None = None) -> None:
        from utils import load_portfolio as _load

        self.portfolio.clear()
        self.portfolio.update(_load(path or self.config.assets_file))

    def flush_portfolio(self, path: Path | None = None) -> None:
        from utils import write_portfolio as _write

        _write(path or self.config.assets_file, self.portfolio)

    # -- cache (in-memory + flush) --
    def ensure_cache_loaded(self) -> dict[str, Any]:
        if self.cache_loaded:
            return self.cache
        path = self.config.cache_file
        logger.info("loading cache from %s", path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.cache = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            logger.info("cache file not found, starting with empty cache")
            self.cache = {}
        except json.JSONDecodeError:
            logger.warning("cache file %s is not valid JSON; starting empty", path)
            self.cache = {}
        self.cache_loaded = True
        self.cache_dirty = False
        return self.cache

    def mark_cache_dirty(self) -> None:
        self.cache_dirty = True

    def flush_cache(self) -> None:
        if not self.cache_loaded or not self.cache_dirty:
            return
        path = self.config.cache_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2)
        self.cache_dirty = False
        logger.info("wrote cache to %s", path)

    # -- plotter selection (explicit, no module global) --
    def plotter_class(self):  # type: ignore[no-untyped-def]
        from visual import PLOTTERS

        try:
            return PLOTTERS[self.config.plotter]
        except KeyError as exc:
            raise ValueError(f"unknown plotter {self.config.plotter!r}") from exc

    def output_data_dir(self, *, incognito: bool = False) -> Path:
        """Chart output dir: ``data/incognito`` for incognito passes, else ``data/clear``."""
        base = self.config.server.directory / "data"
        return base / "incognito" if incognito else base / "clear"

    def configure_web_output(self, *, incognito: bool = False):  # type: ignore[no-untyped-def]
        """Point WebChart file output at the pass's dir; reset seq."""
        from visual.web_chart import WebChart

        WebChart.data_dir = self.output_data_dir(incognito=incognito)
        WebChart._slug_counts = {}
        WebChart._plot_seq = 0
