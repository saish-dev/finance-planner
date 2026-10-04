"""One CRUD description per editable table.

Every data page (income, expenses, loans, investments, insurance) is the same
page: a table of rows, an inline add/edit form, and a delete button. Rather
than writing that five times, each table is described here and rendered by a
generic view + a generic template. Columns are computed in Python, so the
templates only ever have to know how to print a money value or a month.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from .forms import (
    ActualBalanceForm,
    ExpenseForm,
    GoalForm,
    IncomeExtraForm,
    InsurancePolicyForm,
    InvestmentHoldingForm,
    LoanForm,
    OneTimeExpenseForm,
    RetirementAccountForm,
    SalaryChangeForm,
)
from .models import (
    ActualBalance,
    Expense,
    Goal,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    OneTimeExpense,
    RetirementAccount,
    SalaryChange,
)
from .services.dates import format_month

# Cell kinds understood by partials/_cell.html
MONEY = "money"
PCT = "pct"
MONTH = "month"
TEXT = "text"
BOOL = "bool"
NUM = "num"


@dataclass
class Column:
    label: str
    kind: str
    get: Callable
    derived: bool = False  # shown greyed: the app worked this out, you did not
    align_right: bool = True
    info: str = ""         # explains how the value is arrived at, in a tooltip

    def __post_init__(self):
        if self.kind in (TEXT, BOOL, MONTH):
            self.align_right = self.kind is not TEXT


@dataclass
class CrudConfig:
    slug: str
    model: type
    form_class: type
    singular: str
    plural: str
    columns: list[Column]
    page: str                      # url name of the page this table lives on
    add_label: str = ""
    empty_message: str = ""
    needs_planner: bool = False    # form takes a `planner=` kwarg
    needs_user: bool = False       # form takes a `user=` kwarg
    can_duplicate: bool = True     # rows may be copied (not where a field must be unique)
    needs_projection: bool = False # derived columns read ctx["projection"] (rows by month)
    order_by: tuple = ()
    row_warnings: Callable | None = None
    detail_url_name: str = ""
    note: str = ""
    extra_row_context: Callable | None = None

    def __post_init__(self):
        self.add_label = self.add_label or f"Add {self.singular.lower()}"
        self.empty_message = self.empty_message or f"No {self.plural.lower()} yet."


def _month_or_dash(value):
    return format_month(value) if value else None


# --------------------------------------------------------------------------
# Derived columns for loans -- these are the "what did the app work out for
# me?" values the spec asks to show inline, next to what the user typed.
# --------------------------------------------------------------------------

def _loan_last_emi(loan, ctx):
    return _month_or_dash(loan.last_emi_month(ctx["planner_start"]))


def _loan_opening(loan, ctx):
    return loan.opening_balance(ctx["planner_start"])


def _loan_months_left(loan, ctx):
    return loan.months_remaining_at(ctx["planner_start"])


def _loan_warnings(loan, ctx):
    warnings = []
    if loan.last_emi_month(ctx["planner_start"]) is None:
        warnings.append(
            "This loan has no derivable end date. Add a tenure or an end month override."
        )
    if loan.principal_outstanding_today is None:
        warnings.append(
            "Opening balance was back-solved from the EMI, rate and remaining tenure."
        )
    if loan.end_month_override:
        warnings.append("End month override is in force; tenure and derived dates are ignored.")
    return warnings


def _holding_sip_window(holding, ctx):
    start = holding.sip_start_month
    if not start and not holding.monthly_sip:
        return "No SIP"
    start_label = format_month(start) if start else "already running"
    end_label = format_month(holding.sip_end_month) if holding.sip_end_month else "ongoing"
    return f"{start_label} - {end_label}"


def _holding_return(holding, ctx):
    """Rate and where it came from, in one cell instead of four columns."""
    rate = holding.effective_return_pct(ctx["default_return_pct"])
    basis = holding.return_basis_label(ctx["default_return_pct"])
    return f"{rate.normalize():f}% ({basis.lower()})"


def _holding_warnings(holding, ctx):
    warnings = []
    rate = holding.effective_return_pct(ctx["default_return_pct"])
    if holding.return_basis in (holding.BASIS_3Y, holding.BASIS_5Y):
        # A trailing return is history, not a forecast, and this one is being
        # applied flat for the whole horizon.
        warnings.append(
            f"{holding.get_return_basis_display()} applied flat for the whole projection."
        )
    if holding.expected_return_pct is None and holding.return_basis == holding.BASIS_CUSTOM:
        warnings.append(f"Using the planner default return of {ctx['default_return_pct']}%.")
    return warnings


def _retirement_window(account, ctx):
    start = format_month(account.start_month) if account.start_month else "already running"
    end = format_month(account.end_month) if account.end_month else "ongoing"
    return f"{start} - {end}"


def _retirement_warnings(account, ctx):
    if account.monthly_cap is not None:
        warnings = [
            f"Capped at {account.monthly_cap:,.0f} a month; the corpus is not spendable."
        ]
        if account.monthly_total > account.monthly_cap:
            warnings.append(
                "Already at the cap, so contributions no longer rise with your salary."
            )
    else:
        warnings = [
            "Uncapped: contributions rise with your salary for the whole projection. "
            "EPF on the statutory ceiling would cap at 3,600 a month."
        ]
    if not account.employee_monthly and not account.employer_monthly:
        warnings.append("No monthly contribution: this balance only compounds.")
    return warnings


def _policy_window(policy, ctx):
    start = format_month(policy.start_month) if policy.start_month else "already running"
    end = format_month(policy.end_month) if policy.end_month else "ongoing"
    return f"{start} - {end}"


def _policy_annual(policy, ctx):
    return policy.annual_cost


def _policy_warnings(policy, ctx):
    warnings = []
    if policy.term_years and policy.end_month:
        warnings.append(
            f"{policy.term_years}-year term, so cover ends {format_month(policy.end_month)}."
        )
    elif not policy.end_month:
        warnings.append("No end month: premiums run for the whole projection.")
    return warnings


def _bonus_window(extra, ctx):
    start = format_month(extra.start_month) if extra.start_month else "already running"
    end = format_month(extra.end_month) if extra.end_month else "ongoing"
    return f"{start} - {end}"


def _bonus_warnings(extra, ctx):
    if not extra.end_month:
        return ["No end month: this amount repeats every year for the whole projection."]
    return []


CONFIGS: dict[str, CrudConfig] = {
    "salary": CrudConfig(
        slug="salary",
        model=SalaryChange,
        form_class=SalaryChangeForm,
        singular="Salary change",
        plural="Salary changes",
        page="income",
        order_by=("effective_month",),
        note="Add a row whenever your pay changes. Rows can go in in any order.",
        empty_message="No salary changes yet. Add your current in-hand salary to get started.",
        columns=[
            Column("Effective from", MONTH, lambda o, c: o.effective_month),
            Column("Monthly in-hand", MONEY, lambda o, c: o.monthly_in_hand),
            Column("Note", TEXT, lambda o, c: o.note),
        ],
    ),
    "bonus": CrudConfig(
        slug="bonus",
        model=IncomeExtra,
        form_class=IncomeExtraForm,
        singular="Bonus",
        plural="Bonus and variable pay",
        page="income",
        order_by=("payout_month",),
        note="Paid once a year by default. Set 'Split through' to divide the amount evenly "
             "across every month from 'Paid in' through there instead -- e.g. June to March "
             "pays it out in 10 equal parts. That pattern repeats every year the bonus is "
             "active -- set 'Starts in' / 'Ends in' if it isn't the same amount forever.",
        row_warnings=_bonus_warnings,
        columns=[
            Column("Label", TEXT, lambda o, c: o.label),
            Column("Annual amount", MONEY, lambda o, c: o.annual_amount),
            Column("Paid in", TEXT, lambda o, c: o.get_payout_month_display()),
            Column("Split through", TEXT,
                   lambda o, c: o.get_payout_end_month_display() if o.payout_end_month else "—"),
            Column("Active window", TEXT, _bonus_window, derived=True,
                   info="When this bonus applies. Blank 'Starts in' means already running at "
                        "the projection start; blank 'Ends in' means it repeats every year "
                        "for the whole projection."),
            Column("Per month paid", MONEY, lambda o, c: o.installment_amount, derived=True,
                   info="Annual amount divided across however many months it pays out over."),
        ],
    ),
    "expense": CrudConfig(
        slug="expense",
        model=Expense,
        form_class=ExpenseForm,
        singular="Expense",
        plural="Living expenses",
        page="expenses",
        order_by=("name",),
        note="Living costs only. EMIs, SIPs and insurance premiums have their own pages "
             "and their own cashflow columns, so adding them here would double-count them. "
             "A yearly, half-yearly or quarterly expense hits the cashflow in full in its real "
             "month, exactly like an insurance premium -- it is never smoothed across the year.",
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Amount", MONEY, lambda o, c: o.amount,
                   info="Charged in full each time this expense falls due -- not a monthly figure."),
            Column("Frequency", TEXT, lambda o, c: o.get_frequency_display()),
            Column("Due in", TEXT, lambda o, c: o.get_due_month_display()),
            Column("Inflates?", BOOL, lambda o, c: o.inflates),
            Column("Cost a year", MONEY, lambda o, c: o.annual_cost, derived=True,
                   info="Amount multiplied by payments per year. One twelfth of this sizes the "
                        "emergency fund; the cashflow still charges the real amount in its real month."),
            Column("In 10 years", MONEY,
                   lambda o, c: (o.annual_cost * (1 + Decimal(c["inflation_pct"]) / 100) ** 10)
                   if o.inflates else o.annual_cost,
                   derived=True,
                   info="This year's annual cost compounded at the planner's inflation rate for "
                        "ten years. Flat expenses are unchanged."),
        ],
    ),
    "onetime": CrudConfig(
        slug="onetime",
        model=OneTimeExpense,
        form_class=OneTimeExpenseForm,
        singular="One-time expense",
        plural="One-time expenses",
        page="expenses",
        order_by=("-month", "name"),
        note="A single purchase or one-off cost -- a phone, a trip, a repair. Charged in full "
             "in the month you pick, once, with no frequency and no inflation adjustment. Not "
             "counted towards the emergency-fund target.",
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Amount", MONEY, lambda o, c: o.amount),
            Column("Month", MONTH, lambda o, c: o.month),
        ],
    ),
    "loan": CrudConfig(
        slug="loan",
        model=Loan,
        form_class=LoanForm,
        singular="Loan",
        plural="Loans",
        page="loans",
        needs_planner=True,
        order_by=("name",),
        detail_url_name="loan_detail",
        note="Add as many loans as you have. Greyed columns are values the app derived "
             "from what you typed.",
        row_warnings=_loan_warnings,
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Started", MONTH, lambda o, c: o.start_month),
            Column("EMI", MONEY, lambda o, c: o.monthly_emi),
            Column("Rate", PCT, lambda o, c: o.annual_interest_pct),
            Column("Opening balance", MONEY, _loan_opening, derived=True,
                   info="What you entered as outstanding, or -- if you left it blank -- the "
                        "present value of the EMIs still to run: PV(rate/12, months left, EMI)."),
            Column("Last EMI", TEXT, _loan_last_emi, derived=True,
                   info="An end-month override wins if set; otherwise start month plus tenure, "
                        "or the tenure NPER derives from the principal, rate and EMI."),
            Column("EMIs left", NUM, _loan_months_left, derived=True,
                   info="Instalments still due from the projection start through the last EMI month."),
        ],
    ),
    "investment": CrudConfig(
        slug="investment",
        model=InvestmentHolding,
        form_class=InvestmentHoldingForm,
        singular="Holding",
        plural="Investments",
        page="investments",
        order_by=("name",),
        note="Mutual funds, stocks, PPF -- anything that grows. Each SIP steps up from its "
             "own start month, not from the projection start.",
        row_warnings=_holding_warnings,
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Category", TEXT, lambda o, c: o.category),
            Column("Value today", MONEY, lambda o, c: o.current_value),
            Column("Monthly SIP", MONEY, lambda o, c: o.monthly_sip),
            Column("Step-up", PCT, lambda o, c: o.annual_stepup_pct),
            Column("SIP window", TEXT, _holding_sip_window, derived=True,
                   info="When this fund's SIP runs. Blank start means it is already running at "
                        "the projection start; blank end means it continues to the horizon."),
            Column("Return", TEXT, _holding_return, derived=True,
                   info="The rate this holding compounds at, and which of your three figures "
                        "it came from. Change it with 'Project using' when you edit the row. "
                        "3Y and 5Y are trailing history applied flat for the whole projection."),
        ],
    ),
    "retirement": CrudConfig(
        slug="retirement",
        model=RetirementAccount,
        form_class=RetirementAccountForm,
        singular="Account",
        plural="Retirement (EPF)",
        page="retirement",
        order_by=("name",),
        note="Your in-hand salary is already net of the employee contribution, and the "
             "employer's share never reaches your bank -- so neither is charged to the "
             "cashflow again. The corpus counts towards net worth but is never sold to "
             "cover a shortfall and never counts towards the emergency fund. "
             "Contributions rise in step with your salary.",
        row_warnings=_retirement_warnings,
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Balance today", MONEY, lambda o, c: o.current_balance),
            Column("Employee / mo", MONEY, lambda o, c: o.employee_monthly),
            Column("Employer / mo", MONEY, lambda o, c: o.employer_monthly),
            Column("Interest", PCT, lambda o, c: o.annual_return_pct),
            Column("Cap / mo", MONEY, lambda o, c: o.monthly_cap),
            Column("Total / mo", MONEY, lambda o, c: o.capped_monthly_total, derived=True,
                   info="Employee plus employer contribution today, after the cap. Both rise in "
                        "step with your salary until the cap stops them, and neither is charged "
                        "to the monthly cashflow."),
            Column("Contribution window", TEXT, _retirement_window, derived=True,
                   info="When contributions run. The corpus keeps compounding after they stop; "
                        "nothing is ever withdrawn."),
        ],
    ),
    "insurance": CrudConfig(
        slug="insurance",
        model=InsurancePolicy,
        form_class=InsurancePolicyForm,
        singular="Policy",
        plural="Insurance policies",
        page="insurance",
        needs_planner=True,
        order_by=("name",),
        note="Enter a term in years and the end month is filled in for you. Premiums hit "
             "the cashflow in the exact month they fall due.",
        row_warnings=_policy_warnings,
        columns=[
            Column("Name", TEXT, lambda o, c: o.name),
            Column("Type", TEXT, lambda o, c: o.policy_type),
            Column("Sum assured", MONEY, lambda o, c: o.sum_assured),
            Column("Premium", MONEY, lambda o, c: o.premium_amount),
            Column("Frequency", TEXT, lambda o, c: o.get_frequency_display()),
            Column("Due in", TEXT, lambda o, c: o.get_premium_month_display()),
            Column("Cover window", TEXT, _policy_window, derived=True,
                   info="When the policy is in force. Entering a term in years fills in the end "
                        "month for you, counted inclusively from the start."),
            Column("Cost a year", MONEY, _policy_annual, derived=True,
                   info="Premium multiplied by payments per year. One twelfth of this sizes the "
                        "emergency fund; the cashflow still charges the real premium in its "
                        "real month."),
        ],
    ),
}


# --------------------------------------------------------------------------
# Actuals: what the accounts really held, beside what the plan said they would.
# --------------------------------------------------------------------------

def _actual_net_worth(actual, ctx):
    planned = ctx["projection"].get(actual.month)
    pf = actual.pf_balance if actual.pf_balance is not None else (planned.pf_balance if planned else 0)
    loans = (
        actual.loans_outstanding if actual.loans_outstanding is not None
        else (planned.total_loan_balance if planned else 0)
    )
    return actual.bank_balance + actual.investment_balance + pf - loans


def _planned_net_worth(actual, ctx):
    planned = ctx["projection"].get(actual.month)
    return planned.net_worth if planned else None


def _actual_drift(actual, ctx):
    planned = _planned_net_worth(actual, ctx)
    return None if planned is None else _actual_net_worth(actual, ctx) - planned


def _actual_warnings(actual, ctx):
    if actual.month not in ctx["projection"]:
        return ["This month is outside the projection, so there is no plan to compare it with."]
    return []


CONFIGS["goal"] = CrudConfig(
    slug="goal",
    model=Goal,
    form_class=GoalForm,
    singular="Goal",
    plural="Goals",
    page="goals",
    order_by=("target_month", "name"),
    note="Something you need to pay for by a given month. Goals are measured against the projection "
         "-- nothing is deducted from your cashflow when one falls due.",
    columns=[
        Column("Goal", TEXT, lambda o, c: o.name),
        Column("Target", MONEY, lambda o, c: o.target_amount),
        Column("Needed by", MONTH, lambda o, c: o.target_month),
        Column("Note", TEXT, lambda o, c: o.note),
    ],
)

CONFIGS["actual"] = CrudConfig(
    slug="actual",
    model=ActualBalance,
    form_class=ActualBalanceForm,
    singular="Check-in",
    plural="Monthly check-ins",
    page="actuals",
    add_label="Log a month",
    can_duplicate=False,
    needs_user=True,
    needs_projection=True,
    order_by=("-month",),
    row_warnings=_actual_warnings,
    note="What your accounts really held at the end of a month. Only the bank and fund balances are "
         "needed; PF and loans use the projected figures when left blank. Drift is actual net worth "
         "minus what the plan said.",
    columns=[
        Column("Month", MONTH, lambda o, c: o.month),
        Column("Bank", MONEY, lambda o, c: o.bank_balance),
        Column("Investments", MONEY, lambda o, c: o.investment_balance),
        Column("Actual net worth", MONEY, _actual_net_worth, derived=True,
               info="Bank + investments + PF, minus loans. PF and loans use the plan's figures if you left them blank."),
        Column("Planned", MONEY, _planned_net_worth, derived=True,
               info="Net worth the projection gave for that month."),
        Column("Drift", "drift", _actual_drift, derived=True,
               info="Actual minus planned. Positive means you are ahead of the plan."),
    ],
)


PAGE_TABLES: dict[str, list[str]] = {
    "income": ["salary", "bonus"],
    "expenses": ["expense", "onetime"],
    "loans": ["loan"],
    "investments": ["investment"],
    "retirement": ["retirement"],
    "insurance": ["insurance"],
    "goals": ["goal"],
    "actuals": ["actual"],
}


def get_config(slug: str) -> CrudConfig:
    return CONFIGS[slug]
