"""Build demo.html: the whole app as one self-contained file.

    .venv/bin/python scripts/build_demo.py [output.html]

Every page is rendered from the sample plan (seed_sample) into a throwaway
database, then packed into a single HTML file with the CSS, JS and Chart.js
inlined. Navigation, charts, the dashboard year control, sorting, filtering,
theme and privacy switches all work offline; edits are disabled (a toast says
so) because there is no server behind the file.
"""
import json, os, re, subprocess, sys, tempfile, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "demo.html")
CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"

def read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()

with tempfile.TemporaryDirectory() as tmp:
    dump = os.path.join(tmp, "pages.json")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_pages.py"), dump],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    data = json.load(open(dump))

years = data.pop("__years__")
current_year = str(data.pop("__current_year__"))

MAIN = re.compile(r"<main[^>]*>(.*)</main>", re.S)
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
pages = {}
for route, html in data.items():
    pages[route] = {"title": TITLE.search(html).group(1), "main": MAIN.search(html).group(1)}

# The full document shell comes from one real page; only <main> is swapped.
shell = data["/"]
head_nav = re.search(r"<header class=\"topbar\">.*?</header>", shell, re.S).group(0)
tabbar = re.search(r"<nav class=\"tabbar\".*?</nav>", shell, re.S).group(0)
footer = re.search(r"<footer class=\"footer\">.*?</footer>", shell, re.S).group(0)
early = re.search(r"<script>\s*\(function \(\) \{\s*try \{.*?</script>", shell, re.S).group(0)

css = read("static", "planner.css")
charts_js = read("static", "charts.js")
ui_js = read("static", "ui.js")
chart_lib = urllib.request.urlopen(CHART_JS).read().decode()

def safe(js):  # keep inline scripts from closing their own tag
    return js.replace("</script", "<\\/script")

router = r"""
(function () {
  "use strict";
  var PAGES = %(pages)s, YEARS = %(years)s, CURRENT = "%(current)s";
  var main = document.getElementById("main");
  var current = "/";

  function toast(text) {
    var region = document.getElementById("toasts");
    var t = document.createElement("div");
    t.className = "toast";
    t.innerHTML = "<span></span>";
    t.firstChild.textContent = text;
    region.replaceChildren(t);
    setTimeout(function () { t.remove(); }, 3500);
  }
  var READONLY = "Demo mode: this is a read-only snapshot, so edits are disabled.";

  function resolve(href) {                 // "/loans/", "?page=2" -> route key
    if (href.charAt(0) === "?") href = current.split("?")[0] + href;
    return href;
  }

  function rewrite(scope) {
    scope.querySelectorAll("a[href]").forEach(function (a) {
      var href = a.getAttribute("href");
      if (/^(#|https?:|mailto:)/.test(href)) return;
      var key = resolve(href);
      if (PAGES[key]) a.setAttribute("href", "#" + key);
      else { a.setAttribute("href", "#" + current); a.dataset.blocked = "1"; }
    });
  }

  function markActive() {
    var path = current.split("?")[0];
    var group = {"/cashflow/": 1, "/summary/": 1};
    document.querySelectorAll(".nav-pill a, .menu-list a, .tabbar a").forEach(function (a) {
      var h = (a.getAttribute("href") || "").replace(/^#/, "").split("?")[0];
      var on = h === path || (h === "/loans/" && /^\/loans\/\d+\/$/.test(path));
      a.classList.toggle("active", on);
    });
    document.querySelectorAll("details.menu").forEach(function (m) {
      m.classList.toggle("active", !!m.querySelector(".menu-list a.active"));
      m.removeAttribute("open");
    });
    var tabs = document.querySelectorAll(".tabbar a");
    var map = {"/": 0, "/cashflow/": 1, "/summary/": 1, "/income/": 2, "/expenses/": 2, "/loans/": 2,
               "/insurance/": 2, "/investments/": 3, "/retirement/": 3, "/settings/": 4};
    var idx = map[path]; if (idx === undefined && /^\/loans\//.test(path)) idx = 2;
    tabs.forEach(function (a, i) { a.classList.toggle("active", i === idx); });
  }

  function settle(scope) {
    scope.dispatchEvent(new CustomEvent("htmx:afterSwap", {bubbles: true}));
    scope.dispatchEvent(new CustomEvent("htmx:afterSettle", {bubbles: true}));
  }

  function show() {
    var key = location.hash.slice(1) || "/";
    if (!PAGES[key]) key = "/";
    current = key;
    var page = PAGES[key];
    main.innerHTML = page.main;
    document.title = page.title;
    rewrite(main);
    wireYear();
    markActive();
    window.scrollTo(0, 0);
    settle(main);
  }

  function wireYear() {
    var input = main.querySelector("input.year-input");
    if (!input) return;
    input.removeAttribute("hx-get");
    var body = main.querySelector("#dashboard-body");
    input.addEventListener("change", function () {
      var y = Math.min(Math.max(parseInt(input.value, 10) || +CURRENT, +input.min), +input.max);
      input.value = y;
      var range = main.querySelector('input[type="range"]'); if (range) range.value = y;
      body.innerHTML = YEARS[y]; rewrite(body); settle(body);
    });
    if (YEARS[CURRENT]) { /* initial body is already the current year */ }
  }

  // Anything that would write to a server is blocked with a friendly toast.
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-blocked]");
    if (a) { e.preventDefault(); toast("Not included in the demo snapshot."); return; }
    var b = e.target.closest("[hx-get],[hx-post],[hx-delete]");
    if (b && !b.classList.contains("year-input")) { e.preventDefault(); toast(READONLY); }
    var d = e.target.closest("a[href*='backup/'], a[href*='export.csv']");
    if (d) { e.preventDefault(); toast(READONLY); }
  }, true);
  document.addEventListener("submit", function (e) {
    e.preventDefault(); toast(READONLY);
  }, true);
  document.addEventListener("input", function (e) {
    if (e.target.closest && e.target.closest(".prepay-form")) toast(READONLY);
  });

  window.htmx = { process: function () {} };
  window.addEventListener("hashchange", show);
  document.addEventListener("DOMContentLoaded", function () {
    rewrite(document.querySelector(".topbar")); rewrite(document.querySelector(".tabbar"));
    show();
  });
})();
""" % {"pages": json.dumps(pages), "years": json.dumps(years), "current": current_year}

doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cashflow Planner (demo)</title>
{early}
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,500;12..96,600;12..96,700;12..96,800&display=swap">
<style>{css}</style>
<script>{safe(chart_lib)}</script>
<script>{safe(charts_js)}</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
{head_nav}
<main id="main"></main>
<div id="toasts" class="toasts" role="status" aria-live="polite"></div>
{footer.replace("Projections are recomputed from your current data on every page load", "Demo snapshot of the sample plan &mdash; read-only. Projections are recomputed from your data on every page load in the real app")}
{tabbar}
<script>{safe(router)}</script>
<script>{safe(ui_js)}</script>
</body>
</html>
"""
with open(OUT, "w", encoding="utf-8") as f:
    f.write(doc)
print(f"wrote {OUT} ({len(doc)/1024:.0f} KB, {len(pages)} pages, {len(years)} dashboard years)")
