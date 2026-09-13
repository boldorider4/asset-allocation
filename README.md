# asset-allocation

Personal portfolio reporter: load holdings from JSON, optionally fetch prices and broker positions, then chart allocation (regional split, growth sleeve, total net worth).

![Asset Allocation web visualizer](visual/media/web_view.png)

## Setup

```bash
pip install -e .
```

Requires Python 3.10+. Dependencies include `numpy`, `matplotlib`, `yfinance`, `playwright`, and `pytr`.

## Holdings file

Copy or create `assets.json` in the project root. It must be a JSON object whose keys are portfolio buckets used by `allocation.py`:

| Key | Purpose |
| --- | --- |
| `equity_portfolio` | Equity positions (used for DM/EM and US splits) |
| `fixed_maturity_bond_portfolio` | Fixed-maturity bonds |
| `cash_portfolio` | Cash / emergency fund |
| `bond_portfolio` | Other bonds |
| `commodity_portfolio` | Commodities |
| `pension_portfolio` | Pension |

Each bucket is an array of position objects. Typical fields:

| Field | Meaning |
| --- | --- |
| `ISIN` | Identifier for price lookup (may be `null` if you only use `value`) |
| `shares` | Units held (optional if `value` is set) |
| `value` | Fixed position value in account currency (optional if priced from ISIN) |
| `broker` | Broker label (used by implementations where relevant) |
| `dmem` | Share of position treated as **developed** markets (0–1) |
| `dmem_other` | When a country is “other”, fraction treated as developed (0–1) |
| `usavn` | Within developed markets, fraction attributed to the **US** (0–1) |

`assets.json` is listed in `.gitignore` so you can keep real balances private; `assets.sample.json` is a redacted starting point.

## Price source

Positions are built through `position/factory.py` (JustETF by default, Yahoo Finance via `yfinance` for some ISINs). With `--fetch-prices`, quotes are written to `cache.json` (also gitignored). `--fetch-geosplit` refreshes country weights in the same cache.

## Run

From the repository root, after `pip install -e .`:

```bash
asalloc
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--fetch-prices` | Scrape live quotes into `cache.json` |
| `--fetch-geosplit` | Scrape country allocations into `cache.json` |
| `--fetch-oskar` / `--fetch-scalable` / `--fetch-tr` | Scrape broker holdings |
| `--incognito` | Scale display values |
| `--assets-file PATH` | Use a holdings file other than `assets.json` |

## Web visualizer

The default chart backend (`WebChart`) writes one JSON `*.raw` file per pie into `_visualizer/data/`. Scaffold the JS app (without touching existing raw files) with:

```bash
make web
```

Then run `asalloc` and serve `_visualizer` over HTTP (browsers cannot list `file://` directories), for example:

```bash
python -m http.server --directory _visualizer
```

| Target | What it does |
| --- | --- |
| `make web` | Copy `visual/web` into `_visualizer`, stamp version and GitHub URL |
| `make web-example` | Same, plus sample `*.raw` files (no server, no browser) |
| `make web-clean` | Delete `_visualizer` |

To use matplotlib windows instead, set `DEFAULT_VISUALIZER` in `visual/__init__.py` to `PieChart`.

## Disclaimer

This is a personal tooling repo, not financial advice. Prices and third-party sites can be wrong or unavailable; verify anything material before you act.
