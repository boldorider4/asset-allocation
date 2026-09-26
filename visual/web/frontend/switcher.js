/* Single-document view routing for dashboard + constituents.
 *
 * The app lives at /dashboard: the view is frontend state kept in the
 * hash (#constituents, or absent for the dashboard), exactly the
 * "<server>/dashboard?incognito=<true/false>#constituents" shape. A
 * manual entry carrying #constituents is honored (fetched straight
 * into it); a reload always defaults back to the dashboard view. The
 * ?incognito= query stays backend-tracked (the server renders the
 * initial toggle states from it); the toggle code preserves the hash
 * when rewriting the query.
 *
 * The /constituents endpoint is kept as the HTML source for the
 * constituents view (and still full-renders for direct loads); in-app
 * navigation never leaves the document: the first Edit click fetches
 * it once, afterwards both bodies are served from the in-memory cache.
 * Page scripts boot idempotently per fresh node set via
 * window.__dashboardInit / window.__constituentsInit and verify
 * freshness via window.__dashboardShow /
 * window.__constituentsRevalidate.
 *
 * Freshness: the dashboard re-renders on signature mismatch; the
 * constituents HTML is background-revalidated on every show and swapped
 * in only when the page is clean (no draft open, nothing focused) —
 * user input is never clobbered. Any fetch failure falls back to a
 * classic full navigation.
 */
