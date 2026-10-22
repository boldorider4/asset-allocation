# asset-allocation

> **Disclaimer — Not Financial Advice.**
> This tool is provided `AS-IS`, without warranty of any kind, for informational
> purposes only. Prices, splits, and scraped data may be wrong, delayed, or
> unavailable. Do not make financial decisions solely on its output — verify
> independently and consult a licensed advisor. The authors accept no liability
> for financial loss or damages from its use. See `LICENSE`.

## Description

Self-hosted portfolio dashboard: load holdings from JSON, sync live prices, geosplits, sectors and broker balances, then explore allocation in the web UI.

![Asset Allocation web visualizer](visual/media/web_view.png)

## Setup

```bash
make install
```

Requires Python 3.10+. Dependencies include `numpy`, `matplotlib`, `yfinance`, `playwright`, `ccy`, `curl_cffi`, `pycountry`, and `pytr`.

## Holdings file

The holdings file is keyed by **user**: each top-level key is a user name, and its value holds that user's portfolio — a name and the positions, grouped by asset class.

```json
{
  "default": {
    "portfolio_name": "Portfolio",
    "positions": {
      "equity_portfolio": [ { "ISIN": "IE000BI8OT95", "shares": 220, "value": null } ],
      "bond_portfolio": []
    }
  }
}
```

| Key | Purpose |
| --- | --- |
| `portfolio_name` | The name of this user's portfolio (`"Portfolio"` until it is renamed) |
| `positions` | The positions, grouped by asset class |

The user name comes from `config.ini` (`[update] user = default`) and can be overridden per run with `--user NAME`. Charts are per user too: an update writes to `<output_dir>/<user>/`, and the server serves the selected user's charts.

The store itself is backend-agnostic: the code above the storage layer only talks to `storage.repositories.AssetRepository`, and the concrete backend is chosen in one place (`storage/wiring.py`) from the `[server] backend` setting in `config.ini`:

| `[server] backend` | Meaning |
| --- | --- |
| `json` (default) | Every store in its own JSON file (`assets.json`, `cache.json`, `isin.json`, `users.json`, `.sessions.json`) |
| `sqlite` | Every store in one SQLite database (`[server] db_file`), one table per store |
| `postgres` | Not implemented yet: the wiring logs an error and the store fails to open |

`[server] postgres_dsn` holds the connection string for the future Postgres backend (`postgresql://localhost/asalloc` by default). `[server] db_file` names the SQLite database (`~/.local/asalloc/asalloc.db` by default).

**Where the stores live.** The backend decides: with `sqlite` every store is a table in the one database (`[server] db_file`), with `json` each store is its own file (`[update] assets_file` / `cache_file` / `users_file` / `isin_file`). The two are alternatives — `asalloc serve` and `asalloc update` take either `--db-file` or the per-store `--*-file` options, never both, and the ini key counts as the location just like a flag (the built-in defaults do *not*: they point into the repository, not into the deployment, so a store with neither a flag nor an ini key is a configuration error).

