"""Small builders so each test reads as the scenario it is testing."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model

from planner.models import (
    Expense,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    OneTimeExpense,
    PlannerSettings,
    RetirementAccount,
    SalaryChange,
)

JAN_2026 = dt.date(2026, 1, 1)


def make_user(username="tester"):
    return get_user_model().objects.create(username=username)


def make_planner(user, **overrides):
    """A deliberately inert planner: no hike, no inflation, no interest.

    Tests switch on exactly the one behaviour they are checking, so an
    unexpected number can only have come from the feature under test.
    """
    defaults = dict(
        start_month=JAN_2026,
        project_to_year=2026,
        expense_inflation_pct=Decimal("0"),
        default_salary_hike_pct=Decimal("0"),
        default_hike_month=4,
        bank_balance_today=Decimal("0"),
        bank_interest_pct=Decimal("0"),
        default_investment_return_pct=Decimal("12"),
        ef_target_months=0,
        ef_target_fixed_amount=None,
    )
    defaults.update(overrides)
    return PlannerSettings.objects.create(user=user, **defaults)


def make_salary(user, month=JAN_2026, amount="100000", note=""):
    return SalaryChange.objects.create(
        user=user, effective_month=month, monthly_in_hand=Decimal(amount), note=note
    )


def make_expense(user, name="Living", amount="40000", inflates=False, frequency="monthly", due_month=1):
    return Expense.objects.create(
        user=user, name=name, amount=Decimal(amount), inflates=inflates,
        frequency=frequency, due_month=due_month,
    )


def make_one_time_expense(user, name="Phone", amount="50000", month=JAN_2026):
    return OneTimeExpense.objects.create(
        user=user, name=name, amount=Decimal(amount), month=month,
    )


def make_loan(user, name="Loan", **overrides):
    defaults = dict(
        start_month=JAN_2026,
        principal_outstanding_today=Decimal("100000"),
        annual_interest_pct=Decimal("12"),
        monthly_emi=Decimal("10000"),
        tenure_months=12,
        end_month_override=None,
    )
    defaults.update(overrides)
    return Loan.objects.create(user=user, name=name, **defaults)


def make_holding(user, name="Fund", **overrides):
    defaults = dict(
        category="Equity MF",
        current_value=Decimal("0"),
        monthly_sip=Decimal("10000"),
        sip_start_month=None,
        sip_end_month=None,
        annual_stepup_pct=Decimal("0"),
        expected_return_pct=Decimal("12"),
    )
    defaults.update(overrides)
    return InvestmentHolding.objects.create(user=user, name=name, **defaults)


def make_policy(user, name="Term Life", **overrides):
    defaults = dict(
        policy_type="Term",
        premium_amount=Decimal("12000"),
        frequency=InsurancePolicy.YEARLY,
        premium_month=1,
        start_month=None,
        end_month=None,
    )
    defaults.update(overrides)
    return InsurancePolicy.objects.create(user=user, name=name, **defaults)


def make_pf(user, name="EPF", **overrides):
    defaults = dict(
        current_balance=Decimal("0"),
        employee_monthly=Decimal("10000"),
        employer_monthly=Decimal("10000"),
        annual_return_pct=Decimal("12"),  # 1%/month, so tests stay checkable
        monthly_cap=None,
        start_month=None,
        end_month=None,
    )
    defaults.update(overrides)
    return RetirementAccount.objects.create(user=user, name=name, **defaults)


def make_bonus(user, label="Bonus", amount="120000", month=4, split_end_month=None,
               start_month=None, end_month=None):
    return IncomeExtra.objects.create(
        user=user, label=label, annual_amount=Decimal(amount),
        payout_month=month, payout_end_month=split_end_month,
        start_month=start_month, end_month=end_month,
    )
