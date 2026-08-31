/* The two page controls that live in the browser rather than the Selector:
   the theme and the folds. Both keep their state in localStorage, and both
   survive the live region being swapped whole on every Journal row (#159) -
   the fold state is re-applied on htmx:afterSwap, and the theme never lived
   inside the swapped region at all.

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
        if (v !== null) { d.open = v === "open"; }
      } catch (e) { /* storage denied: the markup's default stands */ }
    }
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

    /* htmx swaps land inside <body>, so the listener belongs there - and
       body is guaranteed to exist by now. */
    document.body.addEventListener("htmx:afterSwap", function (e) {
      applyFolds(e.target);
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
