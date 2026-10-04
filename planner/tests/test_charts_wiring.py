"""Guards for how charts are drawn.

Charts used to be drawn from inline <script> calls in the page. HTMX runs those
mid-swap, before the new markup has settled, so Chart.js measured the canvases
too early and later restored them to the wrong size -- picking a different
year could stretch the page. Pages now only carry <script data-chart="...">
data and charts.js draws after the swap settles.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "planner" / "templates" / "planner"


class ChartWiringTests(SimpleTestCase):
    def test_no_template_calls_a_chart_function_inline(self):
        offenders = []
        for path in TEMPLATES.rglob("*.html"):
            if re.search(r"render(Planner|Loan|Prepay)Charts?\(", path.read_text()):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "charts must be drawn by charts.js after the swap settles")

    def test_every_json_chart_payload_is_tagged_for_drawing(self):
        for path in TEMPLATES.rglob("*.html"):
            for tag in re.findall(r'<script id="[\w-]*(?:chart|data)[\w-]*" type="application/json"[^>]*>', path.read_text()):
                self.assertIn("data-chart=", tag, f"{path.name}: {tag}")

    def test_charts_js_draws_on_load_and_after_swaps_and_prunes_dead_charts(self):
        js = (ROOT / "static" / "charts.js").read_text()
        for needle in ("htmx:afterSettle", "DOMContentLoaded", "pruneCharts", "isConnected"):
            self.assertIn(needle, js)

    def test_chart_holders_can_shrink_and_are_positioned(self):
        css = (ROOT / "static" / "planner.css").read_text()
        self.assertRegex(css, r"\.chart-holder \{[^}]*position: relative")
        self.assertRegex(css, r"\.chart-holder \{[^}]*min-width: 0")
