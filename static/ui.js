/* Small UI behaviours: theme and privacy switches, the nav dropdowns, stacked
 * table labels on phones, and the count-up on headline figures.
 *
 * Everything degrades to a working page without this file -- the theme
 * follows the system, tables scroll sideways, numbers just appear.
 */

(function () {
  "use strict";

  const root = document.documentElement;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function save(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  /* ---------------------------------------------------------------- theme */

  function effectiveTheme() {
    const set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function setupTheme() {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    button.addEventListener("click", function () {
      const next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      save("planner-theme", next);
      // Charts take their colours from CSS variables at draw time.
      document.dispatchEvent(new CustomEvent("planner:theme"));
    });
  }

  /* -------------------------------------------------------------- privacy */

  function setupPrivacy() {
    const button = document.getElementById("privacy-toggle");
    if (!button) return;
    function paint() {
      const on = root.getAttribute("data-privacy") === "on";
      button.setAttribute("aria-pressed", on ? "true" : "false");
      const label = on ? "Show amounts" : "Hide amounts";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
    paint();
    button.addEventListener("click", function () {
      const on = root.getAttribute("data-privacy") === "on";
      if (on) root.removeAttribute("data-privacy"); else root.setAttribute("data-privacy", "on");
      save("planner-privacy", on ? "off" : "on");
      paint();
    });
  }

  /* ------------------------------------------------------------ dropdowns */

  function setupMenus() {
    const menus = document.querySelectorAll("details.menu");
    document.addEventListener("click", function (event) {
      menus.forEach(function (menu) {
        if (!menu.contains(event.target)) menu.removeAttribute("open");
      });
    });
    menus.forEach(function (menu) {
      // Opening one closes the others.
      menu.addEventListener("toggle", function () {
        if (!menu.open) return;
        menus.forEach(function (other) { if (other !== menu) other.removeAttribute("open"); });
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") menus.forEach(function (menu) { menu.removeAttribute("open"); });
    });
  }

  /* ------------------------------------------------- stacked table labels */

  /* On phones a data table turns into stacked cards (see planner.css); each
     cell needs its column heading as a label. Copy it from the header row,
     minus the "i" tooltips and "derived" flags that live inside it. */
  function labelCells(scope) {
    (scope || document).querySelectorAll(".table-card .data-table").forEach(function (table) {
      const labels = Array.from(table.querySelectorAll("thead th")).map(function (th) {
        const clone = th.cloneNode(true);
        clone.querySelectorAll(".info, .derived-flag").forEach(function (el) { el.remove(); });
        return clone.textContent.trim();
      });
      table.querySelectorAll("tbody tr").forEach(function (row) {
        if (row.classList.contains("form-row") || row.classList.contains("note-row")) return;
        Array.from(row.children).forEach(function (cell, index) {
          if (labels[index] && !cell.hasAttribute("data-label")) cell.setAttribute("data-label", labels[index]);
        });
      });
    });
  }

  /* ------------------------------------------------------------- count-up */

  const RUPEE_NUMBER = /^(-?)₹([\d,]+)$/;

  function groupIndian(digits) {
    const head = digits.slice(0, -3);
    const tail = digits.slice(-3);
    if (!head) return tail;
    return head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }

  function countUp(scope) {
    if (reduceMotion) return;
    (scope || document).querySelectorAll(".stat-value, .hero-second strong").forEach(function (el) {
      if (el.dataset.counted) return;
      el.dataset.counted = "1";
      const match = RUPEE_NUMBER.exec(el.textContent.trim());
      if (!match) return;
      const sign = match[1];
      const target = parseInt(match[2].replace(/,/g, ""), 10);
      if (!target || target < 1000) return;

      const final = el.textContent;
      const duration = 800;
      const start = performance.now();
      function frame(now) {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = sign + "₹" + groupIndian(String(Math.round(target * eased)));
        if (t < 1) requestAnimationFrame(frame); else el.textContent = final;
      }
      requestAnimationFrame(frame);
    });
  }

  /* ------------------------------------------------------- slider + number */

  /* Each slider is paired with a number box: the slider has no name, so the
     number box is what the form submits. Dragging the slider copies its value
     across before the form's own input listener fires. */
  document.addEventListener("input", function (event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const pair = target.closest(".range-pair");
    if (!pair) return;
    const range = pair.querySelector('input[type="range"]');
    const number = pair.querySelector('input[type="number"]');
    if (target === range) number.value = range.value; else range.value = number.value;
  }, true);

  /* ----------------------------------------------------------------- init */

  function init() {
    setupTheme();
    setupPrivacy();
    setupMenus();
    labelCells();
    countUp();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  // HTMX swaps whole table cards and the dashboard body.
  document.addEventListener("htmx:afterSwap", function (event) {
    labelCells();
    countUp(event.target);
  });
})();
