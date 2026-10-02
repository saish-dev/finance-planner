"""The projection engine -- the single source of truth for every number the UI shows.

This is the Python translation of the spreadsheet's Cashflow tab: one formula
chain, evaluated month by month, from the planner's start month to the end of
the requested year. Nothing here imports a view, a template or a form, and
nothing is ever persisted -- projections are always recomputed from current
data, which is why deleting a loan or a fund can never corrupt history.

Column order per month (each step may depend on the ones above it):

    1  salary            9  emergency-fund target (informational only)
    2  bonus            10  bank balance   (every rupee of surplus, uncapped)
    3  living expenses  11  investments    (SIPs + growth only)
    4  insurance        12  net worth
    5  loan EMIs + balances
    6  SIPs
    7  one-time expenses
    8  net surplus

There is no sweep and no separate surplus pool: net surplus simply
accumulates in the bank balance, forever. The emergency-fund target is shown
purely as a milestone -- `ef_goal_met` reports whether the balance has
reached it, and nothing is ever moved out of the bank because of it.

Retirement (EPF) sits outside that chain on purpose -- see `_pf_plans`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ..models import (
    MAX_PROJECTION_YEARS,
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
from .amortization import amortise_one_month
from .dates import add_months, month_diff, month_range, month_start, years_elapsed
from .money import ZERO, growth_factor, monthly_rate, q2

DEC0 = Decimal("0")
DEC1 = Decimal("1")


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class LoanMonth:
    """One loan's slice of one month."""
    loan_id: int
    name: str
    emi: Decimal
    balance: Decimal


@dataclass
class LumpyItem:
    """A non-monthly expense or premium that actually charged this month."""
    name: str
    amount: Decimal

    def __str__(self) -> str:
        return self.name


@dataclass
class MonthRow:
    """Every column the UI needs for a single month."""
    index: int
    month: dt.date

    salary: Decimal = ZERO
    bonus: Decimal = ZERO
    total_inflow: Decimal = ZERO

    living_expenses: Decimal = ZERO
    insurance: Decimal = ZERO
    total_emi: Decimal = ZERO
    sip: Decimal = ZERO
    one_time_expense: Decimal = ZERO
    total_outflow: Decimal = ZERO

    # Any non-monthly expense or insurance premium that actually charged this
    # month, so the UI can flag a spike instead of leaving it to be spotted
    # by eye in a scrolling column of numbers.
    lumpy_items: list[LumpyItem] = field(default_factory=list)

    @property
    def is_lumpy(self) -> bool:
        return bool(self.lumpy_items)

    net_surplus: Decimal = ZERO

    loans: list[LoanMonth] = field(default_factory=list)
    total_loan_balance: Decimal = ZERO

    # Retirement (EPF). Contributions are shown for information only: the
    # employee half is already out of `salary`, the employer half never
    # reaches the bank, so neither appears in total_outflow.
    pf_employee: Decimal = ZERO
    pf_employer: Decimal = ZERO
    pf_contribution: Decimal = ZERO
    pf_growth: Decimal = ZERO
    pf_balance: Decimal = ZERO

    # Informational milestone only -- reaching it moves nothing. Net surplus
    # always accumulates straight into the bank balance below.
    ef_target: Decimal = ZERO
    ef_goal_met: bool = False

    bank_interest: Decimal = ZERO
    bank_balance: Decimal = ZERO
    cash_shortfall: bool = False

    investment_growth: Decimal = ZERO
    investment_balance: Decimal = ZERO

    # Money put in versus money earned, for each growing balance. "Capital" is
    # measured from the projection start: it counts the value you already had
    # on day one as capital, because the app has no way of knowing what you
    # originally paid for it. So these are gains *over the projection*, not
    # lifetime returns.
    invested_capital: Decimal = ZERO
    investment_gains: Decimal = ZERO
    pf_capital: Decimal = ZERO
    pf_gains: Decimal = ZERO

    @property
    def total_capital(self) -> Decimal:
        return self.invested_capital + self.pf_capital

    @property
    def total_gains(self) -> Decimal:
        return self.investment_gains + self.pf_gains

    net_worth: Decimal = ZERO

    @property
    def year(self) -> int:
        return self.month.year

    def loan_by_id(self, loan_id: int) -> LoanMonth | None:
        for entry in self.loans:
            if entry.loan_id == loan_id:
                return entry
        return None


