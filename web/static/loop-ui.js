/* The page controls that live in the browser rather than the Selector:
   the theme, the folds, and filling Add Target path fields from the last
   Target's layout. The theme stays in localStorage and is applied before
   first paint by the inline script in terminal_base.html's <head>.
   Folds cannot: the live region is swapped whole on every Journal row
   (#159) from a server render, so the choice has to be a cookie the
   server reads, or a reload and an hx-get both come back with the markup
   defaults (#123).

   This file owns the theme button and the cycle: system -> light -> dark
   -> system. "system" is the absence of a stored choice, so a visitor who
   never touched the button follows the OS. It also writes the fold cookie
   when a details.fold is toggled, and fills Add Target paths as the
   operator types a repository name. */
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

  /* Path fields on Add Target: as the operator types owner/name, fill
     blank work/box/token inputs from their placeholders, which already
     carry the last Target's layout with `name` standing in. A field
     the operator has typed in is left alone. */
  function renameLast(value, old, neu) {
    var i = value.lastIndexOf("/");
    var last = i >= 0 ? value.slice(i + 1) : value;
    var parent = i >= 0 ? value.slice(0, i + 1) : "";
    if (last === old) { return parent + neu; }
    if (old && last.indexOf(old) >= 0) {
      return parent + last.replace(old, neu);
    }
    return "";
  }

  function wireTargetAdd(root) {
    var form = root.querySelector ? root.querySelector("form.target-add") : null;
    if (!form && root.matches && root.matches("form.target-add")) { form = root; }
    if (!form) { return; }
    var repoInput = form.querySelector('[name="repo"]');
    if (!repoInput) { return; }
    var fields = ["work_repo", "box_repo", "token_file"];
    function fill() {
      var repo = (repoInput.value || "").trim();
      var slash = repo.lastIndexOf("/");
      var name = slash >= 0 ? repo.slice(slash + 1) : "";
      if (!name) { return; }
      for (var i = 0; i < fields.length; i++) {
        var el = form.querySelector('[name="' + fields[i] + '"]');
        if (!el || el.dataset.edited) { continue; }
        var hint = el.getAttribute("placeholder") || "";
        if (!hint) { continue; }
        var next = renameLast(hint, "name", name);
        if (next) { el.value = next; }
      }
    }
    if (!form.dataset.wired) {
      form.dataset.wired = "1";
      for (var j = 0; j < fields.length; j++) {
        (function (el) {
          if (!el) { return; }
          el.addEventListener("input", function () { el.dataset.edited = "1"; });
        })(form.querySelector('[name="' + fields[j] + '"]'));
      }
      repoInput.addEventListener("input", fill);
    }
    fill();
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
    wireTargetAdd(document);

    /* htmx swaps land inside <body>, so the listener belongs there - and
       body is guaranteed to exist by now. Fold open/shut is already on
       the swapped markup (the cookie rode that request). */
    document.body.addEventListener("htmx:afterSwap", function (e) {
      wireTargetAdd(e.target);
    });
  });

  /* toggle does not bubble, so listen in the capture phase. */
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (d && d.matches && d.matches("details.fold[data-fold]")) {
      persistFold(d);
    }
  }, true);
})();
