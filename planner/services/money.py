"""Decimal money helpers.

Every amount in this app is a Decimal from storage through the projection
engine to the template. Floats are never used for money: over 360 months of
compounding, binary rounding drift becomes visible in the net worth figure.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    """Round to 2 decimal places, half-up (how a bank statement rounds)."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def pct_to_rate(percent: Decimal | None) -> Decimal:
    """Annual percentage (6.00) -> annual rate (0.06). None -> 0."""
    if percent is None:
        return Decimal("0")
    return Decimal(percent) / Decimal("100")


def monthly_rate(annual_percent: Decimal | None) -> Decimal:
    """Annual percentage -> simple monthly rate (annual / 12).

    Simple division, not a compounded (1+r)^(1/12)-1 conversion, because that
    is what the spreadsheet this app replaces does and the whole engine is
    meant to reproduce it faithfully.
    """
    return pct_to_rate(annual_percent) / Decimal("12")


def growth_factor(annual_percent: Decimal | None, periods: int) -> Decimal:
    """(1 + annual_rate) ** periods, for whole-year growth like inflation."""
    return (Decimal("1") + pct_to_rate(annual_percent)) ** int(periods)