@dataclass
class Horizon:
    """The resolved projection window, plus anything the user should be told."""
    start_month: dt.date
    end_year: int
    requested_year: int
    notices: list[str] = field(default_factory=list)

    @property
    def end_month(self) -> dt.date:
        return dt.date(self.end_year, 12, 1)

    @property
    def total_months(self) -> int:
        return month_diff(self.end_month, self.start_month) + 1


def resolve_horizon(planner: PlannerSettings, upto_year: int | None = None) -> Horizon:
    """Clamp the requested end year into something projectable.

    Too early clamps to the start year, too far clamps to 30 years -- neither
    is an error, because the year box is the most-used control in the app and
    it should never throw the user into a validation page.
    """
    start = month_start(planner.start_month)
    requested = int(upto_year or planner.project_to_year)
    notices: list[str] = []

    end_year = requested
    if end_year < start.year:
        end_year = start.year
        notices.append(
            f"{requested} is before the projection starts, so it has been clamped to {end_year}."
        )
    # start_year + 29 keeps the window at or under 360 months whatever month
    # of the year the projection starts in.
    max_year = start.year + MAX_PROJECTION_YEARS - 1
    if end_year > max_year:
        end_year = max_year
        notices.append(
            f"Projections are capped at {MAX_PROJECTION_YEARS} years, so {requested} "
            f"has been shortened to {end_year}. Beyond that the compounding "
            f"assumptions stop being meaningful."
        )
    return Horizon(start_month=start, end_year=end_year, requested_year=requested, notices=notices)


# --------------------------------------------------------------------------
# Pre-computed per-object schedules
# --------------------------------------------------------------------------

@dataclass
class _LoanPlan:
    """A loan reduced to what the monthly loop needs."""
    loan_id: int
    name: str
    emi: Decimal
    rate: Decimal
    seed_month: dt.date
    last_emi_month: dt.date | None
    opening_balance: Decimal


def _loan_plans(loans, start: dt.date) -> list[_LoanPlan]:
    plans = []
    for loan in loans:
        plans.append(
            _LoanPlan(
                loan_id=loan.pk,
                name=loan.name,
                emi=Decimal(loan.monthly_emi),
                rate=loan.rate_per_month,
                seed_month=loan.seed_month(start),
                last_emi_month=loan.last_emi_month(start),
                opening_balance=loan.opening_balance(start),
            )
        )
    return plans


@dataclass
class _SipPlan:
    holding_id: int
    name: str
    base_sip: Decimal
    stepup_pct: Decimal
    start: dt.date
    end: dt.date | None

    def amount_for(self, month: dt.date) -> Decimal:
        """SIP due this month, stepped up from THIS fund's own start month.

        Anchoring the step-up to the fund's own SIP start (not the global
        projection start) is the bug this app is explicitly meant not to
        repeat: a fund whose SIP begins in year 4 gets its first step-up in
        year 5, not immediately.
        """
        if month < self.start or (self.end and month > self.end):
            return DEC0
        return self.base_sip * growth_factor(self.stepup_pct, years_elapsed(month, self.start))


def _sip_plans(holdings, start: dt.date) -> list[_SipPlan]:
    return [
        _SipPlan(
            holding_id=h.pk,
            name=h.name,
            base_sip=Decimal(h.monthly_sip or 0),
            stepup_pct=Decimal(h.annual_stepup_pct or 0),
            start=h.effective_sip_start(start),
            end=h.sip_end_month,
        )
        for h in holdings
    ]


@dataclass
class _BonusPlan:
    """A bonus/variable-pay stream reduced to what the monthly loop needs."""
    months: tuple[int, ...]
    amount_per_month: Decimal
    start: dt.date
    end: dt.date | None

    def amount_for(self, month: dt.date) -> Decimal:
        if month < self.start or (self.end and month > self.end):
            return DEC0
        return self.amount_per_month if month.month in self.months else DEC0


def _bonus_plans(extras, start: dt.date) -> list[_BonusPlan]:
    return [
        _BonusPlan(
            months=tuple(e.installment_months()),
            amount_per_month=Decimal(e.annual_amount) / e.installment_count,
            start=e.effective_start(start),
            end=e.end_month,
        )
        for e in extras
    ]


