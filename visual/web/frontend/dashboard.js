/* Dashboard actions: Sync Prices runs a fat update, then reloads.
 *
 * The fat update scrapes prices, geosplits, and sectorsplits, so it takes
 * minutes. The button is disabled while a run is in flight; broker scrapes
 * never run here (no terminal in the server process). Leaving via Edit
 * cancels a running endpoint update first.
 */
(function () {
  "use strict";

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
    if (link.dataset.busy === "1") {
      return;
    }
    link.dataset.busy = "1";
    link.setAttribute("aria-disabled", "true");
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
      link.removeAttribute("aria-disabled");
    }
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
    }
    const edit = document.getElementById("edit-link");
    if (edit) {
      edit.addEventListener("click", cancelAndEdit);
    }
  });
})();
