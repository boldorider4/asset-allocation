/* Dashboard actions: Sync Prices runs a fat update, then reloads.
 *
 * The fat update scrapes prices, geosplits, and sectorsplits, so it takes
 * minutes. The button is disabled while a run is in flight; broker scrapes
 * never run here (no terminal in the server process). Leaving via Edit
 * cancels a running endpoint update first.
 *
 * The button stays pressed for the whole run: set directly while this
 * page's own POST is in flight, and restored from GET /api/update on load
 * so refreshes mid-run keep showing it. A run observed finishing reloads
 * the page on success or reports the failure.
 *
 * The incognito button blurs Euro figures via a body.incognito CSS
 * class (app.js wraps figures in span.euro; labels and percentages stay
 * sharp). The toggle is instant with no reload: history.replaceState
 * carries ?incognito=true as the state carrier (bookmarkable, shared
 * with Constituents), the pressed look follows aria-pressed, and the
 * Edit link href is synced so the round trip holds the state. Chart
 * data never changes.
 */
(function () {
  "use strict";

  var SYNC_POLL_MS = 2000;
  var SYNC_STATUS_RETRIES = 5;

  // True once this page itself started a run: the POST completion handler
  // then owns the outcome, so the poller stays out of its way.
  var startedHere = false;
  // True while any run is believed in flight (own or observed elsewhere).
  var syncActive = false;

  function setSyncPressed(link, pressed) {
    syncActive = pressed;
    if (pressed) {
      link.setAttribute("aria-disabled", "true");
    } else {
      link.removeAttribute("aria-disabled");
    }
  }

  function setStatus(text) {
    const status = document.getElementById("status");
    if (!status) {
      return;
    }
    status.textContent = text;
    status.hidden = !text;
  }

  async function syncPrices(event) {
    event.preventDefault();
    const link = event.currentTarget;
    if (link.dataset.busy === "1" || syncActive) {
      return;
    }
    link.dataset.busy = "1";
    startedHere = true;
    setSyncPressed(link, true);
    setStatus("Syncing prices…");
    try {
      const response = await fetch("/api/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "fat" }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      window.location.reload();
    } catch (err) {
      setStatus("Sync failed: " + (err && err.message ? err.message : err));
      link.dataset.busy = "";
      startedHere = false;
      setSyncPressed(link, false);
    }
  }

  function isIncognitoMode() {
    try {
      return (
        new URLSearchParams(window.location.search).get("incognito") === "true"
      );
    } catch {
      return false;
    }
  }

  function applyIncognitoState(active, syncUrl) {
    if (syncUrl === undefined) {
      syncUrl = true;
    }
    document.body.classList.toggle("incognito", active);
    const toggle = document.getElementById("incognito-link");
    if (toggle) {
      if (active) {
        toggle.setAttribute("aria-pressed", "true");
        toggle.setAttribute("href", "/dashboard");
        toggle.setAttribute("title", "Exit incognito mode");
        toggle.setAttribute("aria-label", "Exit incognito mode");
      } else {
        toggle.removeAttribute("aria-pressed");
        toggle.setAttribute("href", "/dashboard?incognito=true");
        toggle.setAttribute("title", "Incognito mode");
        toggle.setAttribute("aria-label", "Incognito mode");
      }
    }
    const edit = document.getElementById("edit-link");
    if (edit) {
      edit.setAttribute(
        "href",
        active ? "/constituents?incognito=true" : "/constituents"
      );
    }
    // Keep the idle-prefetched constituents URL on the current state.
    const prefetch = document.getElementById("prefetch-constituents");
    if (prefetch) {
      prefetch.setAttribute(
        "href",
        active ? "/constituents?incognito=true" : "/constituents"
      );
    }
    if (syncUrl) {
      try {
        const url = active ? "/dashboard?incognito=true" : "/dashboard";
        window.history.replaceState(null, "", url);
      } catch {
        // Non-pushState contexts (tests, file://): visuals already applied.
      }
    }
  }

  function wireIncognitoToggle() {
    const toggle = document.getElementById("incognito-link");
    // Paint the initial state from the URL (deep links, round trip);
    // the URL already carries the state, so don't rewrite it.
    applyIncognitoState(isIncognitoMode(), false);
    if (!toggle) {
      return;
    }
    // Instant toggle: no reload, no refetch — pure CSS blur flip.
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      applyIncognitoState(!document.body.classList.contains("incognito"));
    });
  }

  async function fetchUpdateStatus() {
    const response = await fetch("/api/update", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  }

  function pollUpdateStatus(link, observedBusy, failures) {
    fetchUpdateStatus().then(
      function (state) {
        if (state && state.updating) {
          setSyncPressed(link, true);
          window.setTimeout(function () {
            pollUpdateStatus(link, true, 0);
          }, SYNC_POLL_MS);
          return;
        }
        setSyncPressed(link, false);
        if (observedBusy && !startedHere) {
          if (state && state.last_ok) {
            window.location.reload();
          } else if (state && state.last_error) {
            setStatus("Sync failed: " + state.last_error);
          }
        }
      },
      function () {
        if (failures + 1 >= SYNC_STATUS_RETRIES) {
          setSyncPressed(link, false);
          setStatus("Sync status unavailable.");
          return;
        }
        window.setTimeout(function () {
          pollUpdateStatus(link, observedBusy, failures + 1);
        }, SYNC_POLL_MS);
      }
    );
  }

  async function cancelAndEdit(event) {
    // Fire-and-forget: stop any endpoint-triggered update, then leave
    // immediately without awaiting the cancel — keepalive delivers it
    // even mid-navigation. Navigation happens regardless so Edit always
    // works, even if the server is unreachable. Timer/systemd runs live
    // in other processes and are never affected. The link href carries
    // the gallery mode (?incognito=true when active), so navigate via
    // it to hold state. The constituents document was prefetched while
    // idle, so this lands on a warm HTTP cache.
    event.preventDefault();
    const target =
      event.currentTarget.getAttribute("href") || "/constituents";
    try {
      window
        .fetch("/api/cancel", { method: "POST", keepalive: true })
        .then(null, function () {
          // Ignore: the constituents page is useful with or without a cancel.
        });
    } catch {
      // Ignore: synchronous failures (e.g. no fetch) navigate anyway.
    }
    window.location.href = target;
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireIncognitoToggle();
    const sync = document.getElementById("sync-link");
    if (sync) {
      sync.addEventListener("click", syncPrices);
      pollUpdateStatus(sync, false, 0);
    }
    const edit = document.getElementById("edit-link");
    if (edit) {
      edit.addEventListener("click", cancelAndEdit);
    }
  });
})();