@dataclass
class _PfPlan:
    """A retirement account reduced to what the monthly loop needs.

    Contributions track your salary: EPF is a percentage of basic pay, so when
    the salary jumps -- whether through an explicit SalaryChange or the default
    annual hike -- the contribution jumps in the same proportion. The user
    enters today's rupee amount and never has to restate it after a raise.
    """
    account_id: int
    name: str
    employee: Decimal
    employer: Decimal
    rate: Decimal
    start: dt.date
    end: dt.date | None
    opening_balance: Decimal
    anchor_salary: Decimal  # salary in the account's start month
    cap: Decimal | None = None  # ceiling on employee + employer together

    def contributions_for(self, month: dt.date, salary: Decimal) -> tuple[Decimal, Decimal]:
        if month < self.start or (self.end and month > self.end):
            return DEC0, DEC0

        if self.anchor_salary <= 0:
            # No salary to track (or none entered yet): hold the contribution
            # flat rather than dividing by zero.
            employee, employer = self.employee, self.employer
        else:
            ratio = salary / self.anchor_salary
            employee, employer = self.employee * ratio, self.employer * ratio

        # EPF on the statutory wage ceiling stops rising with your salary once
        # it reaches the cap. Both halves are scaled by the same factor so the
        # employee/employer split of the capped total is preserved.
        total = employee + employer
        if self.cap is not None and total > self.cap and total > 0:
            scale = self.cap / total
            employee, employer = employee * scale, employer * scale
        return employee, employer


def blended_return_pct(holdings, default_pct: Decimal) -> Decimal:
    """Value-weighted expected return across all holdings.

    SIMPLIFYING ASSUMPTION (same as the spreadsheet): this is computed once
    from today's values and then held constant for the whole projection,
    rather than being re-weighted every month as the faster-growing funds
    come to dominate the pool. Re-weighting monthly would move the 30-year
    net worth by a few percent at most, and it would make the number
    impossible to check by hand against the sheet.
    """
    holdings = list(holdings)
    if not holdings:
        return Decimal(default_pct)

    total_value = sum((Decimal(h.current_value or 0) for h in holdings), DEC0)
    if total_value > 0:
        weighted = sum(
            (Decimal(h.current_value or 0) * Decimal(h.effective_return_pct(default_pct)) for h in holdings),
            DEC0,
        )
        return weighted / total_value

    # Nothing invested yet: weight by SIP size instead, so a portfolio that
    # only exists as future SIPs still gets a sensible blended rate.
    total_sip = sum((Decimal(h.monthly_sip or 0) for h in holdings), DEC0)
    if total_sip > 0:
        weighted = sum(
            (Decimal(h.monthly_sip or 0) * Decimal(h.effective_return_pct(default_pct)) for h in holdings),
            DEC0,
        )
        return weighted / total_sip
    return Decimal(default_pct)


def _expense_due(expense: Expense, month: dt.date, inflation_factor: Decimal) -> Decimal:
    """The real charge this month -- never smoothed across months.

    Mirrors `_premium_due` below: a monthly expense (frequency_months == 1)
    matches every month, exactly as before this field existed. A yearly,
    half-yearly or quarterly one only matches its due month and its repeats.
    """
    if (month.month - expense.due_month) % expense.frequency_months != 0:
        return DEC0
    amount = Decimal(expense.amount)
    return amount * inflation_factor if expense.inflates else amount


def _expense_monthly_equivalent(expense: Expense, inflation_factor: Decimal) -> Decimal:
    """Smoothed monthly cost -- sizes the emergency fund only, same idea as
    an insurance policy's `monthly_equivalent`, so a lumpy expense does not
    make the EF target lurch in the one month it actually falls due."""
    equiv = expense.annual_cost / Decimal("12")
    return equiv * inflation_factor if expense.inflates else equiv


def _premium_due(policy: InsurancePolicy, month: dt.date, planner_start: dt.date) -> Decimal:
    """The exact premium charged this month -- never smoothed across months."""
    start = policy.start_month or planner_start
    if month < month_start(start):
        return DEC0
    if policy.end_month and month > policy.end_month:
        return DEC0
    # Quarterly/half-yearly repeat every 3/6 months from premium_month.
    if (month.month - policy.premium_month) % policy.frequency_months != 0:
        return DEC0
    return Decimal(policy.premium_amount)


