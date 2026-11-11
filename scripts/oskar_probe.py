#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live diagnostic probe for the OSKAR cockpit «Aktuelle Gewichtung» scrape.

Usage::

    python scripts/oskar_probe.py
    python scripts/oskar_probe.py --expect IE000O5FBC47 --expect IE00BKS7L097

Logs into OSKAR exactly like :func:`scrape.oskar.fetch_oskar_etfs` (email via
``input()``, password hidden via ``getpass`` — credentials are never logged),
expands the allocation widget with the production merge logic, and writes
diagnostics for offline analysis:

* ``/tmp/opencode/oskar_probe.log``       full DEBUG log of the session
* ``/tmp/opencode/oskar_probe_rows.json`` merged raw rows (isin/category/...)
* ``/tmp/opencode/oskar_probe_widget.html`` the widget's complete HTML (final state)
* ``/tmp/opencode/oskar_probe_widget_<bucket>.html``
                                          widget HTML right after each bucket was
                                          expanded (that bucket's subtree still open)

Each bucket is expanded with the production logic and audited immediately
afterwards (taxonomy + expected-ISIN DOM hits), because the cockpit collapses
previously expanded buckets later on — the end state alone is not diagnostic.

The final stdout summary states, per expected ISIN, whether it was found in
the collected rows, present in the widget DOM but not collected, or absent
from the DOM entirely.  This is a diagnostic tool; it does not modify any
portfolio data.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scrape import oskar as osk  # noqa: E402

logger = logging.getLogger("scripts.oskar_probe")

DEFAULT_EXPECTED = ("IE000O5FBC47", "IE00BKS7L097")
_MAX_HTML_BYTES = 4_000_000

_WIDGET_AUDIT_JS = r"""
([isins]) => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const walkShadows = (node, visit) => {
        visit(node);
        node.querySelectorAll("*").forEach((el) => {
            try {
                if (el.shadowRoot) walkShadows(el.shadowRoot, visit);
            } catch (e) { /* closed shadow */ }
        });
    };
    const allocRoots = [];
    const seenAlloc = new Set();
    walkShadows(document, (root) => {
        if (!root || !root.querySelector) return;
        const aa = root.querySelector(".asset-allocation");
        if (aa && !seenAlloc.has(aa)) {
            seenAlloc.add(aa);
            allocRoots.push(aa);
        }
    });
    const taxonomy = [];
    const isinHits = [];
    let category = "";
    let subcategory = "";
    for (const root of allocRoots) {
        const rowEls = [...root.querySelectorAll("div.row")].filter((r) =>
            ["level1", "level2", "level3"].some((lv) => r.classList.contains(lv))
        );
        for (const r of rowEls) {
            const asset = r.querySelector(".asset");
            const label = norm(asset ? asset.textContent : "");
            const blob = norm(r.innerText || "");
            let level = "other";
            if (r.classList.contains("level1")) {
                level = "level1";
                category = label;
                subcategory = "";
            } else if (r.classList.contains("level2")) {
                level = "level2";
                subcategory = label;
            } else if (r.classList.contains("level3")) {
                level = "level3";
            }
            if (level !== "level3") {
                taxonomy.push({ level, label, category, subcategory });
            }
            for (const isin of isins) {
                if (blob.indexOf(isin) >= 0) {
                    isinHits.push({
                        isin,
                        level,
                        label,
                        category,
                        subcategory,
                        blob: blob.slice(0, 300),
                    });
                }
            }
        }
    }
    const body = (document.body && document.body.innerText) || "";
    const bodyFlags = {};
    for (const isin of isins) bodyFlags[isin] = body.indexOf(isin) >= 0;
    return { taxonomy, isinHits, bodyFlags, url: location.href };
}
"""

_WIDGET_HTML_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    const walkShadows = (node, visit) => {
        visit(node);
        node.querySelectorAll("*").forEach((el) => {
            try {
                if (el.shadowRoot) walkShadows(el.shadowRoot, visit);
            } catch (e) { /* closed shadow */ }
        });
    };
    walkShadows(document, (root) => {
        if (!root || !root.querySelector) return;
        const aa = root.querySelector(".asset-allocation");
        if (aa && !seen.has(aa)) {
            seen.add(aa);
            out.push(aa.outerHTML);
        }
    });
    return out;
}
"""


def _configure_logging(out_dir: Path) -> Path:
    log_path = out_dir / "oskar_probe.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("asyncio").setLevel(logging.INFO)
    osk_logger = logging.getLogger("scrape.oskar")
    osk_logger.handlers.clear()
    osk_logger.propagate = True
    return log_path


def _evaluate_per_frame(page: Any, js: str, arg: Any = "[]") -> list[Any]:
    results: list[Any] = []
    for fr in page.frames:
        try:
            chunk = fr.evaluate(js, arg)
        except Exception as exc:
            logger.debug("probe frame eval skipped url=%s err=%s", (getattr(fr, "url", "") or "")[:100], exc)
            continue
        results.append(chunk)
    return results


def _write_artifacts(
    out_dir: Path,
    raw_rows: list[dict[str, Any]],
    audit_chunks: list[Any],
    html_chunks: list[Any],
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    rows_path = out_dir / "oskar_probe_rows.json"
    rows_path.write_text(
        json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["rows"] = str(rows_path)

    html_path = out_dir / "oskar_probe_widget.html"
    html_parts: list[str] = []
    for chunk in html_chunks:
        if isinstance(chunk, list):
            html_parts.extend(str(part) for part in chunk)
    html = "\n\n<!-- ===== next .asset-allocation root ===== -->\n\n".join(html_parts)
    if len(html) > _MAX_HTML_BYTES:
        html = html[:_MAX_HTML_BYTES]
    html_path.write_text(html, encoding="utf-8")
    paths["widget_html"] = str(html_path)

    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live diagnostic probe for the OSKAR allocation scrape."
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="ISIN",
        help="ISIN to verify (repeatable; defaults to the two known-missing ones)",
    )
    parser.add_argument(
        "--out-dir",
        default="/tmp/opencode",
        help="Directory for log/JSON/HTML artifacts (default: /tmp/opencode)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120_000,
        help="Navigation/operation timeout in ms (default: 120000)",
    )
    parser.add_argument(
        "--dashboard-url",
        default=osk._DASHBOARD_URL,
        help="OSKAR dashboard URL to start from",
    )
    args = parser.parse_args(argv)
    expected = args.expect or list(DEFAULT_EXPECTED)

    out_dir = Path(args.out_dir)
    log_path = _configure_logging(out_dir)
    logger.info("probe: starting; artifacts dir=%s expected=%s", out_dir, expected)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.error(
            "playwright is required; install with pip and run: playwright install chromium"
        )
        return 1

    exit_code = 0
    with_sync = sync_playwright()
    with with_sync as p:
        browser: Any | None = None
        page: Any | None = None
        raw_rows: list[dict[str, Any]] = []
        audit_chunks: list[Any] = []
        html_chunks: list[Any] = []
        try:
            logger.info("probe: launching headless browser")
            browser, context, page = osk._open_oskar_page(
                p,
                dashboard_url=args.dashboard_url,
                timeout_ms=args.timeout_ms,
            )
            logger.info("probe: dashboard loaded url=%s", page.url)

            if not osk._on_cockpit_dashboard(page):
                logger.info("probe: login required (url=%s)", page.url)
                osk._perform_cli_login(
                    page, timeout_ms=max(args.timeout_ms, 300_000)
                )

            osk._try_dismiss_sourcepoint_cookie_banner(page, timeout_ms=20_000)
            logger.info("probe: clicking allocation tab")
            osk._click_allocation_tab(page, timeout_ms=args.timeout_ms)
            allocation_scope = osk._wait_for_allocation_scope(
                page, timeout_ms=args.timeout_ms
            )

            merge_state: dict[str, Any] = {"ordered": [], "idx_by_isin": {}}
            for top_label, sub_labels in osk._OSKAR_ALLOCATION_BUCKETS.items():
                logger.info("probe: expanding bucket %r", top_label)
                osk._expand_oskar_allocation_bucket(
                    page,
                    allocation_scope,
                    top_label,
                    sub_labels,
                    merge_state=merge_state,
                )
                audit_chunks = [
                    c
                    for c in _evaluate_per_frame(page, _WIDGET_AUDIT_JS, [expected])
                    if isinstance(c, dict)
                ]
                for chunk in audit_chunks:
                    logger.info(
                        "probe: audit after %r frame url=%s taxonomy=%s",
                        top_label,
                        chunk.get("url"),
                        json.dumps(chunk.get("taxonomy", []), ensure_ascii=False),
                    )
                    for hit in chunk.get("isinHits", []):
                        logger.info(
                            "probe: DOM hit after %r: isin=%s level=%s label=%r category=%r subcategory=%r blob=%r",
                            top_label,
                            hit.get("isin"),
                            hit.get("level"),
                            hit.get("label"),
                            hit.get("category"),
                            hit.get("subcategory"),
                            hit.get("blob", "")[:160],
                        )
                slug = re.sub(r"\W+", "_", top_label).strip("_").lower() or "bucket"
                bucket_html_parts: list[str] = []
                for chunk in _evaluate_per_frame(page, _WIDGET_HTML_JS):
                    if isinstance(chunk, list):
                        bucket_html_parts.extend(str(part) for part in chunk)
                bucket_html = (
                    "\n\n<!-- ===== next .asset-allocation root ===== -->\n\n".join(
                        bucket_html_parts
                    )
                )
                if bucket_html:
                    bucket_path = out_dir / f"oskar_probe_widget_{slug}.html"
                    try:
                        bucket_path.write_text(
                            bucket_html[:_MAX_HTML_BYTES], encoding="utf-8"
                        )
                        logger.info("probe: wrote %s", bucket_path)
                    except Exception as exc:
                        logger.warning("probe: failed to write %s: %s", bucket_path, exc)

            page.wait_for_timeout(1_800)
            osk._merge_row_snapshots_into(
                merge_state["ordered"],
                merge_state["idx_by_isin"],
                osk._collect_raw_rows_from_page(page),
            )
            raw_rows = merge_state["ordered"]
            logger.info("probe: collected %d raw row(s)", len(raw_rows))

            audit_chunks = _evaluate_per_frame(
                page, _WIDGET_AUDIT_JS, [expected]
            )
            for chunk in audit_chunks:
                if not isinstance(chunk, dict):
                    continue
                logger.info(
                    "probe: frame url=%s taxonomy=%s",
                    chunk.get("url"),
                    json.dumps(chunk.get("taxonomy", []), ensure_ascii=False),
                )
                for hit in chunk.get("isinHits", []):
                    logger.info(
                        "probe: DOM hit isin=%s level=%s label=%r category=%r subcategory=%r blob=%r",
                        hit.get("isin"),
                        hit.get("level"),
                        hit.get("label"),
                        hit.get("category"),
                        hit.get("subcategory"),
                        hit.get("blob", "")[:160],
                    )
                for isin, present in (chunk.get("bodyFlags") or {}).items():
                    logger.info(
                        "probe: ISIN %s present in page body text: %s",
                        isin,
                        bool(present),
                    )

            html_chunks = _evaluate_per_frame(page, _WIDGET_HTML_JS)

            collected = {str(r.get("isin", "")).strip() for r in raw_rows}
            for isin in expected:
                hits = []
                for chunk in audit_chunks:
                    if isinstance(chunk, dict):
                        hits.extend(h for h in chunk.get("isinHits", []) if h.get("isin") == isin)
                if isin in collected:
                    logger.info("probe: %s: FOUND in collected rows", isin)
                elif hits:
                    logger.info(
                        "probe: %s: in DOM but NOT collected (level=%s label=%r category=%r subcategory=%r)",
                        isin,
                        hits[0].get("level"),
                        hits[0].get("label"),
                        hits[0].get("category"),
                        hits[0].get("subcategory"),
                    )
                else:
                    logger.info("probe: %s: NOT in DOM at all", isin)
        except KeyboardInterrupt:
            logger.warning("probe: interrupted by user")
            exit_code = 130
        finally:
            try:
                _write_artifacts(out_dir, raw_rows, audit_chunks, html_chunks)
            except Exception as exc:
                logger.error("probe: failed to write artifacts: %s", exc)
            try:
                if page is not None:
                    osk._try_oskar_logout(page, timeout_ms=min(15_000, args.timeout_ms))
            except Exception as exc:
                logger.warning("probe: logout error before browser close: %s", exc)
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass

    logger.info("probe: done; artifacts in %s (log=%s)", out_dir, log_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
