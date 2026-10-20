/* Login screen: pick a user, optionally in incognito mode, then log in.
 *
 * The incognito toggle mirrors the dashboard's: a glyph button whose
 * pressed state is the state, plus a plain ON/OFF read-out. It decides
 * the ?incognito= flag the server puts on the dashboard URL we land on.
 * The user name is validated by the server (unknown users are
 * rejected), so a typo never looks like an empty portfolio.
 */
(function () {
  "use strict";

  var state = { incognito: false, busy: false };

  function setStatus(text) {
    var status = document.getElementById("login-status");
    if (!status) {
      return;
    }
    status.textContent = text;
    status.hidden = !text;
  }

  function applyIncognito() {
    var toggle = document.getElementById("incognito-link");
    var label = document.getElementById("incognito-state");
    if (toggle) {
      toggle.setAttribute("aria-pressed", state.incognito ? "true" : "false");
      toggle.setAttribute(
        "title",
        state.incognito ? "Exit incognito mode" : "Incognito mode"
      );
      toggle.setAttribute(
        "aria-label",
        state.incognito ? "Exit incognito mode" : "Incognito mode"
      );
    }
    if (label) {
      label.textContent = state.incognito ? "ON" : "OFF";
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (state.busy) {
      return;
    }
    var input = document.getElementById("login-user");
    var user = input ? String(input.value || "").trim() : "";
    if (!user) {
      setStatus("Enter a user name.");
      return;
    }
    state.busy = true;
    setStatus("");
    try {
      var response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: user, incognito: state.incognito }),
      });
      var data = {};
      try {
        data = await response.json();
      } catch (err) {
        data = {};
      }
      if (!response.ok || !data.ok) {
        setStatus(data.error || "Login failed.");
        state.busy = false;
        return;
      }
      window.location.href = data.redirect || "/dashboard";
    } catch (err) {
      setStatus("Login failed: " + (err && err.message ? err.message : err));
      state.busy = false;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyIncognito();
    var toggle = document.getElementById("incognito-link");
    if (toggle) {
      toggle.addEventListener("click", function () {
        state.incognito = !state.incognito;
        applyIncognito();
      });
    }
    var form = document.getElementById("login-form");
    if (form) {
      form.addEventListener("submit", submit);
    }
    var input = document.getElementById("login-user");
    if (input) {
      input.focus();
    }
  });
})();