# --------------------------------------------------------------------------
# Salary
# --------------------------------------------------------------------------

def _salary_series(planner, changes, months, start) -> dict:
    """Walk the salary across every month once, up front.

    Done ahead of the main loop rather than inside it because the retirement
    contributions need the salary for an arbitrary month (their own anchor
    month) to work out how far pay has moved since then.
    """
    by_month = {c.effective_month: Decimal(c.monthly_in_hand) for c in changes}
    hike_rate = DEC1 + (Decimal(planner.default_salary_hike_pct) / Decimal("100"))

    series = {}
    salary = q2(_base_salary(changes, start))
    for index, month in enumerate(months):
        # An explicit change wins; otherwise carry forward and apply the
        # default hike once a year in the designated month. The hike never
        # fires in month 0 -- the salary you entered is the salary you are on.
        if month in by_month:
            salary = q2(by_month[month])
        elif index > 0 and month.month == planner.default_hike_month:
            salary = q2(salary * hike_rate)
        series[month] = salary
    return series


def _pf_plans(accounts, start: dt.date, salaries: dict) -> list[_PfPlan]:
    plans = []
    for account in accounts:
        # The amounts entered are today's rupees, so they are anchored to the
        # salary in the account's first projected month.
        anchor = max(account.effective_start(start), start)
        plans.append(
            _PfPlan(
                account_id=account.pk,
                name=account.name,
                employee=Decimal(account.employee_monthly or 0),
                employer=Decimal(account.employer_monthly or 0),
                rate=monthly_rate(account.annual_return_pct),
                start=anchor,
                end=account.end_month,
                opening_balance=Decimal(account.current_balance or 0),
                anchor_salary=salaries.get(anchor, DEC0),
                cap=Decimal(account.monthly_cap) if account.monthly_cap is not None else None,
            )
        )
    return plans


def _base_salary(changes: list[SalaryChange], start: dt.date) -> Decimal:
    """Salary in effect at the projection start.

    Normally the most recent change on or before the start month. If every
    change is in the future the earliest one is used as the base, so the
    months before it are not silently projected at zero income.
    """
    prior = [c for c in changes if c.effective_month <= start]
    if prior:
        return Decimal(prior[-1].monthly_in_hand)
    if changes:
        return Decimal(changes[0].monthly_in_hand)
    return DEC0


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

