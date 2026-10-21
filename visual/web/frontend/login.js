/* Login screen: pick a user, optionally in incognito mode, then log in.
 *
 * The incognito toggle mirrors the dashboard's: a glyph button whose
 * pressed state is the state, plus a plain ON/OFF read-out. It decides
 * the ?incognito= flag the server puts on the dashboard URL we land on.
 * Incognito starts engaged: the figures are hidden until you say so.
 * The credentials are validated by the server (an unknown user and a
 * wrong password are refused the same way), so a typo never looks like
 * an empty portfolio. Empty passwords are allowed.
 *
 * The form has two shapes, described by MODES: logging in, and
 * resetting a password. The active shape decides the labels, which
 * boxes are shown, the button text and whether the incognito toggle
 * is there. A reset stays on this page and restores the login shape
 * when it succeeds.
 */
(function () {
  "use strict";

  var state = { incognito: true, busy: false };

  // Every box the form can show; the modes pick which ones are visible.
  var BOXES = [
    { id: "login-user", labelId: "login-user-label" },
    { id: "login-password", labelId: "login-password-label" },
    { id: "login-new-password", labelId: "login-new-password-label" },
  ];

  // The two shapes of the form.
  var MODES = {
    login: {
      submit: "Log In",
      link: "Reset password",
      fields: [
        { id: "login-user", label: "User" },
        { id: "login-password", label: "Password" },
      ],
    },
    reset: {
      submit: "Reset",
      link: "Cancel",
      fields: [
        { id: "login-user", label: "User" },
        { id: "login-password", label: "Old Password" },
        { id: "login-new-password", label: "New Password" },
      ],
    },
  };

  // The shape the form is in right now.
  var mode = "login";

  // Failed logins highlight the fields instead of showing a banner: a
  // red outline that stays until the next attempt, plus a one-shot flare
  // (like the constituents' saved flash). Both the user and the
  // password box are marked — a wrong password is as much a failure as
  // an unknown user. The message still goes to the screen-reader-only
  // status line.
  function announce(text) {
    var status = document.getElementById("login-status");
    if (status) {
      status.textContent = text;
    }
  }

  function message(text) {
    var note = document.getElementById("login-message");
    if (!note) {
      return;
    }
    note.textContent = text;
    note.hidden = !text;
  }

  // The boxes of the active shape: the ones a failure is about.
  function loginFields() {
    var fields = MODES[mode].fields;
    return fields
      .map(function (field) {
        return document.getElementById(field.id);
      })
      .filter(function (field) {
        return !!field;
      });
  }

  function fieldError(text) {
    var fields = loginFields();
    fields.forEach(function (input) {
      input.classList.add("login-error");
      input.classList.add("login-flare");
      input.setAttribute("aria-invalid", "true");
    });
    window.setTimeout(function () {
      fields.forEach(function (input) {
        input.classList.remove("login-flare");
      });
    }, 900);
    announce(text);
  }

  function clearFieldError() {
    loginFields().forEach(function (input) {
      input.classList.remove("login-error");
      input.classList.remove("login-flare");
      input.removeAttribute("aria-invalid");
    });
    announce("");
  }

  function boxFor(id) {
    for (var i = 0; i < BOXES.length; i++) {
      if (BOXES[i].id === id) {
        return BOXES[i];
      }
    }
    return null;
  }

  function valueOf(id) {
    var input = document.getElementById(id);
    return input ? String(input.value || "") : "";
  }

  /* Switch the form to another shape: the labels of the used boxes,
   * the visibility of the unused ones, the button text, the incognito
   * toggle (meaningless while resetting) and the link (which doubles
   * as "Cancel" there).
   */
  function applyMode(next) {
    var name = MODES[next] ? next : "login";
    mode = name;
    var config = MODES[name];
    var used = {};
    config.fields.forEach(function (field) {
      used[field.id] = true;
    });
    BOXES.forEach(function (box) {
      var shown = !!used[box.id];
      var input = document.getElementById(box.id);
      var label = document.getElementById(box.labelId);
      if (input) {
        input.hidden = !shown;
      }
      if (label) {
        label.hidden = !shown;
      }
    });
    config.fields.forEach(function (field) {
      var box = boxFor(field.id);
      var label = box && document.getElementById(box.labelId);
      if (label) {
        label.textContent = field.label;
      }
    });
    var submit = document.getElementById("login-submit");
    if (submit) {
      submit.textContent = config.submit;
    }
    var link = document.getElementById("reset-password-link");
    if (link) {
      link.textContent = config.link;
    }
    var toggle = document.getElementById("login-toggle");
    if (toggle) {
      toggle.hidden = name === "reset";
    }
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

  function clearPasswords() {
    ["login-password", "login-new-password"].forEach(function (id) {
      var input = document.getElementById(id);
      if (input) {
        input.value = "";
      }
    });
  }

  /* Reset the password: the old one is checked, the new one is stored.
   * On success the login shape comes back (the whole set of boxes
   * reappears) and the message says so.
   */
  async function submitReset(user) {
    var oldPassword = valueOf("login-password");
    var newPassword = valueOf("login-new-password");
    state.busy = true;
    try {
      var response = await fetch("/api/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user: user,
          old_password: oldPassword,
          new_password: newPassword,
        }),
      });
      var data = {};
      try {
        data = await response.json();
      } catch (err) {
        data = {};
      }
      if (!response.ok || !data.ok) {
        fieldError(data.error || "Reset failed.");
        state.busy = false;
        return;
      }
      clearPasswords();
      applyMode("login");
      message("Password updated. Log in with the new one.");
      state.busy = false;
    } catch (err) {
      fieldError("Reset failed: " + (err && err.message ? err.message : err));
      state.busy = false;
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
    message("");
    if (!user) {
      fieldError("Enter a user name.");
      if (input) {
        input.focus();
      }
      return;
    }
    if (mode === "reset") {
      await submitReset(user);
      return;
    }
    // The password is sent verbatim (no trimming: it may contain
    // spaces). Empty passwords are allowed.
    var password = valueOf("login-password");
    state.busy = true;
    try {
      var response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user: user,
          password: password,
          incognito: state.incognito,
        }),
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
    applyMode("login");
    applyIncognito();
    var toggle = document.getElementById("incognito-link");
    if (toggle) {
      toggle.addEventListener("click", function () {
        state.incognito = !state.incognito;
        applyIncognito();
      });
    }
    var link = document.getElementById("reset-password-link");
    if (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        if (mode === "reset") {
          clearPasswords();
          applyMode("login");
          message("");
        } else {
          applyMode("reset");
          message("");
        }
        var field = document.getElementById("login-password");
        if (field) {
          field.focus();
        }
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
