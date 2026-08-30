"""Forms.

Two things worth knowing:

* Every "month" field renders as a native <input type="month">, so the user
  picks Jan 2026 rather than typing a day they do not care about.
* The forms that need to know when the projection starts (loans, insurance)
  take a `planner` argument -- they derive end dates from it.
"""

from __future__ import annotations

import datetime as dt

from django import forms

from .models import (
    Expense,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    PlannerSettings,
    RetirementAccount,
    SalaryChange,
)
from .services.amortization import LoanDerivationError, nper


class MonthInput(forms.DateInput):
    input_type = "month"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m")

    def format_value(self, value):
        if isinstance(value, dt.date):
            return value.strftime("%Y-%m")
        if isinstance(value, str) and len(value) == 10:
            return value[:7]
        return value


class MonthField(forms.DateField):
    """A DateField that only asks for a month; the day is always the 1st."""

    widget = MonthInput

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m", "%Y-%m-%d"])
        super().__init__(**kwargs)


class StyledForm(forms.ModelForm):
    """Adds the CSS hooks the templates expect, without a form library."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "checkbox")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "select")
            else:
                widget.attrs.setdefault("class", "input")
            if isinstance(widget, forms.NumberInput):
                widget.attrs.setdefault("step", "0.01")


class PlannerSettingsForm(StyledForm):
    start_month = MonthField(label="Projection starts", help_text="Month zero of every table and chart.")

    class Meta:
        model = PlannerSettings
        fields = [
            "start_month",
            "project_to_year",
            "expense_inflation_pct",
            "default_salary_hike_pct",
            "default_hike_month",
            "bank_balance_today",
            "bank_interest_pct",
            "default_investment_return_pct",
            "surplus_pool_today",
            "surplus_pool_return_pct",
            "ef_target_months",
            "ef_target_fixed_amount",
        ]

    def clean_project_to_year(self):
        # Deliberately permissive: the projection clamps out-of-range years
        # and explains itself, rather than blocking the user here.
        return self.cleaned_data["project_to_year"]


class SalaryChangeForm(StyledForm):
    effective_month = MonthField(label="Effective from")

    class Meta:
        model = SalaryChange
        fields = ["effective_month", "monthly_in_hand", "note"]


class IncomeExtraForm(StyledForm):
    class Meta:
        model = IncomeExtra
        fields = ["label", "annual_amount", "payout_month"]


class ExpenseForm(StyledForm):
    class Meta:
        model = Expense
        fields = ["name", "monthly_amount", "inflates"]


class PlannerAwareForm(StyledForm):
    """Base for forms that need the planner's start month to derive dates."""

    def __init__(self, *args, planner=None, **kwargs):
        self.planner = planner
        super().__init__(*args, **kwargs)

    @property
    def planner_start(self) -> dt.date:
        if self.planner:
            return self.planner.start_month
        return dt.date.today().replace(day=1)


class LoanForm(PlannerAwareForm):
    start_month = MonthField(label="Loan started")
    end_month_override = MonthField(label="End month override", required=False)

    class Meta:
        model = Loan
        fields = [
            "name",
            "start_month",
            "principal_outstanding_today",
            "annual_interest_pct",
            "monthly_emi",
            "tenure_months",
            "end_month_override",
        ]

    def clean(self):
        cleaned = super().clean()
        principal = cleaned.get("principal_outstanding_today")
        tenure = cleaned.get("tenure_months")
        override = cleaned.get("end_month_override")
        emi = cleaned.get("monthly_emi")
        rate_pct = cleaned.get("annual_interest_pct")

        # The "at least one of principal / tenure / end month" rule lives on
        # the model, which ModelForm validation already runs -- repeating it
        # here would show the user the same error twice.
        #
        # Catch an EMI that never clears the interest here, where the message
        # can point at the field, instead of letting the projection silently
        # run a loan forever.
        if principal is not None and emi and not tenure and not override:
            try:
                nper(
                    (rate_pct or 0) / 100 / 12 if rate_pct else 0,
                    emi,
                    principal,
                )
            except LoanDerivationError as exc:
                raise forms.ValidationError({"monthly_emi": str(exc)}) from exc
        return cleaned


class InvestmentHoldingForm(StyledForm):
    sip_start_month = MonthField(label="SIP starts", required=False)
    sip_end_month = MonthField(label="SIP ends", required=False)

    class Meta:
        model = InvestmentHolding
        fields = [
            "name",
            "category",
            "current_value",
            "monthly_sip",
            "sip_start_month",
            "sip_end_month",
            "annual_stepup_pct",
            "return_basis",
            "return_3y_pct",
            "return_5y_pct",
            "expected_return_pct",
        ]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("sip_start_month"), cleaned.get("sip_end_month")
        if start and end and end < start:
            self.add_error("sip_end_month", "The SIP end month cannot be before its start month.")

        # Selecting a basis you have not filled in would silently fall back to
        # a different rate than the one the dropdown claims -- say so instead.
        basis = cleaned.get("return_basis")
        required = {
            InvestmentHolding.BASIS_3Y: ("return_3y_pct", "3Y return"),
            InvestmentHolding.BASIS_5Y: ("return_5y_pct", "5Y return"),
        }.get(basis)
        if required and cleaned.get(required[0]) is None:
            self.add_error(
                required[0],
                f"You chose to project using the {required[1]}, so enter it here "
                f"(or switch 'Project using' to your own estimate).",
            )
        return cleaned


class RetirementAccountForm(StyledForm):
    start_month = MonthField(label="Contributions start", required=False)
    end_month = MonthField(label="Contributions stop", required=False)

    class Meta:
        model = RetirementAccount
        fields = [
            "name",
            "current_balance",
            "employee_monthly",
            "employer_monthly",
            "monthly_cap",
            "annual_return_pct",
            "start_month",
            "end_month",
        ]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_month"), cleaned.get("end_month")
        if start and end and end < start:
            self.add_error("end_month", "The end month cannot be before the start month.")
        return cleaned


class InsurancePolicyForm(PlannerAwareForm):
    start_month = MonthField(label="Policy starts", required=False)
    end_month = MonthField(label="Policy ends", required=False)

    class Meta:
        model = InsurancePolicy
        fields = [
            "name",
            "policy_type",
            "sum_assured",
            "premium_amount",
            "frequency",
            "premium_month",
            "term_years",
            "start_month",
            "end_month",
        ]

    def clean(self):
        cleaned = super().clean()
        # The whole point of the term field: enter "16 years" and never work
        # out the end date by hand. An explicit end month still wins.
        if cleaned.get("term_years") and not cleaned.get("end_month"):
            policy = InsurancePolicy(
                start_month=cleaned.get("start_month"),
                term_years=cleaned["term_years"],
            )
            policy.apply_term_years(self.planner_start)
            cleaned["end_month"] = policy.end_month
            self.instance.end_month = policy.end_month

        start, end = cleaned.get("start_month"), cleaned.get("end_month")
        if start and end and end < start:
            self.add_error("end_month", "The end month cannot be before the start month.")
        return cleaned


class ProjectToYearForm(forms.Form):
    """The one control that re-renders everything."""

    upto = forms.IntegerField(
        label="Project to year", min_value=1900, max_value=2200, required=False,
        widget=forms.NumberInput(attrs={"class": "input year-input", "step": "1"}),
    )
