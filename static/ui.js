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
     number box is what gets submitted. Dragging the slider copies its value
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

  // The dashboard's year slider re-renders the page, which is expensive, so it
  // fires its request once on release instead of on every step of the drag.
  document.addEventListener("change", function (event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "range") return;
    const pair = target.closest(".range-pair[data-fire-change]");
    if (!pair) return;
    pair.querySelector('input[type="number"]').dispatchEvent(new Event("change", { bubbles: true }));
  });

  /* ---------------------------------------------------------- undo toast */

  /* Deleting a row asks for no confirmation; instead a toast offers Undo for
     a few seconds. The server fires `row-deleted` and remembers the row. */
  document.addEventListener("row-deleted", function (event) {
    const region = document.getElementById("toasts");
    if (!region || !event.detail) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    const text = document.createElement("span");
    text.textContent = "Deleted \u201c" + event.detail.name + "\u201d";
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "btn";
    undo.textContent = "Undo";
    undo.setAttribute("hx-post", "/rows/undo/");
    undo.setAttribute("hx-target", "#table-" + event.detail.slug);
    undo.setAttribute("hx-swap", "outerHTML");
    toast.append(text, undo);
    region.replaceChildren(toast);
    htmx.process(toast);
    const timer = setTimeout(function () { toast.remove(); }, 9000);
    // Keep the button in the page until the request finishes: the server's
    // "metrics-changed" event is fired on it and has to bubble up first.
    undo.addEventListener("click", function () { clearTimeout(timer); undo.disabled = true; });
    undo.addEventListener("htmx:afterRequest", function () { toast.remove(); });
  });

  /* ------------------------------------------------- table sort + filter */

  function cellValue(cell) {
    const text = cell.textContent.trim();
    const month = Date.parse("1 " + text);
    if (/^[A-Za-z]{3} \d{4}$/.test(text) && !isNaN(month)) return month;
    const number = parseFloat(text.replace(/[\u20b9,%\s]/g, "").replace("\u2212", "-"));
    if (!isNaN(number) && /\d/.test(text) && /^[-\u2212+]?[\u20b9\d]/.test(text)) return number;
    return text.toLowerCase();
  }

  function compare(a, b) {
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b));
  }

  function enhanceTables() {
    document.querySelectorAll(".table-card .data-table").forEach(function (table) {
      if (table.dataset.enhanced) return;
      table.dataset.enhanced = "1";
      const tbody = table.tBodies[0];
      const headers = Array.from(table.querySelectorAll("thead th"));

      // A "group" is a data row plus the note row that follows it, if any.
      function groups() {
        const out = [];
        Array.from(tbody.rows).forEach(function (row) {
          if (row.classList.contains("note-row") && out.length) out[out.length - 1].push(row);
          else if (!row.querySelector("td.empty")) out.push([row]);
        });
        return out;
      }

      headers.forEach(function (th, index) {
        if (th.classList.contains("actions-col")) return;
        th.classList.add("sortable");
        th.setAttribute("tabindex", "0");
        th.setAttribute("aria-sort", "none");
        function sort() {
          const ascending = th.getAttribute("aria-sort") !== "ascending";
          headers.forEach(function (other) { other.setAttribute("aria-sort", "none"); });
          th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
          const sorted = groups().sort(function (x, y) {
            const result = compare(cellValue(x[0].cells[index]), cellValue(y[0].cells[index]));
            return ascending ? result : -result;
          });
          sorted.forEach(function (group) { group.forEach(function (row) { tbody.appendChild(row); }); });
        }
        th.addEventListener("click", sort);
        th.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); sort(); }
        });
      });

      // A filter box once there are enough rows to be worth searching.
      if (groups().length >= 6) {
        const head = table.closest(".table-card").querySelector(".card-head");
        const box = document.createElement("input");
        box.type = "search";
        box.className = "table-filter input";
        box.placeholder = "Filter rows";
        box.setAttribute("aria-label", "Filter rows");
        box.addEventListener("input", function () {
          const needle = box.value.trim().toLowerCase();
          groups().forEach(function (group) {
            const show = !needle || group[0].textContent.toLowerCase().includes(needle);
            group.forEach(function (row) { row.hidden = !show; });
          });
        });
        head.insertBefore(box, head.lastElementChild);
      }
    });
  }

  /* ----------------------------------------------------------------- init */

  function init() {
    setupTheme();
    setupPrivacy();
    setupMenus();
    labelCells();
    enhanceTables();
    countUp();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  // HTMX swaps whole table cards and the dashboard body.
  document.addEventListener("htmx:afterSwap", function (event) {
    labelCells();
    enhanceTables();
    countUp(event.target);
  });
})();
