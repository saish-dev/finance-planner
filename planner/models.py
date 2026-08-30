"""Data model for the planner.

Every table carries `user` so this single-user app becomes multi-user without
a schema change. Every money column is Decimal(14, 2) -- never a float.
Every "month" column is a DateField normalised to the 1st of the month.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import models

from .services.amortization import LoanDerivationError, nper, pv_of_annuity
from .services.dates import add_months, format_month, month_diff, month_start
from .services.money import monthly_rate

MONEY = {"max_digits": 14, "decimal_places": 2}
PERCENT = {"max_digits": 6, "decimal_places": 2}

MONTH_CHOICES = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"),
    (5, "May"), (6, "June"), (7, "July"), (8, "August"),
    (9, "September"), (10, "October"), (11, "November"), (12, "December"),
]

MAX_PROJECTION_YEARS = 30


class UserOwned(models.Model):
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True


class PlannerSettings(UserOwned):
    """The single row of global assumptions driving the whole projection."""

    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planner_settings",
    )
    start_month = models.DateField(
        default=dt.date.today,
        help_text="Month zero of the projection. Stored as the 1st of the month.",
    )
    project_to_year = models.PositiveIntegerField(
        default=2040,
        help_text="Project through December of this year.",
    )
    expense_inflation_pct = models.DecimalField(
        default=Decimal("6.00"), verbose_name="Expense inflation % p.a.", **PERCENT
    )
    default_salary_hike_pct = models.DecimalField(
        default=Decimal("8.00"),
        verbose_name="Default salary hike % p.a.",
        help_text="Applied in the hike month of any year with no explicit salary change.",
        **PERCENT,
    )
    default_hike_month = models.IntegerField(
        default=4, choices=MONTH_CHOICES, verbose_name="Default hike month"
    )
    bank_balance_today = models.DecimalField(default=Decimal("0.00"), **MONEY)
    bank_interest_pct = models.DecimalField(
        default=Decimal("3.00"),
        verbose_name="Bank interest % p.a.",
        help_text="Credited monthly at one twelfth of this rate.",
        **PERCENT,
    )
    default_investment_return_pct = models.DecimalField(
        default=Decimal("12.00"),
        verbose_name="Default investment return % p.a.",
        help_text="Used for any holding that does not set its own expected return.",
        **PERCENT,
    )
    surplus_pool_today = models.DecimalField(
        default=Decimal("0.00"),
        verbose_name="Surplus pool today",
        help_text="Un-earmarked savings you already hold that are neither your bank "
                  "buffer nor a fund. Leave at zero if you have none.",
        **MONEY,
    )
    surplus_pool_return_pct = models.DecimalField(
        default=Decimal("6.00"),
        verbose_name="Surplus pool return % p.a.",
        help_text="What the swept surplus earns while it sits unallocated -- roughly a "
                  "liquid or arbitrage fund. Kept separate from your funds' returns.",
        **PERCENT,
    )
    ef_target_months = models.PositiveIntegerField(
        default=6,
        verbose_name="Emergency fund target (months)",
        help_text="Months of essential spending: living expenses + insurance + EMIs.",
    )
    ef_target_fixed_amount = models.DecimalField(
        null=True, blank=True,
        verbose_name="Emergency fund fixed target",
        help_text="Optional. If set, this overrides the months-based target.",
        **MONEY,
    )

    class Meta:
        verbose_name = "planner settings"
        verbose_name_plural = "planner settings"

    def __str__(self):
        return f"Settings from {format_month(self.start_month)} to {self.project_to_year}"

    def save(self, *args, **kwargs):
        self.start_month = month_start(self.start_month)
        super().save(*args, **kwargs)

    @property
    def max_year(self) -> int:
        return self.start_month.year + MAX_PROJECTION_YEARS - 1


class SalaryChange(UserOwned):
    """A raise, promotion or job switch. Rows may be entered in any order."""

    effective_month = models.DateField(help_text="Salary changes from this month onward.")
    monthly_in_hand = models.DecimalField(**MONEY)
    note = models.CharField(max_length=120, blank=True, help_text="Promotion, switched company, ...")

    class Meta:
        ordering = ["effective_month"]

    def __str__(self):
        return f"{format_month(self.effective_month)}: {self.monthly_in_hand}"

    def save(self, *args, **kwargs):
        self.effective_month = month_start(self.effective_month)
        super().save(*args, **kwargs)


class IncomeExtra(UserOwned):
    """A bonus or other variable pay stream, paid once a year."""

    label = models.CharField(max_length=120)
    annual_amount = models.DecimalField(**MONEY)
    payout_month = models.IntegerField(choices=MONTH_CHOICES, default=4)

    class Meta:
        ordering = ["payout_month", "label"]
        verbose_name_plural = "income extras"

    def __str__(self):
        return f"{self.label} ({self.get_payout_month_display()})"


class Expense(UserOwned):
    """A recurring living expense.

    EMIs, SIPs and insurance premiums do NOT belong here -- they have their own
    tables and their own cashflow columns, so putting them here double-counts.
    """

    name = models.CharField(max_length=120)
    monthly_amount = models.DecimalField(**MONEY)
    inflates = models.BooleanField(
        default=True,
        help_text="Grow by the planner's inflation rate every 12 months from the projection start.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}: {self.monthly_amount}/mo"


class Loan(UserOwned):
    """Any number of loans -- car, personal, home, education, whatever."""

    name = models.CharField(max_length=120)
    start_month = models.DateField(
        help_text="Month the loan began. May be in the past relative to the projection start."
    )
    principal_outstanding_today = models.DecimalField(
        null=True, blank=True,
        verbose_name="Principal outstanding at projection start",
        help_text="Outstanding as of the planner's start month, not at loan origination. "
                  "Leave blank to back-solve it from the EMI, rate and tenure.",
        **MONEY,
    )
    annual_interest_pct = models.DecimalField(null=True, blank=True, verbose_name="Interest % p.a.", **PERCENT)
    monthly_emi = models.DecimalField(verbose_name="Monthly EMI", **MONEY)
    tenure_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Leave blank to derive from principal, rate and EMI.",
    )
    end_month_override = models.DateField(
        null=True, blank=True,
        help_text="Optional. If set, this wins over tenure and every derived end date.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.start_month = month_start(self.start_month)
        if self.end_month_override:
            self.end_month_override = month_start(self.end_month_override)
        super().save(*args, **kwargs)

    def clean(self):
        if self.monthly_emi is not None and self.monthly_emi <= 0:
            raise ValidationError({"monthly_emi": "EMI must be greater than zero."})
        # Without at least one of these the loan is underdetermined: the
        # principal anchors the balance, the tenure/end anchors the last EMI.
        if self.principal_outstanding_today is None and not self.tenure_months and not self.end_month_override:
            raise ValidationError(
                "Enter at least one of: principal outstanding, tenure in months, or an "
                "end month override. Without one of them there is no way to work out "
                "when this loan ends."
            )
        if self.end_month_override and self.start_month and self.end_month_override < month_start(self.start_month):
            raise ValidationError({"end_month_override": "End month cannot be before the loan's start month."})

    # -- derivation ------------------------------------------------------
    # Mirrors the spreadsheet's chain. Kept here (not in views or templates)
    # so the loans list, the detail page and the projection engine all agree.

    @property
    def rate_per_month(self) -> Decimal:
        return monthly_rate(self.annual_interest_pct)

    def derived_tenure_months(self) -> int | None:
        """EMIs remaining, derived via NPER. None when it cannot be derived."""
        if self.tenure_months:
            return int(self.tenure_months)
        if self.principal_outstanding_today is not None:
            # The principal is stated as of the projection start, so NPER gives
            # the EMIs remaining from there -- not the loan's original tenure.
            try:
                return nper(self.rate_per_month, self.monthly_emi, self.principal_outstanding_today)
            except LoanDerivationError:
                return None
        return None

    def last_emi_month(self, planner_start: dt.date) -> dt.date | None:
        """The final month an EMI is charged."""
        if self.end_month_override:
            return self.end_month_override
        if self.tenure_months:
            return add_months(self.start_month, int(self.tenure_months) - 1)
        remaining = self.derived_tenure_months()
        if remaining is None:
            return None
        anchor = self.seed_month(planner_start)
        return add_months(anchor, remaining - 1) if remaining > 0 else anchor

    def months_remaining_at(self, planner_start: dt.date) -> int:
        """EMIs still due from the projection start (or the loan's own start)."""
        last = self.last_emi_month(planner_start)
        if last is None:
            return 0
        return max(0, month_diff(last, self.seed_month(planner_start)) + 1)

    def opening_balance(self, planner_start: dt.date) -> Decimal:
        """Balance the projection seeds this loan with.

        Either what the user typed, or the present value of the EMIs still to
        run -- PV(rate/12, months_remaining, -emi).
        """
        if self.principal_outstanding_today is not None:
            return Decimal(self.principal_outstanding_today)
        return pv_of_annuity(self.rate_per_month, self.months_remaining_at(planner_start), self.monthly_emi)

    def seed_month(self, planner_start: dt.date) -> dt.date:
        """First month this loan's balance appears in the projection."""
        return max(self.start_month, month_start(planner_start))


class InvestmentHolding(UserOwned):
    """A mutual fund, stock, PPF -- anything that grows and may take a SIP."""

    name = models.CharField(max_length=120)
    category = models.CharField(
        max_length=60, blank=True, help_text="Equity MF, Direct Equity, Debt Fund, PPF, ..."
    )
    current_value = models.DecimalField(default=Decimal("0.00"), **MONEY)
    monthly_sip = models.DecimalField(default=Decimal("0.00"), verbose_name="Monthly SIP", **MONEY)
    sip_start_month = models.DateField(
        null=True, blank=True, help_text="Blank means the SIP is already running at the projection start."
    )
    sip_end_month = models.DateField(null=True, blank=True, help_text="Blank means the SIP continues indefinitely.")
    annual_stepup_pct = models.DecimalField(
        default=Decimal("0.00"),
        verbose_name="Annual SIP step-up %",
        help_text="The SIP grows by this much every 12 months from THIS fund's own SIP start month.",
        **PERCENT,
    )
    BASIS_CUSTOM = "custom"
    BASIS_3Y = "3y"
    BASIS_5Y = "5y"
    BASIS_CHOICES = [
        (BASIS_CUSTOM, "My own estimate"),
        (BASIS_3Y, "3-year trailing return"),
        (BASIS_5Y, "5-year trailing return"),
    ]

    return_basis = models.CharField(
        max_length=10, choices=BASIS_CHOICES, default=BASIS_CUSTOM,
        verbose_name="Project using",
        help_text="Which of the three figures below actually drives the projection.",
    )
    return_3y_pct = models.DecimalField(
        null=True, blank=True,
        verbose_name="3Y return % p.a.",
        help_text="The 3-year annualised (CAGR) figure from the fund's page.",
        **PERCENT,
    )
    return_5y_pct = models.DecimalField(
        null=True, blank=True,
        verbose_name="5Y return % p.a.",
        help_text="The 5-year annualised (CAGR) figure from the fund's page.",
        **PERCENT,
    )
    expected_return_pct = models.DecimalField(
        null=True, blank=True,
        verbose_name="Own estimate % p.a.",
        help_text="Blank falls back to the planner's default return.",
        **PERCENT,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.sip_start_month:
            self.sip_start_month = month_start(self.sip_start_month)
        if self.sip_end_month:
            self.sip_end_month = month_start(self.sip_end_month)
        super().save(*args, **kwargs)

    def clean(self):
        if self.sip_start_month and self.sip_end_month and self.sip_end_month < self.sip_start_month:
            raise ValidationError({"sip_end_month": "The SIP end month cannot be before its start month."})

    def effective_sip_start(self, planner_start: dt.date) -> dt.date:
        """Blank start means 'already running', i.e. from the projection start.

        This is also the step-up anchor -- per fund, never the global start.
        """
        return month_start(self.sip_start_month or planner_start)

    def effective_return_pct(self, default_pct: Decimal) -> Decimal:
        """The rate the projection actually compounds this holding at.

        Trailing 3Y/5Y figures are applied flat for the whole horizon, exactly
        as entered -- no taper. That is a deliberate choice: a rate you can
        read off the fund page and check by hand beats one the app quietly
        adjusts. It does mean a 22% five-year number will project 22% for
        fifteen years, so the basis is surfaced in the Investments table.

        Falls back rather than raising if the chosen basis was left blank, so
        a half-entered row can never break the projection.
        """
        chosen = {
            self.BASIS_3Y: self.return_3y_pct,
            self.BASIS_5Y: self.return_5y_pct,
            self.BASIS_CUSTOM: self.expected_return_pct,
        }.get(self.return_basis)
        if chosen is not None:
            return chosen
        if self.expected_return_pct is not None:
            return self.expected_return_pct
        return default_pct

    def return_basis_label(self, default_pct: Decimal) -> str:
        """Where the rate above came from -- shown next to it in the table."""
        chosen = {
            self.BASIS_3Y: self.return_3y_pct,
            self.BASIS_5Y: self.return_5y_pct,
            self.BASIS_CUSTOM: self.expected_return_pct,
        }.get(self.return_basis)
        if chosen is not None:
            return self.get_return_basis_display()
        if self.expected_return_pct is not None:
            return f"{self.get_return_basis_display()} is blank; using your own estimate"
        return "Planner default"


class RetirementAccount(UserOwned):
    """EPF and anything else that accumulates outside your in-hand pay.

    Deliberately NOT an InvestmentHolding, for two reasons:

    * The employee contribution is deducted before the salary reaches you, and
      the employer's share never touches your bank account at all. Neither one
      may be subtracted from the monthly surplus the way a SIP is -- your
      `monthly_in_hand` is already net of the employee cut, so charging it
      again would count it twice.
    * The corpus is locked. It counts towards net worth but can never be sold
      to cover a bad month, and it does not count towards the emergency fund.
    """

    name = models.CharField(max_length=120, default="EPF")
    current_balance = models.DecimalField(
        default=Decimal("0.00"),
        verbose_name="Balance today",
        help_text="The corpus as of the projection start month.",
        **MONEY,
    )
    employee_monthly = models.DecimalField(
        default=Decimal("0.00"),
        verbose_name="Employee contribution / month",
        help_text="Already deducted from your in-hand salary, so it is not charged again.",
        **MONEY,
    )
    employer_monthly = models.DecimalField(
        default=Decimal("0.00"),
        verbose_name="Employer contribution / month",
        help_text="Never passes through your bank account; it only grows the corpus.",
        **MONEY,
    )
    annual_return_pct = models.DecimalField(
        default=Decimal("8.25"),
        verbose_name="Interest % p.a.",
        help_text="The EPF rate, credited monthly at one twelfth.",
        **PERCENT,
    )
    monthly_cap = models.DecimalField(
        null=True, blank=True,
        verbose_name="Monthly contribution cap",
        help_text="Optional ceiling on employee + employer together. EPF on the statutory "
                  "wage ceiling is 12% of Rs 15,000 each side, so 3600 in total. Leave blank "
                  "for no cap. Contributions track your salary until they hit this.",
        **MONEY,
    )
    start_month = models.DateField(
        null=True, blank=True,
        help_text="Blank means contributions are already running at the projection start.",
    )
    end_month = models.DateField(
        null=True, blank=True,
        help_text="Blank means contributions continue. Set this to the month you expect to "
                  "stop contributing; the corpus keeps compounding either way.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.start_month:
            self.start_month = month_start(self.start_month)
        if self.end_month:
            self.end_month = month_start(self.end_month)
        super().save(*args, **kwargs)

    def clean(self):
        if self.start_month and self.end_month and self.end_month < self.start_month:
            raise ValidationError({"end_month": "The end month cannot be before the start month."})

    @property
    def monthly_total(self) -> Decimal:
        return Decimal(self.employee_monthly or 0) + Decimal(self.employer_monthly or 0)

    @property
    def capped_monthly_total(self) -> Decimal:
        """What actually goes in this month, once the cap is applied."""
        if self.monthly_cap is not None:
            return min(self.monthly_total, Decimal(self.monthly_cap))
        return self.monthly_total

    def effective_start(self, planner_start: dt.date) -> dt.date:
        """Blank start means 'already running', i.e. from the projection start.

        This is also the anchor the salary-tracking ratio is measured from.
        """
        return month_start(self.start_month or planner_start)


class InsurancePolicy(UserOwned):
    """A policy whose premium is charged in the exact month it falls due."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"
    FREQUENCY_CHOICES = [
        (MONTHLY, "Monthly"),
        (QUARTERLY, "Quarterly"),
        (HALF_YEARLY, "Half-Yearly"),
        (YEARLY, "Yearly"),
    ]
    FREQUENCY_MONTHS = {MONTHLY: 1, QUARTERLY: 3, HALF_YEARLY: 6, YEARLY: 12}

    name = models.CharField(max_length=120)
    policy_type = models.CharField(max_length=60, blank=True, help_text="Term/Life, Health, Motor, ...")
    sum_assured = models.DecimalField(null=True, blank=True, **MONEY)
    premium_amount = models.DecimalField(**MONEY)
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default=YEARLY)
    premium_month = models.IntegerField(
        choices=MONTH_CHOICES, default=1,
        help_text="Month the premium is debited. Quarterly and half-yearly policies repeat "
                  "every 3 or 6 months from here.",
    )
    start_month = models.DateField(null=True, blank=True, help_text="Blank means the policy is already running.")
    end_month = models.DateField(
        null=True, blank=True,
        help_text="Blank means ongoing. For a term policy enter the term in years and this is filled in for you.",
    )
    term_years = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name="Term (years)",
        help_text="Term policies: enter the term and the end month is computed from the start month.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "insurance policies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.start_month:
            self.start_month = month_start(self.start_month)
        if self.end_month:
            self.end_month = month_start(self.end_month)
        super().save(*args, **kwargs)

    def clean(self):
        if self.start_month and self.end_month and self.end_month < self.start_month:
            raise ValidationError({"end_month": "The end month cannot be before the start month."})

    @property
    def frequency_months(self) -> int:
        return self.FREQUENCY_MONTHS[self.frequency]

    @property
    def payments_per_year(self) -> int:
        return 12 // self.frequency_months

    @property
    def annual_cost(self) -> Decimal:
        return Decimal(self.premium_amount) * self.payments_per_year

    @property
    def monthly_equivalent(self) -> Decimal:
        """Only ever used to size the emergency fund -- cashflow is never smoothed."""
        return self.annual_cost / Decimal("12")

    def apply_term_years(self, planner_start: dt.date) -> None:
        """Fill in `end_month` from `term_years` so the user never does date maths.

        A 16-year term starting Apr 2024 ends Mar 2040: start + 16 years less
        one month, so the final covered month is inclusive.
        """
        if not self.term_years:
            return
        anchor = month_start(self.start_month or planner_start)
        self.end_month = add_months(anchor, int(self.term_years) * 12 - 1)
