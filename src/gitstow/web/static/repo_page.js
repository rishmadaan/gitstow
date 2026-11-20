/*
 * SPDX-FileCopyrightText: 2026 rishmadaan
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

/* Repo page: the Incoming / Local changes tabs, and the page-wide "busy" state
   while a Pull or Fetch on the Incoming tab is in flight. Delegated listeners:
   every Pull/Fetch answer swaps the whole #repo-page, controls included. */
(function () {
  "use strict";

  function showTab(name) {
    var page = document.getElementById("repo-page");
    if (!page) return;
    page.querySelectorAll('[role="tab"]').forEach(function (t) {
      t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false");
    });
    page.querySelectorAll('[role="tabpanel"]').forEach(function (p) {
      p.hidden = p.id !== "tab-" + name;
    });
    try {
      var url = new URL(window.location.href);
      url.searchParams.set("tab", name);
      url.hash = "";
      history.replaceState(null, "", url);
    } catch (e) { /* history unavailable: the tab still switches */ }
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-tab]");
    if (!el || !document.getElementById("repo-page")) return;
    e.preventDefault();
    showTab(el.dataset.tab);
    if (!el.matches('[role="tab"]')) {
      var tabs = document.querySelector(".tabs");
      if (tabs) tabs.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  });

  /* Arrow keys move between the two tabs (the ARIA tabs pattern). */
  document.addEventListener("keydown", function (e) {
    var tab = e.target.closest && e.target.closest('[role="tab"]');
    if (!tab || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    var all = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
    var next = all[(all.indexOf(tab) + (e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
    next.focus();
    showTab(next.dataset.tab);
  });

  /* While a Pull or Fetch runs: the page says which, its other actions are off,
     and nothing claims success until the answer (the redrawn page) arrives. */
  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var el = evt.detail && evt.detail.elt;
    var busy = el && el.dataset && el.dataset.busy;
    var page = document.getElementById("repo-page");
    if (!busy || !page) return;
    page.classList.add("is-busy", "is-" + busy);
    page.querySelectorAll("[data-busy]").forEach(function (b) { if (b !== el) b.disabled = true; });
  });
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var el = evt.detail && evt.detail.elt;
    var page = document.getElementById("repo-page");
    if (!el || !el.dataset || !el.dataset.busy || !page) return;
    if (evt.detail.successful) return;  // the redrawn page replaces this one
    page.classList.remove("is-busy", "is-pulling", "is-fetching");
    page.querySelectorAll("[data-busy]").forEach(function (b) { b.disabled = false; });
    var toast = document.getElementById("toast");
    if (toast) {
      toast.textContent = "The request did not reach gitstow, or it failed. Nothing on this page changed; reload to re-read.";
      toast.className = "toast show toast-error";
      setTimeout(function () { toast.className = "toast"; }, 4000);
    }
  });
})();