def build_projection(user, upto_year: int | None = None) -> list[MonthRow]:
    """One MonthRow per month from the planner's start month to the horizon.

    `upto_year` overrides PlannerSettings.project_to_year for this call only
    (that is what the dashboard's "Project to year" box passes), and is
    clamped by resolve_horizon.
    """
    planner = PlannerSettings.objects.filter(user=user).first()
    if planner is None:
        return []

    horizon = resolve_horizon(planner, upto_year)
    start = horizon.start_month

    months = list(month_range(start, horizon.end_month))

    salary_changes = list(SalaryChange.objects.filter(user=user).order_by("effective_month"))
    salaries = _salary_series(planner, salary_changes, months, start)
    bonus_plans = _bonus_plans(IncomeExtra.objects.filter(user=user), start)
    expenses = list(Expense.objects.filter(user=user))
    policies = list(InsurancePolicy.objects.filter(user=user))
    one_time_by_month: dict[dt.date, list[OneTimeExpense]] = {}
    for ote in OneTimeExpense.objects.filter(user=user):
        one_time_by_month.setdefault(ote.month, []).append(ote)
    loan_plans = _loan_plans(Loan.objects.filter(user=user), start)
    holdings = list(InvestmentHolding.objects.filter(user=user))
    sip_plans = _sip_plans(holdings, start)
    pf_plans = _pf_plans(RetirementAccount.objects.filter(user=user), start, salaries)

    bank_rate = monthly_rate(planner.bank_interest_pct)
    invest_rate = monthly_rate(blended_return_pct(holdings, Decimal(planner.default_investment_return_pct)))
    ef_months = Decimal(planner.ef_target_months or 0)
    ef_fixed = Decimal(planner.ef_target_fixed_amount) if planner.ef_target_fixed_amount is not None else None
    insurance_monthly_equiv = sum((p.monthly_equivalent for p in policies), DEC0)

    # Running state. All Decimal, quantised to paise each month so the numbers
    # are reproducible and hand-checkable rather than drifting on long chains.
    bank = q2(Decimal(planner.bank_balance_today))
    investments = q2(sum((Decimal(h.current_value or 0) for h in holdings), DEC0))

    # Capital put in, tracked alongside each balance so growth is the residual.
    invested_capital = investments
    pf_balances = {plan.account_id: q2(plan.opening_balance) for plan in pf_plans}
    pf_capital = q2(sum(pf_balances.values(), DEC0))
    loan_balances = {plan.loan_id: DEC0 for plan in loan_plans}

    rows: list[MonthRow] = []

    for index, month in enumerate(months):
        row = MonthRow(index=index, month=month)

        # 1. Salary (walked ahead of the loop in _salary_series).
        row.salary = salaries[month]

        # 2. Bonus / variable pay. A bonus with an "Ends in" month splits its
        #    annual amount evenly across every month from "Starts in" through
        #    there, repeating every year -- a single payout month is just the
        #    one-month case of the same rule.
        row.bonus = q2(sum((plan.amount_for(month) for plan in bonus_plans), DEC0))
        row.total_inflow = q2(row.salary + row.bonus)

        # 3. Living expenses: inflating ones grow in 12-month steps from the
        #    projection start, flat ones never move. A yearly, half-yearly or
        #    quarterly expense is charged in full in its real month only --
        #    never smoothed across the months in between.
        elapsed_years = years_elapsed(month, start)
        inflation_factor = growth_factor(planner.expense_inflation_pct, elapsed_years)
        living = DEC0
        lumpy_items: list[LumpyItem] = []
        for expense in expenses:
            due = q2(_expense_due(expense, month, inflation_factor))
            living += due
            if due and expense.frequency_months > 1:
                lumpy_items.append(LumpyItem(name=expense.name, amount=due))
        row.living_expenses = q2(living)

        # 4. Insurance: the real premium in the real month.
        insurance_due = DEC0
        for policy in policies:
            premium = q2(_premium_due(policy, month, start))
            insurance_due += premium
            if premium and policy.frequency_months > 1:
                lumpy_items.append(LumpyItem(name=policy.name, amount=premium))
        row.insurance = q2(insurance_due)
        row.lumpy_items = lumpy_items

        # 5. Loans: flat EMI inside the window, balance amortised monthly.
        total_emi = DEC0
        total_balance = DEC0
        for plan in loan_plans:
            balance = loan_balances[plan.loan_id]
            if month == plan.seed_month:
                balance = q2(plan.opening_balance)

            in_window = (
                plan.last_emi_month is not None
                and plan.seed_month <= month <= plan.last_emi_month
            )
            # A loan that has already amortised to zero stops charging EMIs
            # even if its stated tenure runs on for another month or two.
            emi = DEC0
            if in_window and balance > 0:
                # The last EMI is capped at what is actually owed. The sheet
                # charged a flat EMI here and floored the balance at zero,
                # which quietly spent money that was never due -- this keeps
                # the cashflow column and the balance column consistent.
                payoff = q2(balance * (DEC1 + plan.rate))
                emi = min(plan.emi, payoff)
                balance = q2(amortise_one_month(balance, plan.rate, emi))

            loan_balances[plan.loan_id] = balance
            total_emi += emi
            total_balance += balance
            row.loans.append(LoanMonth(loan_id=plan.loan_id, name=plan.name, emi=q2(emi), balance=balance))

        row.total_emi = q2(total_emi)
        row.total_loan_balance = q2(total_balance)

        # 6. SIPs, each stepped up from its own fund's SIP start month.
        row.sip = q2(sum((plan.amount_for(month) for plan in sip_plans), DEC0))

        # 6b. Retirement (EPF). Contributions scale with the salary, and the
        #     corpus compounds -- but NEITHER figure touches the cashflow. The
        #     employee half is already out of `salary` before it reaches you,
        #     and the employer half never enters your bank account. They are
        #     recorded here so the tables can show them, and they feed net
        #     worth, nothing else.
        pf_employee = DEC0
        pf_employer = DEC0
        pf_growth = DEC0
        pf_total = DEC0
        for plan in pf_plans:
            # Per account, not pooled: two accounts may credit different rates.
            balance = pf_balances[plan.account_id]
            growth = q2(balance * plan.rate)
            employee, employer = plan.contributions_for(month, row.salary)
            balance = q2(balance + growth + employee + employer)

            pf_balances[plan.account_id] = balance
            pf_employee += employee
            pf_employer += employer
            pf_growth += growth
            pf_total += balance

        row.pf_employee = q2(pf_employee)
        row.pf_employer = q2(pf_employer)
        row.pf_contribution = q2(row.pf_employee + row.pf_employer)
        row.pf_growth = q2(pf_growth)
        row.pf_balance = q2(pf_total)

        pf_capital = q2(pf_capital + row.pf_contribution)
        row.pf_capital = pf_capital
        row.pf_gains = q2(row.pf_balance - pf_capital)

        # 7. One-time expenses: a single purchase, charged in full in its own
        #    month, never repeated -- and, like a one-off, never counted
        #    towards the emergency-fund target below.
        one_time_total = DEC0
        for ote in one_time_by_month.get(month, []):
            amount = q2(Decimal(ote.amount))
            one_time_total += amount
            lumpy_items.append(LumpyItem(name=ote.name, amount=amount))
        row.one_time_expense = q2(one_time_total)

        # 8. Net surplus.
        row.total_outflow = q2(
            row.living_expenses + row.insurance + row.total_emi + row.sip + row.one_time_expense
        )
        row.net_surplus = q2(row.total_inflow - row.total_outflow)

        # 9. Emergency fund target: fixed if given, else N months of essential
        #    spending. Insurance and any non-monthly expense enter at their
        #    monthly equivalent here (and only here) so a yearly charge does
        #    not make the target lurch in the one month it actually lands.
        #    One-time expenses never enter this at all -- a single purchase
        #    is not an ongoing essential cost.
        if ef_fixed is not None:
            row.ef_target = q2(ef_fixed)
        else:
            expenses_equiv = sum(
                (_expense_monthly_equivalent(e, inflation_factor) for e in expenses), DEC0
            )
            row.ef_target = q2(ef_months * (expenses_equiv + insurance_monthly_equiv + row.total_emi))

        # 10. Bank. Every rupee of net surplus simply accumulates here, with
        #    interest, for good -- there is no cap and nowhere else for it to
        #    go. A deficit is never covered from anywhere: the balance just
        #    goes negative and the month is flagged, because a month the plan
        #    cannot fund is exactly what needs to be visible.
        row.bank_interest = q2(bank * bank_rate) if bank > 0 else ZERO
        bank = q2(bank + row.bank_interest + row.net_surplus)
        row.bank_balance = bank
        row.cash_shortfall = bank < 0
        row.ef_goal_met = bank >= row.ef_target

        # 11. Investments: your actual funds -- SIPs and growth ONLY. Never
        #     topped up from the bank and never sold to cover a shortfall.
        row.investment_growth = q2(investments * invest_rate)
        investments = q2(investments + row.investment_growth + row.sip)
        row.investment_balance = investments

        # Capital in each balance, so the rest of it is growth. Investments
        # only ever receive SIPs, so their capital just accumulates.
        invested_capital = q2(invested_capital + row.sip)
        row.invested_capital = invested_capital
        row.investment_gains = q2(investments - invested_capital)

        # 12. Net worth. The PF corpus counts towards it, but note that it was
        #     never offered up to cover a shortfall above: it is locked money,
        #     not a buffer.
        row.net_worth = q2(bank + investments + row.pf_balance - row.total_loan_balance)

        rows.append(row)

    return rows


