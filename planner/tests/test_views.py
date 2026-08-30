"""View tests: every page renders, and the HTMX row editing round-trips."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from planner.middleware import get_owner
from planner.models import (
    Expense,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    PlannerSettings,
    RetirementAccount,
)

from .factories import (
    make_expense,
    make_holding,
    make_loan,
    make_planner,
    make_policy,
    make_salary,
)

D = Decimal


class PageTests(TestCase):
    """The middleware pins requests to the owner account, so no login here."""

    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user, project_to_year=2030)
        make_salary(self.user, amount="120000")
        make_expense(self.user, name="Rent", amount="30000", inflates=True)
        make_loan(self.user, name="Car Loan")
        make_holding(self.user, name="Flexi Cap")
        make_policy(self.user, name="Term Life")

    def test_every_page_renders(self):
        for name in ["dashboard", "settings", "income", "expenses", "loans",
                     "investments", "retirement", "insurance", "cashflow", "summary"]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_dashboard_partial_recomputes_for_the_requested_year(self):
        response = self.client.get(reverse("dashboard_partial"), {"upto": 2035})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["horizon"].end_year, 2035)
        self.assertEqual(len(response.context["years"]), 10)

    def test_dashboard_clamps_a_year_outside_the_range_instead_of_erroring(self):
        response = self.client.get(reverse("dashboard_partial"), {"upto": 2200})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["horizon"].end_year, 2055)
        self.assertTrue(response.context["horizon"].notices)

    def test_a_junk_year_falls_back_to_the_saved_setting(self):
        response = self.client.get(reverse("dashboard_partial"), {"upto": "soon"})
        self.assertEqual(response.context["horizon"].end_year, 2030)

    def test_loan_detail_shows_the_amortisation_schedule(self):
        loan = Loan.objects.get(name="Car Loan")
        response = self.client.get(reverse("loan_detail", args=[loan.pk]))

        self.assertEqual(response.status_code, 200)
        schedule = response.context["schedule"]
        self.assertEqual(schedule[0]["month"], dt.date(2026, 1, 1))
        self.assertEqual(schedule[0]["opening"], D("100000.00"))
        self.assertEqual(schedule[0]["interest"], D("1000.00"))
        self.assertEqual(schedule[0]["principal"], D("9000.00"))
        self.assertEqual(schedule[0]["closing"], D("91000.00"))
        # The schedule stops at the payoff month, with no empty months
        # trailing behind it.
        self.assertEqual(schedule[-1]["month"], dt.date(2026, 11, 1))
        self.assertEqual(schedule[-1]["closing"], D("0.00"))
        self.assertEqual(response.context["residual"], D("0.00"))

    def test_cashflow_csv_export(self):
        response = self.client.get(reverse("cashflow_csv"), {"upto": 2027})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment;", response["Content-Disposition"])

        lines = response.content.decode().strip().splitlines()
        self.assertEqual(len(lines), 1 + 24)  # header + two years of months
        self.assertIn("Car Loan EMI", lines[0])
        self.assertIn("PF balance", lines[0])
        self.assertIn("Net worth", lines[0])
        self.assertTrue(lines[1].startswith("2026-01"))

    def test_cashflow_shows_balances_only_by_default(self):
        response = self.client.get(reverse("cashflow"))

        self.assertEqual(response.context["groups"], {"balances"})
        self.assertTrue(response.context["show_balances"])
        self.assertFalse(response.context["show_income"])
        self.assertFalse(response.context["show_spending"])
        body = response.content.decode()
        self.assertIn(">Net worth", body)
        self.assertNotIn(">Salary", body)

    def test_cashflow_column_groups_can_be_switched_on(self):
        response = self.client.get(reverse("cashflow"), {"v": "1", "cols": ["income", "spending"]})

        self.assertEqual(response.context["groups"], {"income", "spending"})
        body = response.content.decode()
        self.assertIn(">Salary", body)
        self.assertIn(">SIP", body)
        self.assertNotIn(">Pool", body)

    def test_unticking_every_group_is_not_read_as_the_default(self):
        # The v=1 marker distinguishes "submitted with none ticked" from
        # "never touched the form".
        response = self.client.get(reverse("cashflow"), {"v": "1"})

        self.assertEqual(response.context["groups"], set())
        self.assertNotIn(">Bank", response.content.decode())

    def test_an_unknown_group_is_ignored(self):
        response = self.client.get(reverse("cashflow"), {"v": "1", "cols": ["income", "nonsense"]})
        self.assertEqual(response.context["groups"], {"income"})

    def test_summary_collapses_the_outflow_columns_by_default(self):
        response = self.client.get(reverse("summary"))
        self.assertFalse(response.context["show_breakdown"])
        # Match the column header specifically -- "Insurance" also appears in
        # the nav on every page.
        body = response.content.decode()
        self.assertIn('<th class="num">Out', body)
        self.assertNotIn('<th class="num">Insurance', body)

        detailed = self.client.get(reverse("summary"), {"detail": "1"})
        self.assertTrue(detailed.context["show_breakdown"])
        self.assertIn('<th class="num">Insurance', detailed.content.decode())

    def test_info_indicators_explain_the_columns(self):
        body = self.client.get(reverse("cashflow")).content.decode()
        self.assertIn('class="info"', body)
        self.assertIn("capped at the EF target", body)
        # Accessible to keyboard and screen readers, not hover-only.
        self.assertIn('tabindex="0"', body)
        self.assertIn("aria-label=", body)

    def test_no_template_comment_leaks_into_any_page(self):
        # Django's {# #} comments are single-line only; a multi-line one is
        # emitted verbatim, and inside <head> the browser hoists it into the
        # body as visible text.
        for name in ["dashboard", "settings", "income", "expenses", "loans",
                     "investments", "retirement", "insurance", "cashflow", "summary"]:
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertNotIn("{#", body)
                self.assertNotIn("{%", body)

    def test_cashflow_year_filter_and_pagination(self):
        response = self.client.get(reverse("cashflow"), {"year": 2028})
        self.assertEqual(response.context["year_filter"], 2028)
        self.assertEqual(response.context["total_rows"], 12)

    def test_settings_form_saves(self):
        response = self.client.post(reverse("settings"), {
            "start_month": "2026-01",
            "project_to_year": 2032,
            "expense_inflation_pct": "7.00",
            "default_salary_hike_pct": "9.00",
            "default_hike_month": 4,
            "bank_balance_today": "250000.00",
            "bank_interest_pct": "3.50",
            "default_investment_return_pct": "12.50",
            "surplus_pool_today": "0.00",
            "surplus_pool_return_pct": "6.00",
            "ef_target_months": 6,
            "ef_target_fixed_amount": "",
        })

        self.assertRedirects(response, reverse("settings"))
        self.planner.refresh_from_db()
        self.assertEqual(self.planner.project_to_year, 2032)
        self.assertEqual(self.planner.expense_inflation_pct, D("7.00"))
        self.assertEqual(self.planner.surplus_pool_return_pct, D("6.00"))


class RowEditingTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        self.planner = make_planner(self.user)

    def test_create_a_row_returns_the_refreshed_table(self):
        response = self.client.post(reverse("row_create", args=["expense"]), {
            "name": "Groceries", "monthly_amount": "12000", "inflates": "on",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Groceries")
        expense = Expense.objects.get(name="Groceries")
        self.assertEqual(expense.user, self.user)
        self.assertTrue(expense.inflates)

    def test_edit_and_delete_a_row(self):
        expense = make_expense(self.user, name="Petrol", amount="5000")

        form = self.client.get(reverse("row_edit", args=["expense", expense.pk]))
        self.assertContains(form, "Petrol")

        self.client.post(reverse("row_update", args=["expense", expense.pk]), {
            "name": "Petrol", "monthly_amount": "6500", "inflates": "on",
        })
        expense.refresh_from_db()
        self.assertEqual(expense.monthly_amount, D("6500.00"))

        response = self.client.post(reverse("row_delete", args=["expense", expense.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())

    def test_deleting_a_loan_leaves_the_projection_healthy(self):
        loan = make_loan(self.user)
        self.client.post(reverse("row_delete", args=["loan", loan.pk]))

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"].opening_loan_balance, D("0.00"))

    def test_an_underdetermined_loan_is_rejected_with_an_explanation(self):
        response = self.client.post(reverse("row_create", args=["loan"]), {
            "name": "Mystery Loan",
            "start_month": "2026-01",
            "principal_outstanding_today": "",
            "annual_interest_pct": "10",
            "monthly_emi": "5000",
            "tenure_months": "",
            "end_month_override": "",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one of")
        # HTMX is told to put the errors back on the form, not over the table.
        self.assertEqual(response["HX-Retarget"], "#form-row-loan")
        self.assertFalse(Loan.objects.filter(name="Mystery Loan").exists())

    def test_an_emi_that_never_clears_the_interest_is_rejected(self):
        response = self.client.post(reverse("row_create", args=["loan"]), {
            "name": "Impossible Loan",
            "start_month": "2026-01",
            "principal_outstanding_today": "1000000",
            "annual_interest_pct": "12",
            "monthly_emi": "5000",  # 10,000 of interest a month
            "tenure_months": "",
            "end_month_override": "",
        })

        self.assertContains(response, "never be repaid")
        self.assertFalse(Loan.objects.filter(name="Impossible Loan").exists())

    def test_a_sip_ending_before_it_starts_is_rejected(self):
        response = self.client.post(reverse("row_create", args=["investment"]), {
            "name": "Backwards Fund", "category": "Equity MF",
            "current_value": "1000", "monthly_sip": "5000",
            "sip_start_month": "2030-01", "sip_end_month": "2028-01",
            "annual_stepup_pct": "0", "expected_return_pct": "12",
        })

        self.assertContains(response, "cannot be before its start month")
        self.assertFalse(InvestmentHolding.objects.filter(name="Backwards Fund").exists())

    def test_term_years_fills_in_the_end_month_on_save(self):
        response = self.client.post(reverse("row_create", args=["insurance"]), {
            "name": "Term Cover", "policy_type": "Term",
            "sum_assured": "10000000", "premium_amount": "18500",
            "frequency": InsurancePolicy.YEARLY, "premium_month": 4,
            "term_years": "16", "start_month": "2024-04", "end_month": "",
        })

        self.assertEqual(response.status_code, 200)
        policy = InsurancePolicy.objects.get(name="Term Cover")
        self.assertEqual(policy.end_month, dt.date(2040, 3, 1))

    def test_choosing_a_return_basis_you_have_not_filled_in_is_rejected(self):
        response = self.client.post(reverse("row_create", args=["investment"]), {
            "name": "Flexi Cap", "category": "Equity MF",
            "current_value": "100000", "monthly_sip": "10000",
            "sip_start_month": "", "sip_end_month": "", "annual_stepup_pct": "10",
            "return_basis": InvestmentHolding.BASIS_5Y,
            "return_3y_pct": "18", "return_5y_pct": "", "expected_return_pct": "12",
        })

        self.assertContains(response, "so enter it here")
        self.assertFalse(InvestmentHolding.objects.filter(name="Flexi Cap").exists())

    def test_a_holding_projects_at_its_chosen_trailing_return(self):
        self.client.post(reverse("row_create", args=["investment"]), {
            "name": "Flexi Cap", "category": "Equity MF",
            "current_value": "100000", "monthly_sip": "10000",
            "sip_start_month": "", "sip_end_month": "", "annual_stepup_pct": "10",
            "return_basis": InvestmentHolding.BASIS_5Y,
            "return_3y_pct": "18", "return_5y_pct": "22", "expected_return_pct": "12",
        })

        holding = InvestmentHolding.objects.get(name="Flexi Cap")
        self.assertEqual(holding.effective_return_pct(D("12")), D("22.00"))
        self.assertEqual(holding.return_basis_label(D("12")), "5-year trailing return")

    def test_create_a_retirement_account(self):
        response = self.client.post(reverse("row_create", args=["retirement"]), {
            "name": "EPF", "current_balance": "1150000",
            "employee_monthly": "7200", "employer_monthly": "2200",
            "annual_return_pct": "8.25", "start_month": "", "end_month": "",
        })

        self.assertEqual(response.status_code, 200)
        account = RetirementAccount.objects.get(name="EPF")
        self.assertEqual(account.user, self.user)
        self.assertEqual(account.monthly_total, D("9400.00"))

    def test_a_retirement_account_ending_before_it_starts_is_rejected(self):
        response = self.client.post(reverse("row_create", args=["retirement"]), {
            "name": "Backwards EPF", "current_balance": "0",
            "employee_monthly": "1000", "employer_monthly": "1000",
            "annual_return_pct": "8.25",
            "start_month": "2030-01", "end_month": "2028-01",
        })

        self.assertContains(response, "cannot be before the start month")
        self.assertFalse(RetirementAccount.objects.filter(name="Backwards EPF").exists())

    def test_a_policy_ending_before_it_starts_is_rejected(self):
        response = self.client.post(reverse("row_create", args=["insurance"]), {
            "name": "Backwards Policy", "policy_type": "Health",
            "sum_assured": "", "premium_amount": "5000",
            "frequency": InsurancePolicy.YEARLY, "premium_month": 4,
            "term_years": "", "start_month": "2030-01", "end_month": "2028-01",
        })

        self.assertContains(response, "cannot be before the start month")
        self.assertFalse(InsurancePolicy.objects.filter(name="Backwards Policy").exists())


class FirstRunTests(TestCase):
    def test_the_dashboard_works_before_anything_is_entered(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_data"])
        self.assertContains(response, "Nothing to project yet")
        # Settings are created on first visit rather than 500-ing.
        self.assertTrue(PlannerSettings.objects.filter(user=get_owner()).exists())

    def test_data_pages_work_before_anything_is_entered(self):
        for name in ["income", "expenses", "loans", "investments", "insurance"]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
