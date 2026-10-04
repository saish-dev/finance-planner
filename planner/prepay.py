"""Loan prepayment: what an extra payment saves.

A standalone amortisation, not the projection: it replays the loan from its
opening balance with and without the extra money and compares the two. The
projection itself is untouched -- to make an extra payment real in the
cashflow, raise the EMI on the Loans page.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .services.dates import add_months
from .services.money import q2

ZERO = Decimal("0")
MAX_MONTHS = 1200   # a hard stop so a loan that never clears cannot loop forever


@dataclass
class Payoff:
    months: int
    interest: Decimal
    paid: Decimal
    last_month: dt.date | None
    balances: list[Decimal]     # month-end balance, one per month
    cleared: bool               # False if the EMI never covers the interest


def simulate(balance: Decimal, rate: Decimal, emi: Decimal, start: dt.date,
             extra: Decimal = ZERO, lump: Decimal = ZERO, lump_month: dt.date | None = None) -> Payoff:
    """Roll a loan forward until it is paid off.

    Interest accrues first, then the payment is taken, the same order the
    projection uses. The final payment is capped at what is owed.
    """
    balance = q2(balance)
    interest_total = paid_total = ZERO
    balances: list[Decimal] = []
    month = start
    count = 0
    while balance > Decimal("0.005") and count < MAX_MONTHS:
        interest = q2(balance * rate)
        payment = emi + extra + (lump if lump_month and month == lump_month else ZERO)
        payment = min(payment, balance + interest)
        if payment <= interest and extra == 0 and lump == 0 and payment < balance + interest:
            return Payoff(count, interest_total, paid_total, None, balances, cleared=False)
        balance = q2(balance + interest - payment)
        interest_total += interest
        paid_total += payment
        balances.append(balance)
        count += 1
        month = add_months(month, 1)
    return Payoff(
        months=count, interest=q2(interest_total), paid=q2(paid_total),
        last_month=add_months(start, count - 1) if count else None,
        balances=balances, cleared=balance <= Decimal("0.005"),
    )


def _dec(raw) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
        return value if value > 0 else ZERO
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return ZERO


def _month(raw):
    try:
        year, month = str(raw).split("-")[:2]
        return dt.date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None


def compare(loan, planner, params) -> dict:
    """Baseline versus extra payments, from the query-string values."""
    start = loan.seed_month(planner.start_month)
    opening = loan.opening_balance(planner.start_month)
    rate = loan.rate_per_month
    emi = Decimal(loan.monthly_emi)

    extra = _dec(params.get("extra"))
    lump = _dec(params.get("lump"))
    lump_month = _month(params.get("lump_month")) or start
    if opening <= 0:
        return {"empty": True}

    base = simulate(opening, rate, emi, start)
    new = simulate(opening, rate, emi, start, extra=extra, lump=lump, lump_month=lump_month)
    changed = bool(extra or lump)
    longest = max(len(base.balances), len(new.balances))

    def padded(balances):
        return [float(b) for b in balances] + [0.0] * (longest - len(balances))

    chart = {
        "labels": [(add_months(start, i)).strftime("%b %Y") for i in range(longest)],
        "base": padded(base.balances),
        "new": padded(new.balances),
    }
    return {
        "empty": False,
        "changed": changed,
        "extra": extra,
        "lump": lump,
        "base": base,
        "new": new,
        "interest_saved": q2(base.interest - new.interest) if base.cleared and new.cleared else None,
        "months_saved": base.months - new.months if base.cleared and new.cleared else None,
        "never_clears": not base.cleared,
        "chart": json.dumps(chart),
    }


def final_emi(loan, planner_start: dt.date) -> tuple[Decimal, Decimal] | None:
    """(the last EMI, how much bigger it is than the regular one), if it is bigger.

    The projection makes the last EMI whatever it takes to clear the loan. When
    the EMI and tenure entered do not quite clear the balance entered, that last
    payment is larger than the regular EMI -- worth telling the user, because it
    is a bump in that month's cashflow. Returns None when the last EMI is
    ordinary (or the loan has no derivable end date).
    """
    last = loan.last_emi_month(planner_start)
    if last is None:
        return None
    balance = q2(loan.opening_balance(planner_start))
    rate, emi = loan.rate_per_month, Decimal(loan.monthly_emi)
    month = loan.seed_month(planner_start)
    while month < last and balance > 0:
        interest = q2(balance * rate)
        balance = q2(balance + interest - min(emi, balance + interest))
        month = add_months(month, 1)
    if balance <= 0 or month != last:
        return None
    payoff = q2(balance * (Decimal(1) + rate))
    extra = q2(payoff - emi)
    return (payoff, extra) if extra > Decimal("0.50") else None
