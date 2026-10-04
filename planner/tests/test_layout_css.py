"""Guards for layout rules that broke once and should not again."""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

CSS = (Path(__file__).resolve().parents[2] / "static" / "planner.css").read_text()


class HeroTileTests(SimpleTestCase):
    """The loan page's 'Opening balance' tile used to inherit the dashboard hero's
    68px headline and overflow its 262px card. The oversized treatment belongs to
    the dashboard grid only; every other dark tile is an ordinary stat card."""

    def test_the_oversized_headline_is_scoped_to_the_dashboard_grid(self):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
            if "clamp(44px" in body:
                self.assertIn(".bento", selector, f"oversized headline leaks outside the dashboard: {selector.strip()}")

    def test_a_plain_hero_tile_keeps_the_normal_card_size(self):
        base = re.search(r"\n\.stat\.hero \{([^}]*)\}", CSS).group(1)
        self.assertNotIn("min-height", base)
        self.assertNotIn("padding", base)

    def test_stat_values_cannot_exceed_their_card(self):
        self.assertRegex(CSS, r"\.stat-value \{ max-width: 100%; \}")
