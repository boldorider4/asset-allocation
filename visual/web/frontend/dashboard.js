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
    // Fire-and-forget: stop any endpoint-triggered update, then leave.
    // Navigation happens regardless so Edit always works, even if the
    // server is unreachable. Timer/systemd runs live in other processes
    // and are never affected.
    event.preventDefault();
    try {
      await fetch("/api/cancel", { method: "POST" });
    } catch {
      // Ignore: the constituents page is useful with or without a cancel.
    }
    window.location.href = "/constituents";
  }

  document.addEventListener("DOMContentLoaded", function () {
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
