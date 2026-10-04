"""Scenario overrides in the engine, and the what-if page built on them."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from planner.middleware import get_owner
from planner.services.projection import Scenario, build_projection
from planner.whatif import baseline_values, parse_scenario

from .factories import JAN_2026, make_expense, make_holding, make_planner, make_salary, make_user

D = Decimal


class ScenarioEngineTests(TestCase):
    def setUp(self):
        self.user = make_user()
        make_planner(self.user, project_to_year=2027, bank_balance_today=D("50000"))
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="40000")

    def test_an_empty_scenario_changes_nothing(self):
        base = build_projection(self.user)
        same = build_projection(self.user, scenario=Scenario())
        self.assertEqual([r.net_worth for r in base], [r.net_worth for r in same])

    def test_overrides_never_reach_the_database(self):
        build_projection(self.user, scenario=Scenario(expense_inflation_pct=D("20")))
        self.user.planner_settings.refresh_from_db()
        self.assertEqual(self.user.planner_settings.expense_inflation_pct, D("0"))

    def test_job_loss_removes_salary_and_bonus_for_those_months_only(self):
        rows = build_projection(
            self.user, scenario=Scenario(job_loss_start=dt.date(2026, 3, 1), job_loss_months=2)
        )
        by_month = {r.month.month: r for r in rows if r.month.year == 2026}
        self.assertEqual(by_month[2].salary, D("100000"))
        self.assertEqual(by_month[3].salary, D("0"))
        self.assertEqual(by_month[4].salary, D("0"))
        self.assertEqual(by_month[5].salary, D("100000"))
        # Living costs carry on, so those months run a deficit.
        self.assertEqual(by_month[3].net_surplus, D("-40000"))

    def test_market_drop_cuts_funds_but_not_capital(self):
        make_holding(self.user, current_value=D("100000"), monthly_sip=D("0"))
        base = {r.month: r for r in build_projection(self.user)}
        hit = {r.month: r for r in build_projection(
            self.user, scenario=Scenario(market_drop_month=dt.date(2026, 6, 1), market_drop_pct=D("50"))
        )}
        june = dt.date(2026, 6, 1)
        # Half the value, give or take a paisa of rounding.
        self.assertLessEqual(abs(hit[june].investment_balance - base[june].investment_balance / 2), D("0.01"))
        self.assertEqual(hit[june].invested_capital, base[june].invested_capital)
        self.assertLess(hit[june].investment_gains, base[june].investment_gains)

    def test_higher_inflation_lowers_net_worth(self):
        make_expense(self.user, name="Rent", amount="10000", inflates=True)
        base = build_projection(self.user)[-1].net_worth
        worse = build_projection(self.user, scenario=Scenario(expense_inflation_pct=D("15")))[-1].net_worth
        self.assertLess(worse, base)


class ParseScenarioTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.planner = make_planner(self.user, expense_inflation_pct=D("6"))
        self.base = baseline_values(self.user, self.planner)

    def test_values_at_baseline_are_not_overrides(self):
        scenario, _, changed = parse_scenario({"infl": "6", "ret": str(self.base["ret"])}, self.base)
        self.assertFalse(changed)
        self.assertIsNone(scenario.expense_inflation_pct)
        self.assertIsNone(scenario.investment_return_pct)

    def test_a_moved_slider_is_an_override_and_is_clamped(self):
        scenario, values, changed = parse_scenario({"infl": "99"}, self.base)
        self.assertTrue(changed)
        self.assertEqual(scenario.expense_inflation_pct, D("25"))
        self.assertEqual(values["infl"], D("25"))

    def test_junk_input_is_ignored(self):
        scenario, _, changed = parse_scenario({"infl": "abc", "jl_start": "nope", "jl_months": "x"}, self.base)
        self.assertFalse(changed)
        self.assertFalse(scenario.has_job_loss)


class WhatIfPageTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        make_planner(self.user, project_to_year=2028)
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="40000")

    def test_page_and_results_render(self):
        self.assertEqual(self.client.get(reverse("whatif")).status_code, 200)
        response = self.client.get(reverse("whatif_results"), {"infl": "12", "jl_start": "2026-06", "jl_months": "3"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Net worth, Dec 2028")
        self.assertTrue(response.context["changed"])

    def test_results_without_any_data(self):
        from planner.models import PlannerSettings
        PlannerSettings.objects.all().delete()
        self.assertEqual(self.client.get(reverse("whatif_results")).status_code, 200)
