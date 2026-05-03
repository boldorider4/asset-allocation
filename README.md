# asset-allocation

Personal portfolio reporter: load holdings from JSON, fetch prices (JustETF or Yahoo Finance via `yfinance`), and print bucket values plus simple geographic-style splits (developed vs emerging, US vs non-US within developed).

## Setup

```bash
pip install -e .
```

Requires Python 3.10+. Declared dependencies: `numpy`, `yfinance`. The default JustETF backend uses only the standard library for HTTP.

## Holdings file

Copy or create `assets.json` in the project root. It must be a JSON object whose keys are portfolio buckets used by `allocation.py`:

| Key | Purpose |
| --- | --- |
| `equity_portfolio` | Equity positions (used for DM/EM and US splits) |
| `fixed_maturity_bond_portfolio` | Fixed-maturity bonds |
| `cash_portfolio` | Cash / emergency fund |
| `bond_portfolio` | Other bonds |
| `commodity_portfolio` | Commodities |

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

`assets.json` is listed in `.gitignore` so you can keep real balances private; commit a redacted example if you share the repo.

## Price source

In `asset_price/factory.py`, set `POSITION_SOURCE` to `justetf` (default) or `yfinance`. With caching enabled, new prices are written to `cache.json` (also gitignored).

## Run

From the repository root:

```bash
python allocation.py
```

Refresh all quotes without reading the cache (fetched prices are still written to `cache.json`):

```bash
python allocation.py --no-cache
```

## Disclaimer

This is a personal tooling repo, not financial advice. Prices and third-party sites can be wrong or unavailable; verify anything material before you act.
