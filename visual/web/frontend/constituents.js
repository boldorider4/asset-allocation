/* Constituents editing: POST changed cells to /api/constituents.
 *
 * A single delegated "change" listener covers Enter, Tab, and click-away.
 * Shares/value edits refresh both boxes of the pair from the response,
 * record the new baselines, and stage an update (Dashboard runs it,
 * otherwise navigates directly); green flash on both boxes, or red flare
 * on the edited box when no cached price allowed a recompute. Label edits
 * and row reorders persist silently and never stage an update. Row deletes
 * remove the row and stage an update. Any failure reverts to the
 * last-known-good value. Locked cells never fire this.
 *
 * Rows also reorder within their section via the grip handle (Pointer
 * Events, so mouse and touch both work). A real drop POSTs the new index
 * order to /api/constituents/order and rewrites every data-index in the
 * section; dropping back home sends nothing, and a failed POST restores
 * the original DOM order.
 *
 * New rows start as a draft behind each section's + button: broker
 * (dropdown, anytime before OK), name/group/ISIN/value editable from
 * the start, a JustETF ISIN check unlocking shares, and OK to persist
 * (server-rendered row swaps in). OK stays disabled until broker, name,
 * and value-or-ISIN-plus-shares are set. Discard via the draft trash
 * button or Escape.
 *
 * The incognito button blurs Value/Shares figures via a body.incognito
 * CSS class (price, names, and ISINs stay sharp). Instant toggle with
 * no reload: history.replaceState carries ?incognito=true, shared with
 * the dashboard round trip; blurred inputs stay editable.
 */
