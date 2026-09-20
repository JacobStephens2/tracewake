/* The page controls that live in the browser rather than the Selector:
   the theme, the folds, and filling Add Target path fields from the last
   Target's layout. Theme and folds keep their state in localStorage, and
   both survive the live region being swapped whole on every Journal row
   (#159) - the fold state is re-applied on htmx:afterSwap, and the theme
   never lived inside the swapped region at all.

   The theme is applied before first paint by the inline script in
   terminal_base.html's <head>; this file only owns the button and the cycle:
   system -> light -> dark -> system. "system" is the absence of a stored
   choice, so a visitor who never touched the button follows the OS. */
(function () {
  "use strict";

  var MODES = ["system", "light", "dark"];

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

  function applyFolds(root) {
    var details = root.querySelectorAll("details.fold[data-fold]");
    for (var i = 0; i < details.length; i++) {
      var d = details[i];
      try {
        var v = localStorage.getItem("fold:" + d.dataset.fold);
        if (v !== null && d.dataset.foldForce !== "open") {
          d.open = v === "open";
        }
      } catch (e) { /* storage denied: the markup's default stands */ }
    }
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
    applyFolds(document);
    wireTargetAdd(document);

    /* htmx swaps land inside <body>, so the listener belongs there - and
       body is guaranteed to exist by now. */
    document.body.addEventListener("htmx:afterSwap", function (e) {
      applyFolds(e.target);
      wireTargetAdd(e.target);
    });
  });

  /* toggle does not bubble, so listen in the capture phase. */
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (d && d.matches && d.matches("details.fold[data-fold]")) {
      try {
        localStorage.setItem("fold:" + d.dataset.fold, d.open ? "open" : "closed");
      } catch (e2) { /* storage denied */ }
    }
  }, true);
})();
