"""Loan prepayment what-if."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from planner import prepay
from planner.middleware import get_owner

from .factories import JAN_2026, make_loan, make_planner

D = Decimal


class SimulateTests(TestCase):
    def test_zero_rate_loan_clears_in_balance_over_emi_months(self):
        result = prepay.simulate(D("100000"), D("0"), D("10000"), JAN_2026)
        self.assertEqual(result.months, 10)
        self.assertEqual(result.interest, D("0.00"))
        self.assertEqual(result.last_month, dt.date(2026, 10, 1))
        self.assertTrue(result.cleared)

    def test_extra_payments_save_interest_and_time(self):
        rate = D("12") / 100 / 12
        base = prepay.simulate(D("100000"), rate, D("10000"), JAN_2026)
        faster = prepay.simulate(D("100000"), rate, D("10000"), JAN_2026, extra=D("5000"))
        self.assertLess(faster.months, base.months)
        self.assertLess(faster.interest, base.interest)

    def test_a_lump_sum_lands_in_its_month(self):
        rate = D("0")
        result = prepay.simulate(D("100000"), rate, D("10000"), JAN_2026, lump=D("50000"), lump_month=dt.date(2026, 2, 1))
        self.assertEqual(result.months, 5)  # 10k, then 60k, then 30k left over three more EMIs
        self.assertEqual(result.balances[1], D("30000.00"))

    def test_final_payment_is_capped_at_what_is_owed(self):
        result = prepay.simulate(D("25000"), D("0"), D("10000"), JAN_2026)
        self.assertEqual(result.months, 3)
        self.assertEqual(result.paid, D("25000.00"))

    def test_an_emi_that_never_covers_interest_is_reported_not_looped(self):
        result = prepay.simulate(D("100000"), D("0.02"), D("500"), JAN_2026)
        self.assertFalse(result.cleared)


class LoanPrepayViewTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user, project_to_year=2030)
        self.loan = make_loan(self.user)

    def test_partial_and_detail_page_render(self):
        self.assertEqual(self.client.get(reverse("loan_detail", args=[self.loan.pk])).status_code, 200)
        response = self.client.get(reverse("loan_prepay", args=[self.loan.pk]), {"extra": "2000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Interest saved")

    def test_junk_inputs_are_treated_as_zero(self):
        response = self.client.get(reverse("loan_prepay", args=[self.loan.pk]), {"extra": "abc", "lump": "-5"})
        self.assertFalse(response.context["prepay"]["changed"])

    def test_other_users_loans_are_not_reachable(self):
        from .factories import make_user
        other = make_loan(make_user("someone-else"))
        self.assertEqual(self.client.get(reverse("loan_prepay", args=[other.pk])).status_code, 404)


class ResidualTests(TestCase):
    """An EMI and tenure that cannot clear the entered balance leave money standing."""

    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user, project_to_year=2040)

    def test_a_loan_that_clears_has_no_residual(self):
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                         monthly_emi=D("10000"), tenure_months=10)
        self.assertEqual(prepay.residual_after_last_emi(loan, self.planner.start_month), D("0"))

    def test_a_tenure_that_is_too_short_leaves_the_difference(self):
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                         monthly_emi=D("10000"), tenure_months=8)
        self.assertEqual(prepay.residual_after_last_emi(loan, self.planner.start_month), D("20000.00"))

    def test_it_matches_what_the_projection_leaves_standing(self):
        from planner.services.projection import build_projection
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("12"),
                         monthly_emi=D("8000"), tenure_months=10)
        rows = build_projection(self.user, upto_year=2030)
        engine = rows[-1].loan_by_id(loan.pk).balance
        self.assertEqual(prepay.residual_after_last_emi(loan, self.planner.start_month), engine)

    def test_dashboard_and_loans_page_both_say_so(self):
        from .factories import make_expense, make_salary
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="30000")
        make_loan(self.user, name="Car", principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=8)
        dash = self.client.get(reverse("dashboard"))
        self.assertContains(dash, "still owe money after the final EMI")
        self.assertContains(dash, "Car has")
        self.assertContains(self.client.get(reverse("loans")), "leave 20,000 unpaid")

    def test_a_short_horizon_does_not_cry_wolf(self):
        make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=8)
        self.planner.project_to_year = 2026
        self.planner.save()
        make_loan(self.user, name="Long", principal_outstanding_today=D("1000000"), monthly_emi=D("10000"),
                  annual_interest_pct=D("0"), tenure_months=100)
        response = self.client.get(reverse("dashboard"), {"upto": 2026})
        # Jan-Dec 2026 is 12 months: the 8-month loan's last EMI (Aug 2026) is inside it.
        names = [item["loan"].name for item in response.context["leftover_loans"]]
        self.assertIn("Loan", names)
        self.assertNotIn("Long", names)

    def test_loans_tile_does_not_promise_a_debt_free_date_it_cannot_deliver(self):
        make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=8)
        tiles = self.client.get(reverse("loans")).context["metrics"]
        self.assertEqual(tiles[2]["label"], "Last EMI")
        self.assertIn("still owed", tiles[2]["foot"])

    def test_loans_tile_says_debt_free_when_it_really_is(self):
        make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=10)
        self.assertEqual(self.client.get(reverse("loans")).context["metrics"][2]["label"], "Debt-free after")