# --------------------------------------------------------------------------
# Roll-ups over a finished projection
# --------------------------------------------------------------------------

@dataclass
class YearRow:
    year: int
    inflow: Decimal
    outflow: Decimal
    surplus: Decimal
    living_expenses: Decimal
    insurance: Decimal
    emi: Decimal
    sip: Decimal
    one_time_expense: Decimal
    pf_contribution: Decimal
    bank_balance: Decimal
    investment_balance: Decimal
    pf_balance: Decimal
    total_loan_balance: Decimal
    net_worth: Decimal
    months: int
    # Year-end split of each growing balance into money in vs money earned.
    invested_capital: Decimal = ZERO
    investment_gains: Decimal = ZERO
    pf_capital: Decimal = ZERO
    pf_gains: Decimal = ZERO


def annual_rollup(rows: list[MonthRow]) -> list[YearRow]:
    """Year-by-year summary. Balances are taken from each year's last month."""
    by_year: dict[int, list[MonthRow]] = {}
    for row in rows:
        by_year.setdefault(row.year, []).append(row)

    out = []
    for year in sorted(by_year):
        months = by_year[year]
        last = months[-1]
        out.append(
            YearRow(
                year=year,
                inflow=q2(sum((m.total_inflow for m in months), DEC0)),
                outflow=q2(sum((m.total_outflow for m in months), DEC0)),
                surplus=q2(sum((m.net_surplus for m in months), DEC0)),
                living_expenses=q2(sum((m.living_expenses for m in months), DEC0)),
                insurance=q2(sum((m.insurance for m in months), DEC0)),
                emi=q2(sum((m.total_emi for m in months), DEC0)),
                sip=q2(sum((m.sip for m in months), DEC0)),
                one_time_expense=q2(sum((m.one_time_expense for m in months), DEC0)),
                pf_contribution=q2(sum((m.pf_contribution for m in months), DEC0)),
                bank_balance=last.bank_balance,
                investment_balance=last.investment_balance,
                pf_balance=last.pf_balance,
                total_loan_balance=last.total_loan_balance,
                net_worth=last.net_worth,
                months=len(months),
                invested_capital=last.invested_capital,
                investment_gains=last.investment_gains,
                pf_capital=last.pf_capital,
                pf_gains=last.pf_gains,
            )
        )
    return out


