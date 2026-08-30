from django.contrib import admin

from .models import (
    Expense,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    PlannerSettings,
    SalaryChange,
)


@admin.register(PlannerSettings)
class PlannerSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "start_month", "project_to_year", "bank_balance_today")


@admin.register(SalaryChange)
class SalaryChangeAdmin(admin.ModelAdmin):
    list_display = ("effective_month", "monthly_in_hand", "note", "user")
    list_filter = ("user",)


@admin.register(IncomeExtra)
class IncomeExtraAdmin(admin.ModelAdmin):
    list_display = ("label", "annual_amount", "payout_month", "user")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("name", "monthly_amount", "inflates", "user")
    list_filter = ("inflates",)


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("name", "start_month", "monthly_emi", "annual_interest_pct",
                    "principal_outstanding_today", "tenure_months", "user")


@admin.register(InvestmentHolding)
class InvestmentHoldingAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "current_value", "monthly_sip",
                    "annual_stepup_pct", "expected_return_pct", "user")


@admin.register(InsurancePolicy)
class InsurancePolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "policy_type", "premium_amount", "frequency",
                    "premium_month", "start_month", "end_month", "user")
