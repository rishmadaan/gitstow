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

  function bind() {
    var search = document.getElementById("repo-search");
    var wsSel = document.getElementById("ws-filter");
    var frozen = document.getElementById("hide-frozen");
    if (!search && !wsSel && !frozen) return;

    var debounce = null;
    if (search) search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(applyFilters, 120);
    });
    if (wsSel) wsSel.addEventListener("change", applyFilters);
    if (frozen) frozen.addEventListener("change", applyFilters);

    // Auto-refresh (and pull row-swaps) replace rows — re-apply the active filters.
    document.body.addEventListener("htmx:afterSwap", applyFilters);
    applyFilters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.applyFilters = applyFilters; // exposed for the live smoke assertion
})();
