/* Constituents editing: POST changed cells to /api/constituents.
 *
 * A single delegated "change" listener covers Enter, Tab, and click-away.
 * Success flashes the cell, records the typed text as the new baseline,
 * and stages an update (Overview runs it, otherwise navigates directly);
 * any failure reverts to the last-known-good value. Locked cells never
 * fire this.
 */
(function () {
  "use strict";

  // Staged-update flag: set on every successfully persisted cell edit.
  // Overview only runs the lite update when this is set; otherwise it
  // navigates straight to the dashboard.
  let dirty = false;

  function flash(input) {
    input.classList.remove("saved-flash");
    // Force reflow so repeated saves re-trigger the transition.
    void input.offsetWidth;
    input.classList.add("saved-flash");
    window.setTimeout(function () {
      input.classList.remove("saved-flash");
    }, 900);
  }

  function revert(input) {
    input.value = input.dataset.original || "";
  }

  async function save(input) {
    const payload = {
      bucket: input.dataset.bucket,
      index: Number(input.dataset.index),
      field: input.dataset.field,
      value: input.value,
    };
    let ok = false;
    try {
      const response = await fetch("/api/constituents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      ok = response.ok;
    } catch {
      ok = false;
    }
    if (!ok) {
      revert(input);
      return;
    }
    input.dataset.original = input.value;
    dirty = true;
    flash(input);
  }

  document.addEventListener("change", function (event) {
    const target = event.target;
    if (target instanceof HTMLInputElement && target.matches("input.cell-box.editable")) {
      save(target);
    }
  });

  function setStatus(text) {
    const status = document.getElementById("update-status");
    if (!status) {
      return;
    }
    status.textContent = text;
    status.hidden = !text;
  }

  function setOverlay(visible) {
    const overlay = document.getElementById("update-overlay");
    if (overlay) {
      overlay.hidden = !visible;
    }
  }

  async function refreshAndGo(event) {
    event.preventDefault();
    const link = event.currentTarget;
    if (link.dataset.busy === "1") {
      return;
    }
    if (!dirty) {
      window.location.href = "/dashboard";
      return;
    }
    link.dataset.busy = "1";
    setOverlay(true);
    try {
      const response = await fetch("/api/update", { method: "POST" });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      window.location.href = "/dashboard";
    } catch (err) {
      setOverlay(false);
      setStatus("Update failed: " + (err && err.message ? err.message : err));
      link.dataset.busy = "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const overview = document.getElementById("overview-link");
    if (overview) {
      overview.addEventListener("click", refreshAndGo);
    }
  });
})();