(function () {
  "use strict";

  var CONSTITUENTS_HASH = "#constituents";

  // "dashboard" | "constituents" -> { title, bodyHTML }
  var viewCache = new Map();
  // "dashboard" | "constituents" -> last vertical scroll offset.
  var scrollMemory = new Map();
  // In-flight fetches by url, so rapid clicks share one request.
  var pendingFetches = new Map();
  // Monotonic navigation counter: async fetches from a superseded
  // navigation are dropped so rapid clicks can't apply views out of
  // order. Cache hits and hash assignments are synchronous/ordered.
  var navSeq = 0;

  function currentView() {
    return window.location.hash === CONSTITUENTS_HASH
      ? "constituents"
      : "dashboard";
  }

  function constituentsUrl() {
    try {
      const incognito =
        new URLSearchParams(window.location.search).get("incognito") ===
        "true";
      return incognito ? "/constituents?incognito=true" : "/constituents";
    } catch {
      return "/constituents";
    }
  }

  function dashboardUrl() {
    try {
      const params = new URLSearchParams(window.location.search);
      const query = params.toString();
      return "/dashboard" + (query ? "?" + query : "");
    } catch {
      return "/dashboard";
    }
  }

  function rememberScroll(view) {
    try {
      scrollMemory.set(view, window.scrollY || 0);
    } catch {
      // Ignore: scroll restore is best-effort.
    }
  }

  function fetchDoc(url) {
    let pending = pendingFetches.get(url);
    if (!pending) {
      pending = fetch(url)
        .then(function (response) {
          if (!response.ok) {
            throw new Error("view fetch failed: " + response.status);
          }
          return response.text();
        })
        .then(function (html) {
          const doc = new DOMParser().parseFromString(html, "text/html");
          if (!doc || !doc.body) {
            throw new Error("view parse failed");
          }
          return { title: doc.title || "", bodyHTML: doc.body.innerHTML };
        });
      pendingFetches.set(url, pending);
      const forget = function () {
        if (pendingFetches.get(url) === pending) {
          pendingFetches.delete(url);
        }
      };
      pending.then(forget, forget);
    }
    return pending;
  }

  function bootView() {
    try {
      if (document.getElementById("gallery")) {
        if (typeof window.__dashboardInit === "function") {
          window.__dashboardInit();
        }
        if (typeof window.__dashboardShow === "function") {
          window.__dashboardShow();
        }
      } else if (document.getElementById("overview-link")) {
        if (typeof window.__constituentsInit === "function") {
          window.__constituentsInit();
        }
      }
    } catch (err) {
      console.warn(err);
    }
  }

  function isConstituentsClean() {
    if (document.querySelector("tr.draft")) {
      return false;
    }
    const focused = document.activeElement;
    if (
      focused &&
      (focused.tagName === "INPUT" ||
        focused.tagName === "SELECT" ||
        focused.tagName === "TEXTAREA")
    ) {
      return false;
    }
    return true;
  }

  function otherView(view) {
    return view === "constituents" ? "dashboard" : "constituents";
  }

  // A cached entry is only paintable when it actually holds that view's
  // markup (each view has an element the other never renders). This makes
  // displaying a poisoned entry impossible: a mismatch falls through to
  // a fresh fetch instead of flashing the wrong view.
  function entryMatchesView(view, entry) {
    if (!entry || typeof entry.bodyHTML !== "string") {
      return false;
    }
    return view === "constituents"
      ? entry.bodyHTML.indexOf('id="overview-link"') !== -1
      : entry.bodyHTML.indexOf('id="gallery"') !== -1;
  }

  // Stash the live DOM of the view being left, so input values, gallery
  // markup, and scroll-era state survive the round trip. Keyed
  // explicitly: by the time hashchange fires, the hash already names the
  // new view, so "current view" would file the stash under the wrong key.
  // Revalidation on show covers external staleness.
  function stashLeaving(target) {
    const leaving = otherView(target);
    try {
      const entry = {
        title: document.title || "",
        bodyHTML: document.body.innerHTML,
      };
      // Never stash a mismatched body: a mid-swap race must not poison
      // the cache with the wrong view's markup.
      if (!entryMatchesView(leaving, entry)) {
        return;
      }
      viewCache.set(leaving, entry);
    } catch {
      // Ignore: the target view still works uncached.
    }
  }

  function renderCached(view, entry) {
    if (entry.title) {
      document.title = entry.title;
    }
    document.body.innerHTML = entry.bodyHTML;
    bootView();
    try {
      window.scrollTo(0, scrollMemory.get(view) || 0);
    } catch {
      // Ignore: scroll restore is best-effort.
    }
  }

  function revalidate(view) {
    // Dashboard freshness is owned by its signature check on show.
    if (view !== "constituents") {
      return;
    }
    // Constituents: refetch in background; adopt the fresh HTML only
    // when the page is clean, otherwise keep the live DOM (the next
    // show retries).
    if (typeof window.__constituentsRevalidate !== "function") {
      return;
    }
    window.__constituentsRevalidate(constituentsUrl()).then(
      function (html) {
        let fresh = null;
        try {
          const doc = new DOMParser().parseFromString(html, "text/html");
          fresh = { title: doc.title || "", bodyHTML: doc.body.innerHTML };
        } catch {
          return;
        }
        const cached = viewCache.get("constituents");
        if (!cached || cached.bodyHTML === fresh.bodyHTML) {
          viewCache.set("constituents", fresh);
          return;
        }
        if (!isConstituentsClean()) {
          return;
        }
        viewCache.set("constituents", fresh);
        // Only swap when still looking at this view (no intervening nav).
        if (currentView() === "constituents") {
          renderCached("constituents", fresh);
        }
      },
      function () {
        // Ignore: keep serving the cached view.
      }
    );
  }

  function showView(view) {
    if (view !== "constituents") {
      view = "dashboard";
    }
    stashLeaving(view);
    const hit = viewCache.get(view);
    if (hit && entryMatchesView(view, hit)) {
      navSeq += 1;
      renderCached(view, hit);
      revalidate(view);
      return true;
    }
    const seq = (navSeq += 1);
    const url = view === "constituents" ? constituentsUrl() : dashboardUrl();
    fetchDoc(url).then(
      function (entry) {
        if (seq !== navSeq) {
          return;
        }
        viewCache.set(view, entry);
        // The hash may have moved on while fetching (rapid clicks);
        // only paint when this view is still the requested one.
        if (currentView() !== view) {
          return;
        }
        renderCached(view, entry);
        revalidate(view);
      },
      function () {
        if (seq !== navSeq) {
          return;
        }
        window.location.href = url;
      }
    );
    return true;
  }

  function goConstituents() {
    if (window.location.hash === CONSTITUENTS_HASH) {
      showView("constituents");
      return true;
    }
    window.location.hash = "constituents";
    // Safety net: the hashchange event above normally drives the swap,
    // but if it is ever swallowed (or the fetch path stalls before
    // painting), force the view once things settle. The guard makes a
    // duplicate delivery a no-op, and a newer navigation wins by failing
    // the hash check — so this can never fight legitimate state.
    window.setTimeout(function () {
      try {
        if (
          window.location.hash === CONSTITUENTS_HASH &&
          !document.getElementById("overview-link")
        ) {
          showView("constituents");
        }
      } catch {
        // Ignore: the event path already handled it.
      }
    }, 120);
    return true;
  }

  function goDashboard() {
    if (
      window.location.hash === "" ||
      typeof window.location.hash !== "string"
    ) {
      showView("dashboard");
      return true;
    }
    // Clear the hash without a reload; hash assignment to "" is
    // inconsistent across browsers, so rewrite the URL and route.
    try {
      window.history.pushState(null, "", dashboardUrl());
    } catch {
      window.location.hash = "";
      return true;
    }
    showView("dashboard");
    return true;
  }

  // Entry point used by the Edit/Dashboard click handlers: maps the
  // legacy full-URL targets onto hash views. Always takes over (async
  // fetch or instant cache hit); falls back to a classic navigation
  // internally on failure. Returns true so callers skip their own
  // window.location.href assignment.
  window.__switchView = function (url) {
    try {
      const path = new URL(url, window.location.href).pathname;
      if (path.indexOf("constituents") !== -1) {
        return goConstituents();
      }
      return goDashboard();
    } catch {
      window.location.href = url;
      return true;
    }
  };

  window.addEventListener("hashchange", function () {
    navSeq += 1;
    showView(currentView());
  });

  function wasReload() {
    try {
      if (typeof performance === "undefined") {
        return false;
      }
      const entries = performance.getEntriesByType("navigation");
      if (
        entries &&
        entries[0] &&
        typeof entries[0].type === "string"
      ) {
        return entries[0].type === "reload";
      }
    } catch {
      // Ignore: fall through to honoring the hash.
    }
    return false;
  }

  // Fresh document loads: honor an incoming #constituents (manual entry,
  // deep link) by fetching into it — except on reload, which always
  // defaults back to the dashboard view. Either way no history entry is
  // added. The served markup already is the dashboard.
  try {
    if (
      window.location.hash === CONSTITUENTS_HASH &&
      !wasReload()
    ) {
      showView("constituents");
    } else if (window.location.hash) {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search
      );
    }
  } catch {
    // Ignore: the hash simply stays inert.
  }
})();
