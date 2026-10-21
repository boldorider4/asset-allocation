/* Login screen: pick a user, optionally in incognito mode, then log in.
 *
 * The incognito toggle mirrors the dashboard's: a glyph button whose
 * pressed state is the state, plus a plain ON/OFF read-out. It decides
 * the ?incognito= flag the server puts on the dashboard URL we land on.
 * Incognito starts engaged: the figures are hidden until you say so.
 * The user name is validated by the server (unknown users are
 * rejected), so a typo never looks like an empty portfolio.
 */
(function () {
  "use strict";

  var state = { incognito: true, busy: false };

  // Failed logins highlight the field instead of showing a banner: a red
  // outline that stays until the next attempt, plus a one-shot flare
  // (like the constituents' saved/stale flash). The message still goes
  // to the screen-reader-only status line.
  function announce(text) {
    var status = document.getElementById("login-status");
    if (status) {
      status.textContent = text;
    }
  }

  function fieldError(text) {
    var input = document.getElementById("login-user");
    if (input) {
      input.classList.add("login-error");
      input.classList.add("login-flare");
      input.setAttribute("aria-invalid", "true");
      window.setTimeout(function () {
        input.classList.remove("login-flare");
      }, 900);
    }
    announce(text);
  }

  function clearFieldError() {
    var input = document.getElementById("login-user");
    if (input) {
      input.classList.remove("login-error");
      input.classList.remove("login-flare");
      input.removeAttribute("aria-invalid");
    }
    announce("");
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
    // A new attempt clears the previous error state.
    clearFieldError();
    if (!user) {
      fieldError("Enter a user name.");
      if (input) {
        input.focus();
      }
      return;
    }
    state.busy = true;
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
        fieldError(data.error || "Login failed.");
        state.busy = false;
        return;
      }
      window.location.href = data.redirect || "/dashboard";
    } catch (err) {
      fieldError("Login failed: " + (err && err.message ? err.message : err));
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