@dataclass
class ProjectionSummary:
    """The headline numbers on the dashboard."""
    first: MonthRow | None = None
    last: MonthRow | None = None
    ef_reached_month: dt.date | None = None
    debt_free_month: dt.date | None = None
    opening_loan_balance: Decimal = ZERO
    lowest_surplus: Decimal = ZERO
    lowest_surplus_month: dt.date | None = None
    shortfall_months: int = 0
    first_shortfall_month: dt.date | None = None
    has_loans: bool = False


def summarise(rows: list[MonthRow]) -> ProjectionSummary:
    if not rows:
        return ProjectionSummary()

    summary = ProjectionSummary(first=rows[0], last=rows[-1])
    summary.opening_loan_balance = rows[0].total_loan_balance
    summary.has_loans = any(row.loans for row in rows)

    for row in rows:
        if summary.ef_reached_month is None and row.ef_goal_met:
            summary.ef_reached_month = row.month
        if row.cash_shortfall:
            summary.shortfall_months += 1
            if summary.first_shortfall_month is None:
                summary.first_shortfall_month = row.month

    if summary.has_loans:
        # Debt-free from the month after the last one that either carries a
        # balance or charges an EMI -- the final instalment month is not a
        # debt-free month, even though it closes at zero.
        indebted = [row for row in rows if row.total_loan_balance > 0 or row.total_emi > 0]
        if not indebted:
            summary.debt_free_month = rows[0].month
        elif indebted[-1] is not rows[-1]:
            summary.debt_free_month = add_months(indebted[-1].month, 1)

    worst = min(rows, key=lambda r: r.net_surplus)
    summary.lowest_surplus = worst.net_surplus
    summary.lowest_surplus_month = worst.month
    return summary


def loan_schedule(loan: Loan, rows: list[MonthRow]) -> list[dict]:
    """Month-by-month amortisation for one loan, pulled out of the projection.

    Derived from the same rows the rest of the app renders, so the loan detail
    page can never disagree with the cashflow table.
    """
    schedule = []
    previous = None
    for row in rows:
        entry = row.loan_by_id(loan.pk)
        if entry is None:
            continue
        if entry.emi == 0 and entry.balance == 0:
            if previous is None:
                continue  # the loan has not started yet
            break  # it is paid off; do not trail empty months behind it
        if previous is not None:
            opening = previous
        elif entry.emi:
            # Invert one month of amortisation: closing = opening*(1+r) - emi.
            opening = q2((entry.balance + entry.emi) / (DEC1 + loan.rate_per_month))
        else:
            opening = entry.balance
        interest = q2(opening * loan.rate_per_month) if entry.emi else ZERO
        principal_paid = q2(entry.emi - interest) if entry.emi else ZERO
        schedule.append({
            "month": row.month,
            "opening": opening,
            "emi": entry.emi,
            "interest": interest,
            "principal": principal_paid,
            "closing": entry.balance,
        })
        previous = entry.balance
    return schedule