**SQLite.** The database runs *inside* the process: no server, no idle load, nothing to supervise — which is what a 24/7 Raspberry Pi wants. WAL mode lets the web server read while the update writes, and every table of a database shares one connection (two connections would deadlock against each other's write transaction). The tables are `user_assets`, `cache`, `isin_registry`, `users` and `sessions`; the payload of a row is its JSON form, so a row written by one backend reads back identically in the other.

The shared connection is used by the server's request threads, so its use is **serialized with a lock**: Python's `sqlite3` keeps its prepared statements on the connection, and one thread resetting a statement another thread is stepping corrupts the row that thread reads (an empty row, or a `NULL` payload where a row was expected). The lock is what makes the one-connection-per-file design safe on a multi-threaded server.

To move an existing installation over, import the JSON stores once and flip the backend:

```bash
python scripts/migrate_json_to_sqlite.py --db-file ~/.local/asalloc/asalloc.db
```

The script leaves the JSON files untouched (they stay the fallback until you delete them), skips a broken row with a warning and is idempotent. After that, `config.ini` → `[server] backend = sqlite` is the only change — the server, the update and `asalloc add-user` all read it.

| Key | Purpose |
| --- | --- |
| `equity_portfolio` | Equity positions (used for DM/EM and US splits) |
| `fixed_maturity_bond_portfolio` | Fixed-maturity bonds |
| `cash_portfolio` | Cash / emergency fund |
| `bond_portfolio` | Other bonds |
| `commodity_portfolio` | Commodities |
| `pension_portfolio` | Pension |

Each asset class is an array of position objects. Typical fields:

| Field | Meaning |
| --- | --- |
| `ISIN` | Identifier for price lookup (may be `null` if you only use `value`) |
| `shares` | Units held (optional if `value` is set) |
| `value` | Fixed position value in account currency (optional if priced from ISIN) |
| `broker` | Broker label (used by implementations where relevant) |
| `dmem` | Share of position treated as **developed** markets (0–1) |
| `dmem_other` | When a country is “other”, fraction treated as developed (0–1) |
| `usavn` | Within developed markets, fraction attributed to the **US** (0–1) |

`assets.json` is listed in `.gitignore` so you can keep real balances private; `assets.sample.json` is a redacted starting point (it ships with the `default` user).

### Users file

The accounts that may log in live in their own store, `users.json` (path from `config.ini` → `[update] users_file`). It is keyed by user name — the same key a database table would use — and holds the password:

```json
{
  "default": { "password": "" }
}
```

Passwords may be empty (the account exists, it just has no secret yet). Copy `users.sample.json` to the configured path and add your users:

```bash
cp users.sample.json ~/.local/asalloc/users.json
```

`users.json` is gitignored (it holds credentials). The store is backend-agnostic: `config.ini` → `[server] backend` moves it into the `users` table of the database together with the assets.

**Adding users.** Use the script or the CLI command — both write the same store:

```bash
python scripts/add_users.py ~/.local/asalloc/users.json alice --password secret
asalloc add-user --config ~/.local/asalloc/config.ini --user alice --password secret
asalloc add-user --config /path/to/config.ini --user alice    # empty password
```

The CLI command reads the users file from the given `config.ini` (`[update] users_file`); `--config` and `--user` are required, `--password` is optional. A new account gets an empty password when none is given; an existing account keeps its password unless a new one is passed. The `--backend postgres` path is a stub and does not work yet.
**Login.** The login screen asks for a **User** and a **Password**: the name must have an account in `users.json` (a name that only appears in `assets.json` is unknown) and the password must match the one stored there. A wrong password is refused exactly like an unknown user, so the login cannot be used to probe for user names — both boxes light up red. Empty passwords are allowed (an account with no password logs in with an empty field). A known account with no portfolio yet gets an empty one on first login, so it lands on an empty dashboard. The password is stored in plaintext for now — hashing belongs in `storage/records.py::UserRecord` when it lands.

**Reset password.** The "Reset password" link under the password box turns the form into a reset (the password box becomes "Old Password", a "New Password" box appears, the button becomes "Reset" and the incognito toggle goes away — still on the login page). The old password must match; an empty new password is allowed. On success the login form comes back and the new password is the one that logs in.

### Migrating an old holdings file

The current format nests the positions under `"positions"` and names the portfolio. A file in the previous shape (the asset classes directly under the user) is rejected with a validation error — run the migration script once per file:

```bash
python scripts/migrate_assets_positions.py ~/.local/asalloc/assets.json
python scripts/migrate_assets_positions.py ~/.local/asalloc/assets.json --portfolio-name "90-10 Equity Portfolio"
```

The script backs the original up before writing (a `.bak` sibling), touches only the file you pass, and leaves a file that is already current untouched.

A **pre-user** file (the asset classes directly at the top level) needs one more step first — it is rejected with a pointer to its own script:

```bash
python scripts/migrate_assets_user.py ~/.local/asalloc/assets.json default
python scripts/migrate_assets_positions.py ~/.local/asalloc/assets.json
```

Neither script ever reads `config.ini` or touches anything else in your home directory.

> The `?user=` selector on the web server is **not authentication**: anyone who can reach the server can read and edit any user's holdings. Keep it on a trusted network.

## Price source

Positions are built through `position/factory.py` (JustETF by default, Yahoo Finance via `yfinance` for some ISINs). With `--fetch-prices`, quotes are written to `cache.json` (also gitignored). `--fetch-geosplit` refreshes country weights and `--fetch-sectorsplit` refreshes sector weights in the same cache.

## Run

From the repository root, after `make install`:

```bash
asalloc update
```

Useful `update` flags:

| Flag | Purpose |
| --- | --- |
| `--config PATH` | Use a `config.ini` other than `$ASALLOC_CONFIG` (may go before or after the subcommand) |
| `--fetch-prices` | Scrape live quotes into `cache.json` |
| `--fetch-geosplit` | Scrape country allocations into `cache.json` |
| `--fetch-sectorsplit` | Scrape sector allocations into `cache.json` |
| `--fetch-oskar` / `--fetch-scalable` / `--fetch-tr` | Scrape broker holdings |
| `--user NAME` | Portfolio user to read and write (default: `config.ini` `[update] user`, else `default`) |
| `--assets-file PATH` | Use a holdings file other than `assets.json` |
| `--plot {web,pie-chart}` | Chart backend (default `web`) |

## Web visualizer

The default chart backend (`WebChart`) writes one JSON `*.raw` file per pie into `~/.local/asalloc/visualizer/data/<user>/` (the `[plotter] output_dir` plus the user subdir). Scaffold the JS app (without touching existing raw files) with:

```bash
make web
```

Then run `asalloc update` and serve the visualizer over HTTP (browsers cannot list `file://` directories):

```bash
make serve
# or
asalloc serve
```

Stop it with `make stop-serve`. Open `http://localhost:8765/` — it sends you to the login page.

**Login.** The login screen asks for a **User** (an account in `users.json` — see [Users file](#users-file)) and has an **Incognito** toggle next to the "Log In" button, with an `OFF`/`ON` read-out. The toggle decides the `?incognito=True|False` flag on the dashboard URL you land on. Unknown users are rejected (no account, no session).

The session lives in a cookie (`asalloc_sid`) and is persisted server-side (the `sessions` table of the database, or `.sessions.json` in the visualizer workspace with the json backend), so it survives restarts — but it **expires after 30 minutes**, after which the next request lands on the login page again. **Logout** (top right, next to Edit) drops it right away; the next visit lands on the login page again. The logged-in user decides which holdings and which charts are served — there is no per-request user parameter any more — and their name is shown in the dashboard header, left of the buttons.

**Clearing the sessions.** `asalloc clear-sessions` drops every session (everybody has to log in again); `--expired-only` drops only the ones past their expiry. The store is the one the backend names, so the command always clears the sessions the running server uses:

```bash
asalloc clear-sessions --config ~/.local/asalloc/config.ini
asalloc clear-sessions --config ~/.local/asalloc/config.ini --expired-only
```

`http://localhost:8765/dashboard` shows the charts of the logged-in user — the incognito button blurs Euro figures in place (instant CSS toggle, no reload; `?incognito=true` carries the pressed state through the constituents round trip) and never changes the data. The constituents page has the same button, blurring Value/Shares figures there.

The charts are titled with the **portfolio name** from the store: the growth charts read "<Portfolio Name>: Geo Breakdown" and "<Portfolio Name>: Sector Breakdown" (the name is edited at the end of the constituents page; the next Dashboard click runs the lite update that re-titles them), while the whole-net-worth chart stays "Complete Portfolio".

`http://localhost:8765/constituents` shows the holdings of the logged-in user from `assets.json` (prices from `cache.json`) as tables with editable shares/value boxes. Changing a box (Enter, Tab, or click-away) persists to `assets.json` via `POST /api/constituents` — empty means `0`, editing shares also clears the stored value so the next update recomputes it from shares × cached price, locked cells are rejected server-side, and the cell flashes on success or reverts on failure. Use `asalloc serve --assets-file PATH --cache-file PATH --users-file PATH` to point the endpoint at other files (without the flags, the paths come from `config.ini`); with the `sqlite` backend the one database holds them all and `asalloc serve --db-file PATH` points the endpoint at it.

The last block of the page edits the **Portfolio Name** (the field at the end of the page). It persists via `POST /api/constituents/name` and stages an update like the other edits, so the next **Dashboard** click runs the lite update that puts the name into the charts.

Clicking Dashboard runs a lite refresh first — but only if a cell was edited since the page loaded, in which case it navigates to the dashboard once the refresh completes (`POST /api/update`: no scraping flags, log level ERROR). With no edits it navigates straight to the dashboard.

Editing shares immediately recomputes value as shares × cached price (and vice versa), so the file always holds a consistent pair; without a cached price the sibling is left untouched and the edited box gets a red flare. Empty means `0`, locked cells are rejected server-side, and a normal save flashes green then reverts on failure. A missing cached price is live-fetched once for that position and written back to the cache. For admin use, `make service` also copies `systemd/asalloc-lite-update.service` (same lite update) but leaves it disabled — start it yourself when needed.

The dashboard's Sync Prices button posts `{"mode": "fat"}` to the same endpoint for a fat refresh (live prices, geosplits, and sectorsplits; broker scrapes never run there). It takes minutes and reloads the gallery when done. Leaving via Edit fires `POST /api/cancel` with `keepalive` and navigates immediately without waiting for it; only one endpoint update runs at a time, timer/systemd runs are unaffected.

The app is a single document at `/dashboard`: the view is frontend state in the hash (`#constituents`, or absent for the dashboard), while `?incognito=` stays backend-tracked. Opening `/dashboard#constituents` directly jumps into the constituents view, but a reload always defaults back to the dashboard. The first Edit click fetches the constituents markup once; afterwards switching (clicks and Back/Forward, via `hashchange`) swaps cached views in place with no document reload. Freshness is verified on every show — the dashboard re-renders when the chart signature changed, the constituents page revalidates in the background without touching open drafts or focused inputs — and any fetch failure falls back to a classic navigation. All responses carry `Cache-Control: no-cache`, so deploys and updates take effect without ever clearing browser data. The dashboard also paints its last gallery from a per-tab snapshot on cold loads, and chart payloads load in parallel. Editing a cell still runs the lite refresh before showing the dashboard — that part is unavoidable.

The bind address, HTTP port, and directory come from `config.ini` (`[server] address`, `port`, and `directory`; defaults `localhost`, `8765`, and `~/.local/asalloc/visualizer`). The server runs in the background.

| Target | What it does |
| --- | --- |
| `make install` | `pip install -e .`, plus a `~/.local/bin/asalloc` symlink to wherever `pip` put the entry point (venv/conda-aware, so systemd finds it) |
| `make web` | Copy `visual/web/frontend` into `~/.local/asalloc/visualizer`, stamp version and GitHub URL |
| `make web-example` | Same, plus sample `*.raw` files (no server, no browser) |
| `make serve` | Scaffold the web app, then background HTTP server for the visualizer dir (from `config.ini`); fails if `asalloc` is not callable |
| `make clean` | Same as `make web-clean` (so `make clean web` rebuilds the visualizer from scratch) |
| `make stop-serve` | Stop the background visualizer server |
| `make service` | Linux: install systemd user units, enable serve + 3-hour update timer |
| `make stop-service` | Linux: stop and disable those user units |
| `make web-clean` | Delete `~/.local/asalloc/visualizer` |

On a headless Debian box, after `make install` and `make service`, put holdings at `~/.local/asalloc/assets.json` and enable lingering so the user units run without a login:

```bash
sudo loginctl enable-linger "$USER"
```

Units live in `systemd/` (`*.service` / `*.timer` for systemd) and are copied to `~/.config/systemd/user/`.

To use matplotlib windows instead, pass `--plot pie-chart` (or set `DEFAULT_VISUALIZER` in `visual/__init__.py`).

## License

Licensed under `AGPL-3.0-or-later` — see `LICENSE`. If you run a modified
version as a backend/network service, you must offer the modified source
to its users.
