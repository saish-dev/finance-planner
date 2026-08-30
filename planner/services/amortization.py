"""Loan maths: NPER, PV, and the derivation chain from the spreadsheet.

Pure functions over Decimals -- no Django imports -- so they can be unit
tested and reasoned about on their own.
"""

from __future__ import annotations

import math
from decimal import Decimal


class LoanDerivationError(ValueError):
    """Raised when a loan's inputs cannot describe a real amortising loan."""


def nper(rate_per_month: Decimal, emi: Decimal, principal: Decimal) -> int:
    """Number of EMIs needed to clear `principal`, rounded up.

    Excel's NPER(rate, -emi, principal). At rate 0 this is just principal/emi.
    An EMI that does not cover the first month's interest never amortises --
    that is a user input error, not a number we should invent.
    """
    if principal <= 0:
        return 0
    if emi <= 0:
        raise LoanDerivationError("EMI must be greater than zero.")
    if rate_per_month == 0:
        return math.ceil(principal / emi)

    first_month_interest = principal * rate_per_month
    if emi <= first_month_interest:
        raise LoanDerivationError(
            "The EMI is smaller than the first month's interest, so this loan "
            "would never be repaid. Check the EMI, rate and principal."
        )
    # n = -ln(1 - P*r/EMI) / ln(1+r)
    ratio = float(1 - (principal * rate_per_month) / emi)
    return math.ceil(-math.log(ratio) / math.log(float(1 + rate_per_month)))


def pv_of_annuity(rate_per_month: Decimal, months: int, emi: Decimal) -> Decimal:
    """Present value of `months` EMIs -- i.e. the balance they would clear.

    Excel's PV(rate, nper, -emi). Used to back-solve a loan's opening balance
    when the user knows the EMI and tenure but not the outstanding principal.
    """
    if months <= 0:
        return Decimal("0")
    if rate_per_month == 0:
        return emi * Decimal(months)
    factor = (Decimal("1") + rate_per_month) ** int(months)
    return emi * (Decimal("1") - (Decimal("1") / factor)) / rate_per_month


def amortise_one_month(balance: Decimal, rate_per_month: Decimal, emi: Decimal) -> Decimal:
    """Roll a loan balance forward one month: interest accrues, then the EMI.

    Floored at zero so the final part-EMI month cannot leave a negative
    balance behind.
    """
    grown = balance * (Decimal("1") + rate_per_month)
    return max(Decimal("0"), grown - emi)
