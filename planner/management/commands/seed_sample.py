"""Load a realistic sample plan so the app has something to show on day one.

    python manage.py seed_sample          # fill in, refusing if data exists
    python manage.py seed_sample --reset  # wipe this user's rows first

Overwrite it with your own numbers through the UI -- nothing here is special.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from planner.middleware import get_owner
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
from planner.services.dates import add_months, month_start

D = Decimal


class Command(BaseCommand):
    help = "Create a realistic sample plan for the single owner account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Delete the owner's existing planner rows before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user = get_owner()
        models = [SalaryChange, IncomeExtra, Expense, OneTimeExpense, Loan, InvestmentHolding,
                  RetirementAccount, InsurancePolicy]

        existing = any(model.objects.filter(user=user).exists() for model in models)
        if existing and not options["reset"]:
            self.stdout.write(self.style.WARNING(
                "This account already has planner data. Re-run with --reset to replace it."
            ))
            return

        if options["reset"]:
            for model in models:
                model.objects.filter(user=user).delete()
            PlannerSettings.objects.filter(user=user).delete()

        start = month_start(dt.date.today())

        PlannerSettings.objects.update_or_create(
            user=user,
            defaults=dict(
                start_month=start,
                project_to_year=start.year + 15,
                expense_inflation_pct=D("6.00"),
                default_salary_hike_pct=D("8.00"),
                default_hike_month=4,
                bank_balance_today=D("450000.00"),
                bank_interest_pct=D("3.00"),
                default_investment_return_pct=D("12.00"),
                ef_target_months=6,
                ef_target_fixed_amount=None,
            ),
        )

        # Income: two past raises, so the lookup table has real history.
        SalaryChange.objects.create(
            user=user, effective_month=add_months(start, -28),
            monthly_in_hand=D("130000.00"), note="Joined current employer",
        )
        SalaryChange.objects.create(
            user=user, effective_month=add_months(start, -4),
            monthly_in_hand=D("185000.00"), note="Promotion",
        )
        IncomeExtra.objects.create(
            user=user, label="Annual bonus", annual_amount=D("200000.00"), payout_month=4,
        )

        for name, amount, inflates in [
            ("Rent", "28000.00", True),
            ("Groceries", "12000.00", True),
            ("Utilities and internet", "4500.00", True),
            ("Petrol and transport", "6000.00", True),
            ("Household help", "4000.00", True),
            ("Dining and leisure", "8000.00", True),
            ("Subscriptions", "1200.00", False),
        ]:
            Expense.objects.create(user=user, name=name, amount=D(amount), inflates=inflates)

        # Two non-monthly expenses, to show what a lumpy annual cost looks
        # like on the cashflow page instead of being smoothed into "Living".
        Expense.objects.create(
            user=user, name="Property tax", amount=D("18000.00"),
            frequency=Expense.YEARLY, due_month=3, inflates=True,
        )
        Expense.objects.create(
            user=user, name="Society maintenance", amount=D("9000.00"),
            frequency=Expense.HALF_YEARLY, due_month=1, inflates=True,
        )

        # A one-time purchase: a single transaction, added with its own
        # month, no frequency and no recurrence.
        OneTimeExpense.objects.create(
            user=user, name="New phone", amount=D("50000.00"), month=add_months(start, 8),
        )

        # Three loans of different shapes: one with an explicit tenure, one
        # long-running, and one where the tenure is left for the app to derive.
        Loan.objects.create(
            user=user, name="Home Loan", start_month=add_months(start, -54),
            principal_outstanding_today=D("4200000.00"), annual_interest_pct=D("8.60"),
            monthly_emi=D("38500.00"), tenure_months=240,
        )
        Loan.objects.create(
            user=user, name="Car Loan", start_month=add_months(start, -26),
            principal_outstanding_today=D("480000.00"), annual_interest_pct=D("9.50"),
            monthly_emi=D("15800.00"), tenure_months=60,
        )
        Loan.objects.create(
            user=user, name="Education Loan", start_month=add_months(start, -68),
            principal_outstanding_today=D("260000.00"), annual_interest_pct=D("10.50"),
            monthly_emi=D("9200.00"),  # tenure derived by NPER
        )

        InvestmentHolding.objects.create(
            user=user, name="Flexi Cap Fund", category="Equity MF",
            current_value=D("850000.00"), monthly_sip=D("15000.00"),
            annual_stepup_pct=D("10.00"),
            # Trailing figures recorded, but projected on a deliberately more
            # conservative own estimate -- 3Y/5Y are history, not a forecast.
            return_3y_pct=D("19.40"), return_5y_pct=D("22.10"),
            expected_return_pct=D("13.00"),
            return_basis=InvestmentHolding.BASIS_CUSTOM,
        )
        InvestmentHolding.objects.create(
            user=user, name="Nifty 50 Index Fund", category="Equity MF",
            current_value=D("420000.00"), monthly_sip=D("8000.00"),
            annual_stepup_pct=D("10.00"),
            return_3y_pct=D("14.60"), return_5y_pct=D("16.80"),
            expected_return_pct=D("12.00"),
            return_basis=InvestmentHolding.BASIS_CUSTOM,
        )
        InvestmentHolding.objects.create(
            user=user, name="Direct equity", category="Direct Equity",
            current_value=D("310000.00"), monthly_sip=D("0.00"),
            expected_return_pct=D("12.00"),
        )
        # A SIP that starts later and stops early -- the schedule the engine
        # has to switch on and off mid-projection.
        InvestmentHolding.objects.create(
            user=user, name="PPF", category="PPF",
            current_value=D("380000.00"), monthly_sip=D("5000.00"),
            sip_start_month=add_months(start, 6),
            sip_end_month=add_months(start, 6 + 12 * 9 - 1),
            expected_return_pct=D("7.10"),
        )

        # EPF on a basic of roughly 60,000: 12% each side, with the employer's
        # 8.33% EPS share left out of the corpus.
        RetirementAccount.objects.create(
            user=user, name="EPF",
            current_balance=D("1150000.00"),
            employee_monthly=D("1800.00"),
            employer_monthly=D("1800.00"),
            monthly_cap=D("3600.00"),  # 12% of the Rs 15,000 statutory ceiling, each side
            annual_return_pct=D("8.25"),
        )

        term = InsurancePolicy(
            user=user, name="Term Life", policy_type="Term/Life",
            sum_assured=D("10000000.00"), premium_amount=D("18500.00"),
            frequency=InsurancePolicy.YEARLY, premium_month=7,
            start_month=add_months(start, -16), term_years=30,
        )
        term.apply_term_years(start)  # fills in end_month, no date maths by hand
        term.save()

        InsurancePolicy.objects.create(
            user=user, name="Family Health Floater", policy_type="Health",
            sum_assured=D("1000000.00"), premium_amount=D("9800.00"),
            frequency=InsurancePolicy.HALF_YEARLY, premium_month=3,
        )
        InsurancePolicy.objects.create(
            user=user, name="Car Insurance", policy_type="Motor",
            premium_amount=D("14200.00"), frequency=InsurancePolicy.YEARLY, premium_month=11,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Sample plan seeded for '{user.username}', starting {start:%b %Y}. "
            f"Run the server and open / to see it."
        ))
