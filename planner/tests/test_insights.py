"""Insights, milestones, FI progress and the surplus heatmap."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from planner import insights
from planner.middleware import get_owner
from planner.services.projection import annual_rollup, build_projection, summarise

from .factories import (
    make_expense,
    make_holding,
    make_loan,
    make_one_time_expense,
    make_planner,
    make_salary,
)

D = Decimal


class InsightTests(TestCase):
    def setUp(self):
        self.user = get_owner()

    def run_all(self, **planner_overrides):
        planner = make_planner(self.user, project_to_year=2030, **planner_overrides)
        rows = build_projection(self.user)
        years = annual_rollup(rows)
        summary = summarise(rows)
        return planner, rows, years, summary

    def titles(self, *args):
        planner, rows, years, summary = args
        return [i.title for i in insights.build_insights(rows, years, summary, planner, self.user)]

    def test_nothing_to_say_without_data(self):
        args = self.run_all()  # a planner row, but no salary, expenses or anything else
        self.assertEqual(self.titles(*args), [])

    def test_overspending_raises_the_dry_bank_warning_first(self):
        make_salary(self.user, amount="50000")
        make_expense(self.user, amount="60000")
        args = self.run_all()
        out = insights.build_insights(args[1], args[2], args[3], args[0], self.user)
        self.assertEqual(out[0].level, insights.WARN)
        self.assertIn("bank runs dry", out[0].title)

    def test_a_healthy_saver_gets_the_good_news(self):
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="30000")
        self.assertTrue(any("saving about" in t for t in self.titles(*self.run_all())))

    def test_heavy_emis_are_flagged(self):
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="20000")
        make_loan(self.user, monthly_emi=D("45000"), principal_outstanding_today=D("3000000"), tenure_months=120)
        self.assertTrue(any("EMIs take" in t for t in self.titles(*self.run_all())))

    def test_lump_sums_soon_that_the_bank_cannot_cover(self):
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="95000")
        make_one_time_expense(self.user, amount="400000", month=dt.date(2026, 2, 1))
        self.assertTrue(any("lump-sum costs" in t for t in self.titles(*self.run_all())))


class MilestoneAndFITests(TestCase):
    def setUp(self):
        self.user = get_owner()
        make_planner(self.user, project_to_year=2040, default_investment_return_pct=D("12"))
        make_salary(self.user, amount="150000")
        make_expense(self.user, amount="40000")
        make_holding(self.user, current_value=D("0"), monthly_sip=D("50000"))

    def test_net_worth_steps_are_dated_in_order(self):
        rows = build_projection(self.user)
        items = insights.milestones(rows, summarise(rows), annual_rollup(rows), self.user.planner_settings)
        months = [item["month"] for item in items]
        self.assertEqual(months, sorted(months))
        self.assertTrue(any(item["label"].startswith("Net worth ") for item in items))

    def test_fi_year_is_when_funds_reach_25x_spending(self):
        rows = build_projection(self.user)
        years = annual_rollup(rows)
        fi = insights.financial_independence(years)
        self.assertIsNotNone(fi["year"])
        year = next(y for y in years if y.year == fi["year"])
        self.assertGreaterEqual(year.investment_balance, insights._annual_spend(year) * 25)
        earlier = [y for y in years if y.year < fi["year"]]
        for y in earlier:
            self.assertLess(y.investment_balance, insights._annual_spend(y) * 25)


class HeatmapTests(TestCase):
    def test_levels_scale_with_surplus_and_mark_deficits_and_lumps(self):
        user = get_owner()
        make_planner(user, project_to_year=2026)
        make_salary(user, amount="100000")
        make_expense(user, amount="60000")
        make_one_time_expense(user, amount="150000", month=dt.date(2026, 6, 1))
        grid = insights.heatmap(build_projection(user))
        cells = grid["years"][0]["cells"]
        self.assertEqual(len(cells), 12)
        self.assertEqual(cells[0]["level"], "p4")          # best month
        self.assertTrue(cells[5]["level"].startswith("n"))  # June: 40k in, 210k out
        self.assertTrue(cells[5]["lumpy"])

    def test_partial_years_leave_empty_cells(self):
        user = get_owner()
        make_planner(user, project_to_year=2026, start_month=dt.date(2026, 10, 1))
        make_salary(user, month=dt.date(2026, 10, 1), amount="1000")
        cells = insights.heatmap(build_projection(user))["years"][0]["cells"]
        self.assertTrue(cells[0]["empty"])
        self.assertFalse(cells[9]["empty"])


class DashboardRenderTests(TestCase):
    def test_dashboard_with_everything_renders(self):
        user = get_owner()
        make_planner(user, project_to_year=2032)
        make_salary(user, amount="120000")
        make_expense(user, amount="40000")
        make_loan(user)
        make_holding(user, current_value=D("500000"))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        for text in ("Milestones ahead", "Surplus calendar", "Money in versus growth"):
            self.assertContains(response, text)
