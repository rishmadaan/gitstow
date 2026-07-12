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

    /* Drop-up: at narrow widths the .table-scroll wrapper (overflow-x: auto)
       becomes a clip container that cuts off the absolutely-positioned
       .menu-pop on the bottom rows. When a menu opens, measure whether the
       pop fits below it — against the viewport AND, when the wrapper is
       actively scrolling horizontally, the wrapper's visible bottom — and
       flip it above the trigger if it doesn't. Capture phase because toggle
       doesn't bubble from <details> in older engines. Every close path
       (Escape above, click-outside, action clicks) removes the open
       attribute, which re-fires toggle here, so drop-up is always cleared. */
    document.addEventListener("toggle", function (e) {
      var d = e.target;
      if (!d || !d.matches || !d.matches("details.menu")) return;
      if (!d.open) { d.classList.remove("drop-up"); return; }
      var pop = d.querySelector(".menu-pop");
      if (!pop) return;
      d.classList.remove("drop-up"); // measure from the default drop-down position
      var popHeight = pop.getBoundingClientRect().height;
      var bottomLimit = window.innerHeight;
      var scroller = d.closest && d.closest(".table-scroll");
      if (scroller && scroller.scrollWidth > scroller.clientWidth) {
        bottomLimit = Math.min(bottomLimit, scroller.getBoundingClientRect().bottom);
      }
      // 6px matches the .menu-pop top/bottom gap in app.css.
      if (d.getBoundingClientRect().bottom + 6 + popHeight > bottomLimit) {
        d.classList.add("drop-up");
      }
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.applyFilters = applyFilters; // exposed for the live smoke assertion
})();
