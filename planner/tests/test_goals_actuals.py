"""Goals measured against the projection, and actuals logged against the plan."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from planner import actuals, goals
from planner.middleware import get_owner
from planner.models import ActualBalance, Goal

from .factories import JAN_2026, make_expense, make_planner, make_salary

D = Decimal


class GoalTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        # 60,000 a month surplus, no growth, no interest: bank = 60,000 x months.
        self.planner = make_planner(self.user, project_to_year=2028)
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="40000")

    def goal(self, name, amount, month):
        return Goal.objects.create(user=self.user, name=name, target_amount=D(amount), target_month=month)

    def test_a_goal_the_pot_covers_is_on_track(self):
        self.goal("Car", "300000", dt.date(2026, 6, 1))  # 6 months -> 360,000 in the bank
        result = goals.analyse(self.user, self.planner)
        card = result["cards"][0]
        self.assertEqual(card["status"], goals.ON_TRACK)
        self.assertEqual(card["projected"], D("360000.00"))
        self.assertEqual(card["shortfall"], D("0"))
        self.assertEqual(result["on_track"], 1)

    def test_a_shortfall_reports_the_gap_and_the_extra_sip_needed(self):
        self.goal("House", "500000", dt.date(2026, 6, 1))
        card = goals.analyse(self.user, self.planner)["cards"][0]
        self.assertEqual(card["status"], goals.SHORT)
        self.assertEqual(card["shortfall"], D("140000.00"))
        self.assertGreater(card["extra_sip"], 0)
        self.assertLess(card["pct"], 100)

    def test_earlier_goals_come_out_of_the_pot_for_later_ones(self):
        self.goal("First", "200000", dt.date(2026, 6, 1))
        self.goal("Second", "200000", dt.date(2026, 12, 1))  # pot 720,000 - 200,000 = 520,000
        cards = goals.analyse(self.user, self.planner)["cards"]
        self.assertEqual(cards[1]["earlier"], D("200000"))
        self.assertEqual(cards[1]["projected"], D("520000.00"))

    def test_past_and_far_future_goals_are_flagged_not_measured(self):
        self.goal("Old", "1000", dt.date(2025, 1, 1))
        self.goal("Far", "1000", dt.date(2040, 1, 1))
        # The projection only runs to the latest goal's year (capped at 30 years),
        # so a far goal inside the cap is measured; one beyond it is not.
        cards = goals.analyse(self.user, self.planner)["cards"]
        self.assertEqual(cards[0]["status"], goals.PAST)
        self.assertEqual(cards[1]["status"], goals.ON_TRACK)
        self.goal("Beyond", "1000", dt.date(2090, 1, 1))
        beyond = goals.analyse(self.user, self.planner)["cards"][-1]
        self.assertEqual(beyond["status"], goals.BEYOND)

    def test_no_goals_is_a_valid_state(self):
        self.assertEqual(goals.analyse(self.user, self.planner)["cards"], [])

    def test_goal_page_and_inline_create(self):
        self.assertEqual(self.client.get(reverse("goals")).status_code, 200)
        response = self.client.post(reverse("row_create", args=["goal"]), {
            "name": "Trip", "target_amount": "50000", "target_month": "2026-09", "note": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "metrics-changed")
        self.assertTrue(Goal.objects.filter(name="Trip").exists())
        self.assertContains(self.client.get(reverse("page_extra", args=["goals"])), "Trip")


class ActualsTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user, project_to_year=2027)
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="40000")  # plan: bank 60,000 a month, so net worth = 60,000 x months

    def log(self, month, bank, inv="0", **extra):
        return ActualBalance.objects.create(
            user=self.user, month=month, bank_balance=D(bank), investment_balance=D(inv), **extra
        )

    def test_drift_is_actual_minus_plan(self):
        self.log(dt.date(2026, 3, 1), "200000")  # plan after 3 months: 180,000
        result = actuals.analyse(self.user, self.planner)
        self.assertEqual(result["latest_drift"], D("20000.00"))
        self.assertEqual(result["latest_planned"], D("180000.00"))
        self.assertEqual(result["drift_pct"], D("11.1"))

    def test_blank_pf_and_loans_fall_back_to_the_plan(self):
        self.log(dt.date(2026, 3, 1), "180000")
        self.assertEqual(actuals.analyse(self.user, self.planner)["latest_drift"], D("0.00"))

    def test_a_month_outside_the_projection_has_no_drift(self):
        self.log(dt.date(2020, 1, 1), "1000")
        result = actuals.analyse(self.user, self.planner)
        self.assertIsNone(result["latest_drift"])

    def test_chart_needs_two_months(self):
        self.log(dt.date(2026, 3, 1), "180000")
        self.assertEqual(actuals.analyse(self.user, self.planner)["chart_json"], "")
        self.log(dt.date(2026, 5, 1), "300000")
        self.assertNotEqual(actuals.analyse(self.user, self.planner)["chart_json"], "")

    def test_one_check_in_per_month(self):
        self.log(dt.date(2026, 3, 1), "1")
        response = self.client.post(reverse("row_create", args=["actual"]), {
            "month": "2026-03", "bank_balance": "5", "investment_balance": "0", "note": "",
        })
        self.assertContains(response, "already logged this month")
        self.assertEqual(ActualBalance.objects.count(), 1)

    def test_actuals_page_renders_with_data(self):
        self.log(dt.date(2026, 3, 1), "200000")
        self.log(dt.date(2026, 4, 1), "260000")
        response = self.client.get(reverse("actuals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Drift")
        self.assertContains(response, "Plan versus reality")
