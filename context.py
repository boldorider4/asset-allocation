# SPDX-License-Identifier: AGPL-3.0-or-later
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
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as _MultiprocessingEvent

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.ini"
DEFAULT_ASSETS_PATH = REPO_ROOT / "assets.json"
DEFAULT_CACHE_PATH = REPO_ROOT / "cache.json"

PositionSource = Literal["justetf", "yfinance"]
PlotterKind = Literal["web", "pie-chart"]

_VALID_SOURCES: tuple[str, ...] = ("justetf", "yfinance")
_VALID_PLOTTERS: tuple[str, ...] = ("web", "pie-chart")
_VALID_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class ServerConfig:
    port: int
    address: str
    directory: Path
    # Disk layout: the served data tree lives at ``directory/data_dir``.
    # Kept separate from the plotter output dir on purpose: one instance
    # may serve what another one wrote.
    data_dir: str = "data"


@dataclass
class PlotterConfig:
    """Chart output layout (the write side, independent of the serve side)."""

    output_dir: Path | None = None
    clear_dir: str = "clear"
    incognito_dir: str = "incognito"


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
    plotter_config: PlotterConfig = field(default_factory=PlotterConfig)
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
    def _ini_parser(cls, path: Path | None) -> tuple[Path, configparser.ConfigParser]:
        cfg_path = cls.config_path(path)
        parser = configparser.ConfigParser()
        if not cfg_path.is_file():
            raise FileNotFoundError(f"config file not found: {cfg_path}")
        parser.read(cfg_path, encoding="utf-8")
        return cfg_path, parser

    @classmethod
    def _resolve_ini_path(
        cls, cfg_path: Path, raw: str | None, fallback: Path | None
    ) -> Path | None:
        if not raw or not raw.strip():
            return fallback
        path = Path(raw.strip()).expanduser()
        if not path.is_absolute():
            path = (cfg_path.parent / path).resolve()
        return path

    @classmethod
    def server_from_ini(cls, path: Path | None = None) -> ServerConfig:
        cfg_path, parser = cls._ini_parser(path)
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
        data_dir = (
            parser.get("server", "plotter_data_dir", fallback="data").strip() or "data"
        )
        return ServerConfig(port=port, address=address, directory=directory, data_dir=data_dir)

    @classmethod
    def _update_from_ini(cls, path: Path | None = None) -> dict[str, Any]:
        """``[update]`` section with today's defaults; missing file → defaults."""
        defaults: dict[str, Any] = {
            "fetch_prices": False,
            "fetch_geosplit": False,
            "fetch_sectorsplit": False,
            "plot_clear": False,
            "plot_incognito": False,
            "log_level": "INFO",
            "assets_file": DEFAULT_ASSETS_PATH,
            "cache_file": DEFAULT_CACHE_PATH,
        }
        try:
            cfg_path, parser = cls._ini_parser(path)
        except FileNotFoundError:
            return defaults
        if not parser.has_section("update"):
            return defaults
        values = dict(defaults)
        for flag in (
            "fetch_prices",
            "fetch_geosplit",
            "fetch_sectorsplit",
            "plot_clear",
            "plot_incognito",
        ):
            values[flag] = parser.getboolean("update", flag, fallback=defaults[flag])
        log_level = parser.get("update", "log_level", fallback="INFO").strip() or "INFO"
        if log_level not in _VALID_LOG_LEVELS:
            raise ValueError(f"unknown log level {log_level!r}")
        values["log_level"] = log_level
        values["assets_file"] = cls._resolve_ini_path(
            cfg_path,
            parser.get("update", "assets_file", fallback=None),
            DEFAULT_ASSETS_PATH,
        )
        values["cache_file"] = cls._resolve_ini_path(
            cfg_path,
            parser.get("update", "cache_file", fallback=None),
            DEFAULT_CACHE_PATH,
        )
        return values

    @classmethod
    def _plotter_from_ini(cls, path: Path | None = None) -> tuple[PlotterKind, PlotterConfig]:
        """``[plotter]`` section; missing file/keys → today's defaults."""
        try:
            cfg_path, parser = cls._ini_parser(path)
        except FileNotFoundError:
            return "web", PlotterConfig()
        kind = parser.get("plotter", "type", fallback="web").strip() or "web"
        if kind not in _VALID_PLOTTERS:
            raise ValueError(f"unknown plotter {kind!r}")
        output_raw = parser.get("plotter", "output_dir", fallback=None)
        output_dir = cls._resolve_ini_path(cfg_path, output_raw, fallback=None)
        clear_dir = (
            parser.get("plotter", "clear_directory", fallback="clear").strip() or "clear"
        )
        incognito_dir = (
            parser.get("plotter", "incognito_directory", fallback="incognito").strip()
            or "incognito"
        )
        return kind, PlotterConfig(  # type: ignore[return-value]
            output_dir=output_dir, clear_dir=clear_dir, incognito_dir=incognito_dir
        )

    @classmethod
    def from_ini(cls, path: Path | None = None) -> AppConfig:
        server = cls.server_from_ini(path)
        update = cls._update_from_ini(path)
        kind, plotter_config = cls._plotter_from_ini(path)
        return cls(server=server, plotter=kind, plotter_config=plotter_config, **update)

    @classmethod
    def from_cli(
        cls, args: argparse.Namespace, ini_path: Path | None = None
    ) -> AppConfig:
        """Build from parsed ``update`` args over ini defaults; CLI wins.

        Explicit CLI flags (anything not ``None``) override the ``[update]``
        and ``[plotter]`` sections; unset flags fall back to them, then to
        today's defaults. ``position_source`` and broker fetch flags have
        no ini keys and behave as before.
        """
        try:
            server = cls.server_from_ini(ini_path)
        except FileNotFoundError:
            server = ServerConfig(
                port=8765,
                address="localhost",
                directory=Path.home() / ".local" / "asalloc" / "visualizer",
            )
        ini_update = cls._update_from_ini(ini_path)
        ini_kind, ini_plotter = cls._plotter_from_ini(ini_path)

        def pick(cli_value: Any, ini_value: Any) -> Any:
            return cli_value if cli_value is not None else ini_value

        source = getattr(args, "position_source", "justetf")
        if source not in _VALID_SOURCES:
            raise ValueError(f"unknown position source {source!r}")
        plotter = pick(getattr(args, "plot", None), ini_kind)
        if plotter not in _VALID_PLOTTERS:
            raise ValueError(f"unknown plotter {plotter!r}")
        log_level = pick(getattr(args, "log_level", None), ini_update["log_level"])
        if log_level not in _VALID_LOG_LEVELS:
            raise ValueError(f"unknown log level {log_level!r}")
        assets = getattr(args, "assets_file", None)
        cache = getattr(args, "cache_file", None)
        return cls(
            fetch_prices=bool(pick(getattr(args, "fetch_prices", None), ini_update["fetch_prices"])),
            fetch_geosplit=bool(pick(getattr(args, "fetch_geosplit", None), ini_update["fetch_geosplit"])),
            fetch_sectorsplit=bool(pick(getattr(args, "fetch_sectorsplit", None), ini_update["fetch_sectorsplit"])),
            fetch_oskar=bool(getattr(args, "fetch_oskar", False)),
            fetch_scalable=bool(getattr(args, "fetch_scalable", False)),
            fetch_traderepublic=bool(getattr(args, "fetch_tr", False)),
            plot_clear=bool(pick(getattr(args, "plot_clear", None), ini_update["plot_clear"])),
            plot_incognito=bool(pick(getattr(args, "plot_incognito", None), ini_update["plot_incognito"])),
            position_source=source,  # type: ignore[arg-type]
            plotter=plotter,  # type: ignore[arg-type]
            assets_file=Path(assets) if assets else ini_update["assets_file"],
            cache_file=Path(cache) if cache else ini_update["cache_file"],
            server=server,
            plotter_config=ini_plotter,
            log_level=log_level,
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
    # Either a threading or a multiprocessing event (child-process runs).
    cancel_event: threading.Event | _MultiprocessingEvent | None = None
    # Lazily built ``CacheRepository`` over a ``JsonStorage`` backend for
    # ``config.cache_file``. The plain ``cache`` dict above stays the
    # in-memory source of truth for reads; the repository is the
    # validated write path (see ``restore`` / ``_mirror_cache_row``).
    # Built once: ``cache_file`` is only ever set at config construction,
    # never mutated afterwards.
    _cache_repo: Any = field(default=None, init=False, repr=False)

    # -- portfolio --
    def load_portfolio(self, path: Path | None = None) -> None:
        from utils import load_portfolio as _load

        self.portfolio.clear()
        self.portfolio.update(_load(path or self.config.assets_file))

    def flush_portfolio(self, path: Path | None = None) -> None:
        from utils import write_portfolio as _write

        _write(path or self.config.assets_file, self.portfolio)

    # -- cache (in-memory + flush) --
    @property
    def cache_repo(self):  # type: ignore[no-untyped-def]
        """Validated cache access: ``CacheRepository`` over ``JsonStorage``.

        Lazily built against ``config.cache_file``. All cache writes in
        the update path go through here; the plain ``cache`` dict remains
        the in-memory read model (mirrored on every write).
        """
        from storage.json_storage import JsonStorage
        from storage.records import CacheEntry
        from storage.repositories import CacheRepository

        if self._cache_repo is None:
            self._cache_repo = CacheRepository(
                JsonStorage(self.config.cache_file, CacheEntry)
            )
        return self._cache_repo

    def _mirror_cache_row(self, isin: str) -> None:
        """Copy one validated repository row back into the plain ``cache`` dict."""
        entry = self.cache_repo.get(str(isin))
        if entry is None:
            self.cache.pop(str(isin), None)
        else:
            self.cache[str(isin)] = entry.to_dict()

    def ensure_cache_loaded(self) -> dict[str, Any]:
        if self.cache_loaded:
            # Dict may have been seeded/assigned directly (notably in
            # tests): restore it into the repository so validated reads
            # see it.
            self.cache_repo.restore(self.cache)
            return self.cache
        logger.info("loading cache from %s", self.config.cache_file)
        self.cache_repo.open()
        self.cache = self.cache_repo.snapshot()
        self.cache_loaded = True
        self.cache_dirty = False
        return self.cache

    def mark_cache_dirty(self) -> None:
        self.cache_dirty = True

    def flush_cache(self) -> None:
        if not self.cache_loaded or not self.cache_dirty:
            return
        # Dict is the source of truth (it may have been mutated directly):
        # restore, then persist through repository verbs only — no backend
        # nouns (``load``/``save``/``connect``/``commit``) appear here, so a
        # future backend swap touches nothing in this method.
        self.cache_repo.restore(self.cache)
        self.cache_repo.persist()
        self.cache_dirty = False
        logger.info("wrote cache to %s", self.config.cache_file)

    # -- plotter selection (explicit, no module global) --
    def plotter_class(self):  # type: ignore[no-untyped-def]
        from visual import PLOTTERS

        try:
            return PLOTTERS[self.config.plotter]
        except KeyError as exc:
            raise ValueError(f"unknown plotter {self.config.plotter!r}") from exc

    def output_data_dir(self, *, incognito: bool = False) -> Path:
        """Chart output dir: the configured plotter output dir (or the
        legacy ``server.directory/data`` tree) plus the clear/incognito
        subdir. The write side is independent of the serve side on
        purpose: one instance may serve what another one wrote."""
        base = self.config.plotter_config.output_dir
        if base is None:
            base = self.config.server.directory / self.config.server.data_dir
        subdir = (
            self.config.plotter_config.incognito_dir
            if incognito
            else self.config.plotter_config.clear_dir
        )
        return base / subdir

    def configure_web_output(self, *, incognito: bool = False):  # type: ignore[no-untyped-def]
        """Point WebChart file output at the pass's dir; reset seq."""
        from visual.plot.web_chart import WebChart

        WebChart.data_dir = self.output_data_dir(incognito=incognito)
        WebChart._slug_counts = {}
        WebChart._plot_seq = 0
