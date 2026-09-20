/* The two page controls that live in the browser rather than the Selector:
   the theme and the folds. The theme stays in localStorage and is applied
   before first paint by the inline script in terminal_base.html's <head>.
   Folds cannot: the live region is swapped whole on every Journal row
   (#159) from a server render, so the choice has to be a cookie the
   server reads, or a reload and an hx-get both come back with the markup
   defaults (#123).

   This file owns the theme button and the cycle: system -> light -> dark
   -> system. "system" is the absence of a stored choice, so a visitor who
   never touched the button follows the OS. It also writes the fold cookie
   when a details.fold is toggled. */
(function () {
  "use strict";

  var MODES = ["system", "light", "dark"];
  var FOLD_COOKIE = "fold";
  var FOLD_MAX_AGE = 31536000;

  function storedMode() {
    try {
      var t = localStorage.getItem("theme");
      return t === "light" || t === "dark" ? t : "system";
    } catch (e) { return "system"; }
  }

  function applyMode(mode) {
    if (mode === "system") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = mode;
    }
    var b = document.getElementById("theme-toggle");
    if (b) { b.textContent = "theme: " + mode; }
  }

  function readFoldCookie() {
    var prefix = FOLD_COOKIE + "=";
    var parts = document.cookie.split("; ");
    var raw = "";
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf(prefix) === 0) {
        raw = parts[i].slice(prefix.length);
        break;
      }
    }
    var chosen = {};
    if (!raw) { return chosen; }
    var entries = raw.split("|");
    for (var j = 0; j < entries.length; j++) {
      var split = entries[j].indexOf(":");
      if (split < 1) { continue; }
      var key = entries[j].slice(0, split);
      var value = entries[j].slice(split + 1);
      if (value === "open" || value === "closed") { chosen[key] = value; }
    }
    return chosen;
  }

  function writeFoldCookie(chosen) {
    var entries = [];
    for (var key in chosen) {
      if (Object.prototype.hasOwnProperty.call(chosen, key)) {
        entries.push(key + ":" + chosen[key]);
      }
    }
    var cookie = FOLD_COOKIE + "=" + entries.join("|")
      + ";path=/;max-age=" + FOLD_MAX_AGE + ";samesite=lax";
    if (location.protocol === "https:") { cookie += ";secure"; }
    document.cookie = cookie;
  }

  function persistFold(d) {
    var chosen = readFoldCookie();
    chosen[d.dataset.fold] = d.open ? "open" : "closed";
    writeFoldCookie(chosen);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var b = document.getElementById("theme-toggle");
    if (b) {
      b.addEventListener("click", function () {
        var next = MODES[(MODES.indexOf(storedMode()) + 1) % MODES.length];
        try {
          if (next === "system") { localStorage.removeItem("theme"); }
          else { localStorage.setItem("theme", next); }
        } catch (e) { /* storage denied: applies for this page only */ }
        applyMode(next);
      });
    }
    applyMode(storedMode());
  });

  /* toggle does not bubble, so listen in the capture phase. */
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (d && d.matches && d.matches("details.fold[data-fold]")) {
      persistFold(d);
    }
  }, true);
})();
