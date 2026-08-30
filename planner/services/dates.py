"""Month arithmetic.

Every date in this app means "a month", stored as the 1st of that month.
Keeping all the off-by-one-prone arithmetic in one place is deliberate: the
month-boundary bugs the spec warns about (SIP step-ups, loan last-EMI months,
quarterly premiums) all come from ad-hoc date math scattered around.
"""

from __future__ import annotations

import datetime as dt


def month_start(value: dt.date) -> dt.date:
    """Normalise any date to the 1st of its month."""
    return dt.date(value.year, value.month, 1)


def add_months(value: dt.date, months: int) -> dt.date:
    """Shift a month by `months` (may be negative). Always returns a 1st."""
    total = (value.year * 12 + (value.month - 1)) + months
    return dt.date(total // 12, total % 12 + 1, 1)


def month_diff(later: dt.date, earlier: dt.date) -> int:
    """Whole months from `earlier` to `later`. Negative if `later` is before."""
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def years_elapsed(later: dt.date, earlier: dt.date) -> int:
    """Completed 12-month blocks between two months, floored at 0.

    Month 0..11 -> 0, month 12..23 -> 1. This is the anchor used for both
    expense inflation and SIP step-ups; step-ups anchor to each fund's own
    sip_start_month, never to the projection start.
    """
    return max(0, month_diff(later, earlier) // 12)


def month_range(start: dt.date, end: dt.date):
    """Yield every month from `start` through `end`, inclusive."""
    cursor = month_start(start)
    last = month_start(end)
    while cursor <= last:
        yield cursor
        cursor = add_months(cursor, 1)


def format_month(value: dt.date) -> str:
    return value.strftime("%b %Y")
