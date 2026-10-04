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


class FinalEmiTests(TestCase):
    """When the entered EMI and tenure do not clear the balance, the last EMI does."""

    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user, project_to_year=2040)

    def test_a_loan_that_clears_on_schedule_has_an_ordinary_last_emi(self):
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                         monthly_emi=D("10000"), tenure_months=10)
        self.assertIsNone(prepay.final_emi(loan, self.planner.start_month))

    def test_a_tenure_that_is_too_short_makes_the_last_emi_bigger(self):
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                         monthly_emi=D("10000"), tenure_months=8)
        payment, extra = prepay.final_emi(loan, self.planner.start_month)
        self.assertEqual(payment, D("30000.00"))
        self.assertEqual(extra, D("20000.00"))

    def test_it_matches_what_the_projection_charges_in_the_last_month(self):
        from planner.services.projection import build_projection
        loan = make_loan(self.user, principal_outstanding_today=D("100000"), annual_interest_pct=D("12"),
                         monthly_emi=D("8000"), tenure_months=10)
        rows = build_projection(self.user, upto_year=2030)
        payment, _ = prepay.final_emi(loan, self.planner.start_month)
        self.assertEqual(rows[9].loan_by_id(loan.pk).emi, payment)
        self.assertEqual(rows[9].loan_by_id(loan.pk).balance, D("0.00"))

    def test_the_balance_is_zero_for_ever_after_and_no_warning_banner_is_shown(self):
        from .factories import make_expense, make_salary
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="30000")
        make_loan(self.user, name="Car", principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=8)
        response = self.client.get(reverse("dashboard"), {"upto": 2035})
        self.assertEqual(response.context["rows"][-1].total_loan_balance, D("0.00"))
        self.assertNotContains(response, "still owe money")
        self.assertEqual(response.context["summary"].debt_free_month, dt.date(2026, 9, 1))

    def test_the_loans_page_explains_the_bigger_last_emi(self):
        make_loan(self.user, name="Car", principal_outstanding_today=D("100000"), annual_interest_pct=D("0"),
                  monthly_emi=D("10000"), tenure_months=8)
        page = self.client.get(reverse("loans"))
        self.assertContains(page, "final EMI in Aug 2026 is 30,000")
        self.assertContains(page, "20,000 more than usual")
        self.assertEqual(page.context["metrics"][2]["label"], "Debt-free after")
