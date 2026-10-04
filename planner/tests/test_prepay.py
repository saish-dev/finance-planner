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
