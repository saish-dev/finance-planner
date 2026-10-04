"""Headline figures shown above each data page.

Three tiles per page: the first is the dark hero tile, the second the mint
highlight, the third a plain one. Everything is derived from the rows the
page already lists, so there is nothing to store or keep in sync.
"""

from __future__ import annotations

from decimal import Decimal

from . import prepay
from .models import (
    Expense,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    OneTimeExpense,
    RetirementAccount,
    SalaryChange,
)

ZERO = Decimal("0")


def _tile(label, value, foot="", kind="money"):
    """`kind` is "money" (rendered with the inr filter), "month" or "text"."""
    return {"label": label, "value": value, "foot": foot, "kind": kind}


def _plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"


def _income(user, planner):
    changes = list(SalaryChange.objects.filter(user=user).order_by("effective_month"))
    current = ZERO
    for change in changes:
        if change.effective_month <= planner.start_month or current == ZERO:
            current = change.monthly_in_hand
    extras = list(IncomeExtra.objects.filter(user=user))
    bonus_total = sum((extra.annual_amount for extra in extras), ZERO)
    return [
        _tile("In-hand salary a month", current,
              f"From {_plural(len(changes), 'salary entry', 'salary entries')}"),
        _tile("Bonuses a year", bonus_total, _plural(len(extras), "bonus or extra", "bonuses and extras")),
        _tile("Total a year", current * 12 + bonus_total, "Twelve months of salary plus bonuses"),
    ]


def _expenses(user, planner):
    living = list(Expense.objects.filter(user=user))
    one_time = list(OneTimeExpense.objects.filter(user=user))
    yearly_cost = sum((e.annual_cost for e in living), ZERO)
    non_monthly = [e for e in living if e.payments_per_year != 12]
    one_time_total = sum((o.amount for o in one_time), ZERO)
    return [
        _tile("Living cost a year", yearly_cost, "Before inflation"),
        _tile("Average a month", yearly_cost / 12, "A yearly cost spread out; the cashflow itself is never smoothed"),
        _tile("Lump-sum items", len(non_monthly) + len(one_time),
              f"{len(non_monthly)} non-monthly, {len(one_time)} one-time ({one_time_total:,.0f} in all)",
              kind="text"),
    ]


def _loans(user, planner):
    loans = list(Loan.objects.filter(user=user))
    start = planner.start_month
    outstanding = sum((loan.opening_balance(start) for loan in loans), ZERO)
    ends = [end for end in (loan.last_emi_month(start) for loan in loans) if end]
    # If the EMIs and tenures cannot clear the balances, the last EMI is not
    # the debt-free date -- say so rather than promise one.
    leftover = sum((prepay.residual_after_last_emi(loan, start) or ZERO for loan in loans), ZERO)
    if leftover:
        last_tile = _tile("Last EMI", max(ends) if ends else None,
                          f"But {leftover:,.0f} is still owed after the final EMIs -- see the notes under each loan",
                          kind="month")
    else:
        last_tile = _tile("Debt-free after", max(ends) if ends else None,
                          "The last EMI across every loan" if ends else "No loan has an end date yet",
                          kind="month")
    return [
        _tile("Outstanding today", outstanding, _plural(len(loans), "loan")),
        _tile("EMIs a month", sum((loan.monthly_emi for loan in loans), ZERO), "All loans together"),
        last_tile,
    ]


def _investments(user, planner):
    holdings = list(InvestmentHolding.objects.filter(user=user))
    return [
        _tile("Value today", sum((h.current_value for h in holdings), ZERO), _plural(len(holdings), "holding")),
        _tile("SIPs a month", sum((h.monthly_sip for h in holdings), ZERO), "Before each fund's step-up"),
        _tile("Holdings", len(holdings), "Funds, stocks and anything else you track", kind="text"),
    ]


def _retirement(user, planner):
    accounts = list(RetirementAccount.objects.filter(user=user))
    return [
        _tile("Corpus today", sum((a.current_balance for a in accounts), ZERO), _plural(len(accounts), "account")),
        _tile("Going in a month", sum((a.capped_monthly_total for a in accounts), ZERO),
              "Employee plus employer, after any cap"),
        _tile("Going in a year", sum((a.capped_monthly_total for a in accounts), ZERO) * 12,
              "Not spendable until you retire"),
    ]


def _insurance(user, planner):
    policies = list(InsurancePolicy.objects.filter(user=user))
    return [
        _tile("Cover (sum assured)", sum((p.sum_assured or ZERO for p in policies), ZERO), _plural(len(policies), "policy", "policies")),
        _tile("Premiums a year", sum((p.annual_cost for p in policies), ZERO), "Charged in the months they fall due"),
        _tile("Premiums a month", sum((p.monthly_equivalent for p in policies), ZERO), "Averaged, for comparison only"),
    ]


def _goals(user, planner):
    from . import goals
    result = goals.analyse(user, planner)
    cards = result["cards"]
    if not cards:
        return [
            _tile("Total to fund", ZERO, "Add a goal to see if the plan reaches it"),
            _tile("On track", "None yet", "", kind="text"),
            _tile("Extra a month needed", ZERO, "To close every shortfall"),
        ]
    off = result["counted"] - result["on_track"]
    return [
        _tile("Total to fund", result["total_target"], _plural(len(cards), "goal")),
        _tile("On track", f"{result['on_track']} of {result['counted']}",
              "Goals the bank and funds cover by their date" if result["counted"] else "No goal falls inside the projection",
              kind="text"),
        _tile("Extra a month needed", result["extra_sip"],
              f"Extra SIP to close {_plural(off, 'shortfall')}" if off else "Nothing to close, every goal is covered"),
    ]


def _actuals(user, planner):
    from . import actuals
    result = actuals.analyse(user, planner)
    if not result["entries"]:
        return [
            _tile("Latest net worth", None, "Log a month-end to start", kind="text"),
            _tile("Drift vs plan", None, "", kind="text"),
            _tile("Check-ins", 0, "", kind="text"),
        ]
    latest = result["latest"]
    drift = result["latest_drift"]
    pct = result["drift_pct"]
    return [
        _tile("Latest actual net worth", latest["actual"], format_month_label(latest["entry"].month)),
        _tile("Drift vs plan", drift if drift is not None else "No plan for that month",
              (f"{'ahead of' if drift >= 0 else 'behind'} the plan by {abs(pct)}%" if drift is not None and pct is not None else ""),
              kind="money" if drift is not None else "text"),
        _tile("Check-ins", len(result["entries"]), "Months logged so far", kind="text"),
    ]


def format_month_label(month):
    return month.strftime("%b %Y")


BUILDERS = {
    "income": _income,
    "expenses": _expenses,
    "loans": _loans,
    "investments": _investments,
    "retirement": _retirement,
    "insurance": _insurance,
    "goals": _goals,
    "actuals": _actuals,
}


def page_metrics(page: str, user, planner) -> list[dict]:
    return BUILDERS[page](user, planner)
