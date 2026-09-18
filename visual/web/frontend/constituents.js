/* Constituents editing: POST changed cells to /api/constituents.
 *
 * A single delegated "change" listener covers Enter, Tab, and click-away.
 * Shares/value edits refresh both boxes of the pair from the response,
 * record the new baselines, and stage an update (Dashboard runs it,
 * otherwise navigates directly); green flash on both boxes, or red flare
 * on the edited box when no cached price allowed a recompute. Label edits
 * store text as-is and flash the edited box. Any failure reverts to the
 * last-known-good value. Locked cells never fire this.
 */
(function () {
  "use strict";

  // Staged-update flag: set on every successfully persisted cell edit.
  // Dashboard only runs the lite update when this is set; otherwise it
  // navigates straight to the dashboard.
  let dirty = false;

  function flash(input, className) {
    input.classList.remove("saved-flash", "stale-flash");
    // Force reflow so repeated saves re-trigger the transition.
    void input.offsetWidth;
    input.classList.add(className);
    window.setTimeout(function () {
      input.classList.remove(className);
    }, 900);
  }

  function revert(input) {
    input.value = input.dataset.original || "";
  }

  function formatFigure(value) {
    if (value === null || value === undefined) {
      return "";
    }
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(2) : "";
  }

  function refreshPair(input, data) {
    const updated = [];
    const row = input.closest("tr");
    const fields = { shares: null, value: null };
    if (row) {
      fields.shares = row.querySelector('input[data-field="shares"]');
      fields.value = row.querySelector('input[data-field="value"]');
    }
    for (const name of ["shares", "value"]) {
      const box = fields[name];
      if (box && data[name] !== undefined) {
        box.value = formatFigure(data[name]);
        box.dataset.original = box.value;
        updated.push(box);
      }
    }
    return updated;
  }

  async function save(input) {
    const payload = {
      bucket: input.dataset.bucket,
      index: Number(input.dataset.index),
      field: input.dataset.field,
      value: input.value,
    };
    let data = null;
    try {
      const response = await fetch("/api/constituents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      data = await response.json();
    } catch {
      revert(input);
      return;
    }
    if (input.dataset.field === "short_name") {
      input.value = data && data.short_name !== undefined ? String(data.short_name) : input.value;
      input.dataset.original = input.value;
      dirty = true;
      flash(input, "saved-flash");
      return;
    }
    const updated = refreshPair(input, data);
    input.dataset.original = input.value;
    dirty = true;
    if (data && data.recomputed === false) {
      flash(input, "stale-flash");
    } else {
      for (const box of updated) {
        flash(box, "saved-flash");
      }
    }
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
