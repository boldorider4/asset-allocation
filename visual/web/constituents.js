/* Constituents editing: POST changed cells to /api/constituents.
 *
 * A single delegated "change" listener covers Enter, Tab, and click-away.
 * Success flashes the cell and records the typed text as the new baseline;
 * any failure reverts to the last-known-good value. Locked cells never
 * fire this.
 */
(function () {
  "use strict";

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

  async function refreshAndGo(event) {
    event.preventDefault();
    const link = event.currentTarget;
    if (link.dataset.busy === "1") {
      return;
    }
    link.dataset.busy = "1";
    setStatus("Updating charts…");
    try {
      const response = await fetch("/api/update", { method: "POST" });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      window.location.href = "/dashboard";
    } catch (err) {
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