(function () {
  "use strict";

  // Staged-update flag: set only by successfully persisted shares/value
  // edits and row deletes. Dashboard runs the lite update when set,
  // otherwise it navigates straight to the dashboard.
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
      flash(input, "saved-flash");
      dirty = true;
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
    if (target instanceof HTMLSelectElement && target.matches("select.broker-select")) {
      onDraftBroker(target);
      return;
    }
    if (!(target instanceof HTMLInputElement) || !target.matches("input.cell-box.editable")) {
      return;
    }
    if (target.closest("tr.draft")) {
      onDraftFieldChange(target);
      return;
    }
    save(target);
  });

  /* Row deletion via the trash button. Removes the row on success and
   * rewrites every data-index in the section (rows AND their inputs, or
   * later cell edits would hit the wrong rows); stages an update so
   * Dashboard recomputes the charts. The row stays put on failure.
   */
  async function deleteRow(button) {
    const row = button.closest("tr");
    const tbody = row && row.parentElement;
    if (!row || !tbody || button.dataset.busy === "1") {
      return;
    }
    button.dataset.busy = "1";
    button.setAttribute("aria-disabled", "true");
    let data = null;
    try {
      const response = await fetch("/api/constituents/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bucket: button.dataset.bucket,
          index: Number(button.dataset.index),
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      data = await response.json();
    } catch (err) {
      setStatus("Delete failed: " + (err && err.message ? err.message : err));
      button.dataset.busy = "";
      button.removeAttribute("aria-disabled");
      return;
    }
    if (!data || data.deleted !== true) {
      setStatus("Delete failed: server did not confirm the deletion");
      button.dataset.busy = "";
      button.removeAttribute("aria-disabled");
      return;
    }
    row.remove();
    Array.prototype.forEach.call(tbody.children, function (sibling, position) {
      sibling.dataset.index = String(position);
      sibling.querySelectorAll("[data-index]").forEach(function (el) {
        el.dataset.index = String(position);
      });
    });
    dirty = true;
  }

  document.addEventListener("click", function (event) {
    const target = event.target;
    if (target instanceof Element) {
      const add = target.closest("button.add");
      if (add) {
        openDraft(add);
        return;
      }
      const discard = target.closest("button.draft-discard");
      if (discard) {
        const draft = discard.closest("tr.draft");
        if (draft) {
          draft.remove();
        }
        return;
      }
      const ok = target.closest("button.ok");
      if (ok) {
        confirmDraft(ok);
        return;
      }
      const button = target.closest("button.trash");
      if (button) {
        deleteRow(button);
      }
    }
  });

  /* Row creation via the + button. One draft per section: a draft <tr>
   * with OK (far left), text/figure inputs editable from the start,
   * locked shares/price dashes, a broker <select>, and a discard button.
   * Choosing a broker swaps the select for its icon; a successful
   * JustETF check on the ISIN unlocks shares. OK validates and POSTs the
   * row, swapping in the server-rendered <tr>; failures and discards
   * keep or drop the draft locally.
   */
  var brokerCatalog = null;

  async function loadBrokers() {
    if (brokerCatalog) {
      return brokerCatalog;
    }
    const response = await fetch("/api/constituents/brokers", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    brokerCatalog = await response.json();
    return brokerCatalog;
  }

  function draftInput(cls, maxlength) {
    const input = document.createElement("input");
    input.className = "cell-box editable " + cls;
    input.setAttribute("maxlength", String(maxlength));
    return input;
  }

  function draftDash() {
    const dash = document.createElement("span");
    dash.className = "cell-box locked";
    dash.textContent = "-";
    return dash;
  }

  function unitSpan() {
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = "Euro";
    return unit;
  }

  function draftCell(child) {
    const cell = document.createElement("td");
    if (child) {
      cell.appendChild(child);
    }
    return cell;
  }

  function focusDraft(row) {
    const first = row.querySelector("input:not([disabled]), select");
    if (first) {
      first.focus();
    }
  }

  /* OK is clickable only for a persistable draft: broker chosen, name
   * given, and either a value or an ISIN-plus-shares pair. Backend
   * validation in confirmDraft stays as the backstop.
   */
  function refreshOkState(row) {
    const ok = row.querySelector("button.ok");
    if (!ok || ok.dataset.busy === "1") {
      return;
    }
    const ready =
      (row.dataset.broker || "") !== "" &&
      draftText(row, "draft-name").trim() !== "" &&
      (draftText(row, "draft-value").trim() !== "" ||
        (draftText(row, "draft-isin").trim() !== "" &&
          draftText(row, "draft-shares").trim() !== ""));
    if (ready) {
      ok.removeAttribute("disabled");
    } else {
      ok.setAttribute("disabled", "");
    }
  }

  async function openDraft(addButton) {
    const section = addButton.closest("section");
    const tbody = section && section.querySelector("tbody");
    const bucket = addButton.dataset.bucket;
    if (!tbody || !bucket) {
      return;
    }
    const existing = tbody.querySelector("tr.draft");
    if (existing) {
      focusDraft(existing);
      return;
    }
    let catalog;
    try {
      catalog = await loadBrokers();
    } catch (err) {
      setStatus("Brokers unavailable: " + (err && err.message ? err.message : err));
      return;
    }
    const entries = catalog && catalog.brokers;
    if (!Array.isArray(entries) || entries.length === 0) {
      setStatus("Brokers unavailable: empty broker list");
      return;
    }
    const row = document.createElement("tr");
    row.className = "draft";
    row.dataset.bucket = bucket;

    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "ok";
    ok.textContent = "OK";
    ok.title = "Add this row";
    ok.setAttribute("aria-label", "Add this row");
    row.appendChild(draftCell(ok));

    row.appendChild(draftCell(draftInput("draft-name name", 64)));
    row.appendChild(draftCell(draftInput("draft-group text", 32)));
    row.appendChild(draftCell(draftInput("draft-isin", 12)));
    const valueCell = draftCell(draftInput("draft-value", 16));
    valueCell.appendChild(unitSpan());
    row.appendChild(valueCell);

    const sharesCell = draftCell(draftDash());
    sharesCell.className = "draft-shares-cell";
    row.appendChild(sharesCell);
    const priceCell = draftCell(draftDash());
    priceCell.appendChild(unitSpan());
    row.appendChild(priceCell);

    const select = document.createElement("select");
    select.className = "broker-select";
    select.setAttribute("aria-label", "Broker");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Broker…";
    select.appendChild(placeholder);
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.label || entry.id;
      select.appendChild(option);
    }
    row.appendChild(draftCell(select));

    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "draft-discard";
    discard.textContent = "✕";
    discard.title = "Discard this row";
    discard.setAttribute("aria-label", "Discard this row");
    const trashCell = draftCell(discard);
    trashCell.className = "trash-cell";
    row.appendChild(trashCell);

    tbody.appendChild(row);
    refreshOkState(row);
    focusDraft(row);
  }

  function onDraftBroker(select) {
    const row = select.closest("tr");
    if (!row || !select.value || !brokerCatalog) {
      return;
    }
    const entry = (brokerCatalog.brokers || []).find(function (item) {
      return item.id === select.value;
    });
    if (!entry || !entry.mark) {
      return;
    }
    // Don't swap to icon yet - keep dropdown until OK is clicked
    row.dataset.broker = select.value;
    refreshOkState(row);
    focusDraft(row);
  }

  function lockDraftShares(row) {
    const cell = row.querySelector(".draft-shares-cell");
    if (cell) {
      cell.innerHTML = "";
      cell.appendChild(draftDash());
    }
    row.dataset.isinOk = "";
  }

  function onDraftFieldChange(input) {
    const row = input.closest("tr");
    if (!row || !input.matches(".draft-isin")) {
      return;
    }
    checkDraftIsin(row);
  }

  async function checkDraftIsin(row) {
    const isinInput = row.querySelector(".draft-isin");
    if (!isinInput || isinInput.dataset.busy === "1") {
      return;
    }
    const isin = isinInput.value.trim();
    lockDraftShares(row);
    if (!isin) {
      refreshOkState(row);
      return;
    }
    isinInput.dataset.busy = "1";
    let exists = false;
    try {
      const response = await fetch("/api/constituents/check-isin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isin: isin }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      exists = !!(await response.json()).exists;
    } catch (err) {
      setStatus("ISIN check failed: " + (err && err.message ? err.message : err));
      isinInput.dataset.busy = "";
      flash(isinInput, "stale-flash");
      refreshOkState(row);
      return;
    }
    isinInput.dataset.busy = "";
    if (!exists) {
      flash(isinInput, "stale-flash");
      refreshOkState(row);
      return;
    }
    row.dataset.isinOk = "1";
    const cell = row.querySelector(".draft-shares-cell");
    if (cell && !cell.querySelector("input")) {
      cell.innerHTML = "";
      cell.appendChild(draftInput("draft-shares", 16));
    }
    flash(isinInput, "saved-flash");
    refreshOkState(row);
  }

  function draftText(row, cls) {
    const input = row.querySelector("." + cls);
    return input ? input.value : "";
  }

  function parseDraftFigure(raw) {
    const text = (raw || "").trim();
    if (!text) {
      return { ok: true, value: null };
    }
    const number = Number(text);
    if (!Number.isFinite(number)) {
      return { ok: false, value: null };
    }
    return { ok: true, value: text };
  }

  async function confirmDraft(okButton) {
    const row = okButton.closest("tr");
    const tbody = row && row.parentElement;
    if (!row || !tbody || okButton.dataset.busy === "1") {
      return;
    }
    const bucket = row.dataset.bucket || "";
    const broker = row.dataset.broker || "";
    if (!broker) {
      setStatus("Choose a broker first");
      const select = row.querySelector("select.broker-select");
      if (select) {
        select.focus();
      }
      return;
    }
    const name = draftText(row, "draft-name");
    if (!name.trim()) {
      setStatus("Name must not be empty");
      const input = row.querySelector(".draft-name");
      if (input) {
        input.focus();
      }
      return;
    }
    const value = parseDraftFigure(draftText(row, "draft-value"));
    if (!value.ok) {
      setStatus("Value must be a number");
      return;
    }
    const shares = parseDraftFigure(draftText(row, "draft-shares"));
    if (!shares.ok) {
      setStatus("Shares must be a number");
      return;
    }
    okButton.dataset.busy = "1";
    let data = null;
    try {
      const response = await fetch("/api/constituents/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bucket: bucket,
          name: name,
          short_name: draftText(row, "draft-group"),
          isin: draftText(row, "draft-isin") || null,
          value: value.value,
          shares: shares.value,
          broker: broker,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      data = await response.json();
    } catch (err) {
      setStatus("Add failed: " + (err && err.message ? err.message : err));
      okButton.dataset.busy = "";
      return;
    }
    const wrapper = document.createElement("tbody");
    wrapper.innerHTML = data && data.row ? String(data.row) : "";
    const real = wrapper.firstElementChild;
    if (!real || real.tagName !== "TR") {
      setStatus("Add failed: server returned no row");
      okButton.dataset.busy = "";
      return;
    }
    row.replaceWith(real);
    dirty = true;
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !drag) {
      const active = document.activeElement;
      if (active instanceof Element) {
        const draft = active.closest("tr.draft");
        if (draft) {
          draft.remove();
        }
      }
    }
  });

  document.addEventListener("input", function (event) {
    const target = event.target;
    if (target instanceof HTMLInputElement) {
      const row = target.closest("tr.draft");
      if (row) {
        refreshOkState(row);
      }
    }
  });

  /* Row reordering via the grip handle. Live-moves the <tr> inside its
   * own <tbody> (cross-section drops are impossible by construction) and
   * persists the permutation on drop. data-index attributes still hold
   * the pre-drag positions until the POST succeeds, so a revert is just
   * a re-sort and a success is a sequential rewrite (rows AND their
   * inputs, or later cell edits would hit the wrong rows).
   */
  var DRAG_THRESHOLD_PX = 6;
  var EDGE_SCROLL_ZONE_PX = 48;
  var EDGE_SCROLL_STEP_PX = 14;
  var EDGE_SCROLL_MS = 50;

  var drag = null;

  function stopEdgeScroll() {
    if (drag && drag.scrollTimer) {
      window.clearInterval(drag.scrollTimer);
      drag.scrollTimer = 0;
    }
  }

  function moveRowToPointer(y) {
    const rows = Array.prototype.filter.call(
      drag.tbody.children,
      function (row) {
        return row !== drag.row;
      }
    );
    let before = null;
    for (const row of rows) {
      const rect = row.getBoundingClientRect();
      if (y < rect.top + rect.height / 2) {
        before = row;
        break;
      }
    }
    if (before) {
      drag.tbody.insertBefore(drag.row, before);
    } else {
      drag.tbody.appendChild(drag.row);
    }
  }

  function updateEdgeScroll(y) {
    stopEdgeScroll();
    drag.lastY = y;
    let delta = 0;
    if (y < EDGE_SCROLL_ZONE_PX) {
      delta = -EDGE_SCROLL_STEP_PX;
    } else if (y > window.innerHeight - EDGE_SCROLL_ZONE_PX) {
      delta = EDGE_SCROLL_STEP_PX;
    }
    if (delta) {
      drag.scrollTimer = window.setInterval(function () {
        window.scrollBy(0, delta);
        moveRowToPointer(drag.lastY);
      }, EDGE_SCROLL_MS);
    }
  }

  function endDragListeners() {
    document.removeEventListener("pointermove", onDragMove);
    document.removeEventListener("pointerup", onDragEnd);
    document.removeEventListener("pointercancel", onDragCancel);
    document.removeEventListener("keydown", onDragKey, true);
  }

  function revertDragOrder(tbody) {
    const rows = Array.prototype.slice.call(tbody.children);
    rows
      .sort(function (a, b) {
        return Number(a.dataset.index) - Number(b.dataset.index);
      })
      .forEach(function (row) {
        tbody.appendChild(row);
      });
  }

  function dropDrag() {
    endDragListeners();
    stopEdgeScroll();
    drag.row.classList.remove("dragging");
    document.body.classList.remove("rows-dragging");
    const finished = drag;
    drag = null;
    if (!finished.started) {
      return;
    }
    persistOrder(finished.tbody);
  }

  function onDragMove(event) {
    if (!drag || !event.isPrimary) {
      return;
    }
    if (!drag.started) {
      if (Math.abs(event.clientY - drag.startY) < DRAG_THRESHOLD_PX) {
        return;
      }
      drag.started = true;
      drag.row.classList.add("dragging");
      document.body.classList.add("rows-dragging");
    }
    moveRowToPointer(event.clientY);
    updateEdgeScroll(event.clientY);
  }

  function onDragEnd() {
    dropDrag();
  }

  function onDragCancel() {
    endDragListeners();
    stopEdgeScroll();
    if (drag) {
      revertDragOrder(drag.tbody);
      drag.row.classList.remove("dragging");
      document.body.classList.remove("rows-dragging");
      drag = null;
    }
  }

  function onDragKey(event) {
    if (drag && drag.started && event.key === "Escape") {
      event.preventDefault();
      onDragCancel();
    }
  }

  async function persistOrder(tbody) {
    const rows = Array.prototype.slice.call(tbody.children);
    const order = rows.map(function (row) {
      return Number(row.dataset.index);
    });
    const bucket = rows.length ? rows[0].dataset.bucket : "";
    const sane =
      rows.length > 0 &&
      rows.every(function (row) {
        return row.dataset.bucket === bucket;
      }) &&
      order.every(function (i) {
        return Number.isInteger(i);
      });
    if (!sane) {
      return;
    }
    const home = order.every(function (value, position) {
      return value === position;
    });
    if (home) {
      return;
    }
    let data = null;
    try {
      const response = await fetch("/api/constituents/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bucket: bucket, order: order }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      data = await response.json();
    } catch (err) {
      revertDragOrder(tbody);
      setStatus("Reorder failed: " + (err && err.message ? err.message : err));
      return;
    }
    if (!data || String((data.order || []).join(",")) !== String(order.join(","))) {
      revertDragOrder(tbody);
      setStatus("Reorder failed: server echoed a different order");
      return;
    }
    rows.forEach(function (row, position) {
      row.dataset.index = String(position);
      row.querySelectorAll("input[data-index]").forEach(function (input) {
        input.dataset.index = String(position);
        input.dataset.original = input.value;
      });
    });
  }

  document.addEventListener(
    "pointerdown",
    function (event) {
      if (drag || !event.isPrimary) {
        return;
      }
      if (event.button !== undefined && event.button !== 0) {
        return;
      }
      const grip = event.target instanceof Element && event.target.closest(".grip");
      if (!grip) {
        return;
      }
      const row = grip.closest("tr");
      const tbody = row && row.parentElement;
      if (!row || !tbody) {
        return;
      }
      event.preventDefault();
      try {
        grip.setPointerCapture(event.pointerId);
      } catch {
        // Older browsers may lack it; document-level listeners still track.
      }
      drag = {
        row: row,
        tbody: tbody,
        grip: grip,
        startY: event.clientY,
        lastY: event.clientY,
        started: false,
        scrollTimer: 0,
      };
      document.addEventListener("pointermove", onDragMove, { passive: false });
      document.addEventListener("pointerup", onDragEnd);
      document.addEventListener("pointercancel", onDragCancel);
      document.addEventListener("keydown", onDragKey, true);
    },
    { passive: false }
  );

  function setStatus(text) {
    const status = document.getElementById("update-status");
    if (!status) {
      return;
    }
    status.textContent = text;
    status.hidden = !text;
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
        toggle.setAttribute("href", "/constituents");
        toggle.setAttribute("title", "Exit incognito mode");
        toggle.setAttribute("aria-label", "Exit incognito mode");
      } else {
        toggle.removeAttribute("aria-pressed");
        toggle.setAttribute("href", "/constituents?incognito=true");
        toggle.setAttribute("title", "Incognito mode");
        toggle.setAttribute("aria-label", "Incognito mode");
      }
    }
    const overview = document.getElementById("overview-link");
    if (overview) {
      overview.setAttribute(
        "href",
        active ? "/dashboard?incognito=true" : "/dashboard"
      );
    }
    // Keep the idle-prefetched dashboard URL on the current state.
    const prefetch = document.getElementById("prefetch-dashboard");
    if (prefetch) {
      prefetch.setAttribute(
        "href",
        active ? "/dashboard?incognito=true" : "/dashboard"
      );
    }
    if (syncUrl) {
      try {
        // The query is backend-tracked incognito state; the view lives
        // in the hash and must survive the rewrite (single document).
        // Pathname stays wherever this document was served from
        // (/dashboard in-app, /constituents on direct loads).
        const path = window.location.pathname;
        const url =
          path + (active ? "?incognito=true" : "") + window.location.hash;
        window.history.replaceState(null, "", url);
      } catch {
        // Non-pushState contexts: visuals already applied.
      }
    }
  }

  // Identity-based bind guard: dataset flags are out of the question
  // because the view cache stores HTML strings — attributes (including
  // any marker) survive the round trip, so restored nodes would arrive
  // pre-marked and never get bound. A WeakSet lives outside the DOM.
  var wiredNodes = new WeakSet();

  function markWired(el) {
    if (!el || wiredNodes.has(el)) {
      return false;
    }
    wiredNodes.add(el);
    return true;
  }

  function wireIncognitoToggle() {
    // Paint the state from the URL (deep links, round trip, fresh nodes
    // after a view swap); the URL already carries it, so don't rewrite.
    applyIncognitoState(isIncognitoMode(), false);
    const toggle = document.getElementById("incognito-link");
    if (!markWired(toggle)) {
      return;
    }
    // Instant toggle: no reload — pure CSS blur flip.
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      applyIncognitoState(!document.body.classList.contains("incognito"));
    });
  }

  // Warm the dashboard document while idle so the return trip lands
  // on a warm HTTP cache. The gallery itself paints from its snapshot
  // on arrival, so only the shell document is prefetched here.
  var dashboardPrefetchScheduled = false;

  function scheduleDashboardPrefetch() {
    if (dashboardPrefetchScheduled) {
      syncDashboardPrefetch();
      return;
    }
    dashboardPrefetchScheduled = true;
    const run = function () {
      syncDashboardPrefetch();
    };
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(run);
    } else {
      window.setTimeout(run, 1500);
    }
  }

  function syncDashboardPrefetch() {
    let link = document.getElementById("prefetch-dashboard");
    if (!link) {
      link = document.createElement("link");
      link.id = "prefetch-dashboard";
      link.rel = "prefetch";
      document.head.appendChild(link);
    }
    link.href = document.body.classList.contains("incognito")
      ? "/dashboard?incognito=true"
      : "/dashboard";
  }

  function setOverlay(visible) {
    const overlay = document.getElementById("update-overlay");
    if (overlay) {
      overlay.hidden = !visible;
    }
  }

  function switchViewOrNavigate(target) {
    try {
      if (
        typeof window.__switchView === "function" &&
        window.__switchView(target)
      ) {
        return;
      }
    } catch {
      // Fall through to classic navigation below.
    }
    window.location.href = target;
  }

  async function refreshAndGo(event) {
    event.preventDefault();
    const link = event.currentTarget;
    if (link.dataset.busy === "1") {
      return;
    }
    // The server renders the Dashboard href with the gallery mode baked
    // in (?incognito=true when active); navigate via the link so the
    // round trip through Constituents holds the state.
    const target = link.getAttribute("href") || "/dashboard";
    if (!dirty) {
      switchViewOrNavigate(target);
      return;
    }
    link.dataset.busy = "1";
    setOverlay(true);
    try {
      const response = await fetch("/api/update", { method: "POST" });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      dirty = false;
      // Restore the pristine state before the view is stashed: the
      // switcher caches live HTML, so a still-visible overlay (or a
      // stuck busy flag) would linger after the round trip.
      link.dataset.busy = "";
      setOverlay(false);
      switchViewOrNavigate(target);
    } catch (err) {
      setOverlay(false);
      setStatus("Update failed: " + (err && err.message ? err.message : err));
      link.dataset.busy = "";
    }
  }

  // Idempotent boot: this script loads on both pages (the view switcher
  // swaps bodies in place), but only the constituents page has
  // #overview-link. Document-level delegation above is bound once per
  // document lifetime and survives swaps; per-node bindings below happen
  // exactly once via wiredNodes.
  function initConstituents() {
    if (!document.getElementById("overview-link")) {
      return;
    }
    // Invariant: the overlay is never visible on show. No update can be
    // in flight here (refreshAndGo swaps only after its POST settles),
    // so a visible overlay means a stale stash — clear it.
    setOverlay(false);
    wireIncognitoToggle();
    scheduleDashboardPrefetch();
    const overview = document.getElementById("overview-link");
    if (markWired(overview)) {
      overview.addEventListener("click", refreshAndGo);
    }
  }

  window.__constituentsInit = initConstituents;

  // Background revalidation hook for the view switcher: refetch this
  // page's HTML and hand it over; the switcher swaps it in only when the
  // page is clean (no draft open, nothing focused).
  window.__constituentsRevalidate = function (url) {
    return fetch(url).then(function (response) {
      if (!response.ok) {
        throw new Error("revalidate failed: " + response.status);
      }
      return response.text();
    });
  };

  document.addEventListener("DOMContentLoaded", initConstituents);
})();
