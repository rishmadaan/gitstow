/* Dashboard filters — client-side over server-rendered data-* attributes.
   Re-applied after every HTMX swap because the 30s auto-refresh replaces
   the tbody rows (the controls live outside it and keep their state). */
(function () {
  "use strict";

  function applyFilters() {
    var ws = (document.getElementById("ws-filter") || {}).value || "";
    var q = ((document.getElementById("repo-search") || {}).value || "").trim().toLowerCase();
    var hideFrozen = !!(document.getElementById("hide-frozen") || {}).checked;

    var rows = document.querySelectorAll("tbody tr[data-key]");
    var visible = 0;
    rows.forEach(function (tr) {
      var match = true;
      if (ws && tr.dataset.workspace !== ws) match = false;
      if (match && q) {
        var hay = tr.dataset.key + " " + tr.dataset.tags;
        if (hay.indexOf(q) === -1) match = false;
      }
      if (match && hideFrozen && tr.dataset.frozen === "1") match = false;
      tr.hidden = !match;
      if (match) visible++;
    });

    var empty = document.getElementById("filter-empty");
    if (empty) empty.hidden = visible > 0 || rows.length === 0;
  }

  /* Event delegation on document.body: the "↻ Refresh" button swaps the
     ENTIRE <main> (hx-select/hx-target "main", outerHTML), replacing the
     filter controls themselves — direct per-element listeners would die
     after one Refresh. Delegated listeners survive any swap. */
  function bind() {
    document.body.addEventListener("input", function (e) {
      if (e.target && e.target.id === "repo-search") {
        clearTimeout(bind._debounce);
        bind._debounce = setTimeout(applyFilters, 120);
      }
    });
    document.body.addEventListener("change", function (e) {
      if (e.target && (e.target.id === "ws-filter" || e.target.id === "hide-frozen")) {
        applyFilters();
      }
    });
    // Auto-refresh (and pull row-swaps) replace rows — re-apply the active filters.
    document.body.addEventListener("htmx:afterSwap", applyFilters);
    applyFilters();

    // Escape closes any open row-actions menu, regardless of focus location.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        document.querySelectorAll("details.menu[open]").forEach(function (d) { d.removeAttribute("open"); });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.applyFilters = applyFilters; // exposed for the live smoke assertion
})();
