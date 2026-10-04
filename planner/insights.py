"""Reading the finished projection back to the user.

Everything here is derived from rows the engine already produced -- no new
arithmetic about money, only comparisons and thresholds. The thresholds are
deliberately blunt rules of thumb (they are prompts to look, not advice), and
each insight says what it saw so the number can be checked against the tables.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from .services.dates import format_month
from .services.projection import YearRow

ZERO = Decimal("0")
FI_MULTIPLE = Decimal("25")      # the "25x annual spending" rule of thumb
WARN, INFO, GOOD = "warn", "info", "good"

NET_WORTH_STEPS = [
    Decimal(x) for x in (
        500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000,
        20_000_000, 50_000_000, 100_000_000, 250_000_000,
    )
]


def _inr_short(value: Decimal) -> str:
    """₹25 L / ₹1.5 Cr, for sentences."""
    size = abs(value)
    if size >= Decimal("10000000"):
        text = f"{(size / Decimal('10000000')).quantize(Decimal('0.01')).normalize():f} Cr"
    elif size >= Decimal("100000"):
        text = f"{(size / Decimal('100000')).quantize(Decimal('0.1')).normalize():f} L"
    else:
        text = f"{size:,.0f}"
    return f"{'-' if value < 0 else ''}₹{text}"


@dataclass
class Insight:
    level: str
    title: str
    body: str
    link: str = ""        # url name to jump to
    link_label: str = ""


# --------------------------------------------------------------------------
# Financial independence
# --------------------------------------------------------------------------

def _annual_spend(year: YearRow) -> Decimal:
    """Living costs plus insurance for the year, scaled up if the year is partial.

    EMIs are left out (they end), SIPs are saving not spending, and one-time
    purchases are not an ongoing cost.
    """
    spend = year.living_expenses + year.insurance
    if year.months and year.months < 12:
        spend = spend / year.months * 12
    return spend


def financial_independence(years: list[YearRow]) -> dict:
    """First year your funds alone reach 25x that year's spending.

    Funds only: PF is locked and the bank is the emergency buffer. This is the
    classic rule of thumb for a sustainable withdrawal, not a guarantee.
    """
    if not years:
        return {"year": None, "progress": None}
    first = years[0]
    spend = _annual_spend(first)
    progress = None
    if spend > 0:
        progress = int(max(ZERO, min(Decimal(100), first.investment_balance / (spend * FI_MULTIPLE) * 100)))
    for year in years:
        need = _annual_spend(year) * FI_MULTIPLE
        if need > 0 and year.investment_balance >= need:
            return {"year": year.year, "progress": progress, "need": need}
    return {"year": None, "progress": progress}


# --------------------------------------------------------------------------
# Milestones
# --------------------------------------------------------------------------

def milestones(rows, summary, years, planner) -> list[dict]:
    """Dated moments to look forward to, in order. At most seven."""
    if not rows:
        return []
    items = []
    start_nw = rows[0].net_worth
    top = max(row.net_worth for row in rows)

    if summary.ef_reached_month and summary.ef_reached_month > rows[0].month:
        items.append({"month": summary.ef_reached_month, "label": "Emergency fund reached", "kind": "ef"})
    if summary.debt_free_month and summary.debt_free_month > rows[0].month:
        items.append({"month": summary.debt_free_month, "label": "Debt-free", "kind": "debt"})

    if start_nw < 0:
        for row in rows:
            if row.net_worth >= 0:
                items.append({"month": row.month, "label": "Net worth turns positive", "kind": "nw"})
                break

    shown = 0
    for step in NET_WORTH_STEPS:
        if step <= start_nw or step > top:
            continue
        crossed = next((row for row in rows if row.net_worth >= step), None)
        if crossed:
            items.append({"month": crossed.month, "label": f"Net worth {_inr_short(step)}", "kind": "nw"})
            shown += 1
        if shown == 4:
            break

    fi = financial_independence(years)
    if fi["year"]:
        items.append({
            "month": dt.date(fi["year"], 12, 1),
            "label": "Financial independence",
            "kind": "fi",
            "hint": f"Funds reach 25x a year's spending ({_inr_short(fi['need'])})",
        })

    items.sort(key=lambda item: item["month"])
    return items[:7]


# --------------------------------------------------------------------------
# Heatmap of monthly surplus
# --------------------------------------------------------------------------

def heatmap(rows) -> dict:
    """Years down the side, months across, each coloured by that month's surplus.

    Green steps for money left over, red for a deficit, scaled against the
    biggest value on each side so a single large bonus does not wash out the
    rest. Lump-sum months carry a marker.
    """
    if not rows:
        return {"years": [], "labels": []}
    biggest_gain = max((r.net_surplus for r in rows if r.net_surplus > 0), default=ZERO)
    biggest_loss = min((r.net_surplus for r in rows if r.net_surplus < 0), default=ZERO)

    def level(value: Decimal) -> str:
        if value > 0 and biggest_gain > 0:
            step = min(4, max(1, int(value / biggest_gain * 4 + Decimal("0.999"))))
            return f"p{step}"
        if value < 0 and biggest_loss < 0:
            step = min(3, max(1, int(value / biggest_loss * 3 + Decimal("0.999"))))
            return f"n{step}"
        return "z"

    by_year: dict[int, dict[int, object]] = {}
    for row in rows:
        by_year.setdefault(row.month.year, {})[row.month.month] = row

    out = []
    for year in sorted(by_year):
        cells = []
        for month in range(1, 13):
            row = by_year[year].get(month)
            if row is None:
                cells.append({"empty": True})
                continue
            cells.append({
                "empty": False,
                "level": level(row.net_surplus),
                "lumpy": row.is_lumpy,
                "shortfall": row.cash_shortfall,
                "label": format_month(row.month),
                "value": row.net_surplus,
            })
        out.append({"year": year, "cells": cells})
    return {"years": out, "labels": ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]}


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------

def _savings_rate(years: list[YearRow]) -> tuple[int, int] | None:
    """(year, percent) of income that was saved or invested, for the first year."""
    if not years or not years[0].inflow:
        return None
    first = years[0]
    return first.year, int((first.surplus + first.sip) / first.inflow * 100)


def build_insights(rows, years, summary, planner, user) -> list[Insight]:
    if not rows or not any(r.total_inflow or r.total_outflow for r in rows):
        return []
    out: list[Insight] = []
    first = rows[0]

    if summary.shortfall_months:
        out.append(Insight(
            WARN, f"The bank runs dry in {format_month(summary.first_shortfall_month)}",
            f"{summary.shortfall_months} month{'s' if summary.shortfall_months != 1 else ''} end with a negative "
            f"bank balance. Fund units are never sold to cover it, so this is a real gap in the plan.",
            "cashflow", "See the months",
        ))

    # Lump sums landing soon, against what is in the bank right now.
    soon = sum((item.amount for row in rows[:3] for item in row.lumpy_items), ZERO)
    if soon and first.bank_balance < soon:
        out.append(Insight(
            WARN, f"{_inr_short(soon)} of lump-sum costs land in the next 3 months",
            f"That is more than the {_inr_short(first.bank_balance)} in the bank today.",
            "expenses", "Review expenses",
        ))

    rate = _savings_rate(years)
    if rate:
        year, pct = rate
        if pct < 15:
            out.append(Insight(
                WARN, f"You are saving about {pct}% of income",
                f"In {year}, what is left over plus SIPs comes to {pct}% of what comes in. "
                f"A common aim is 20% or more.",
                "whatif", "Try a what-if",
            ))
        elif pct >= 30:
            out.append(Insight(GOOD, f"You are saving about {pct}% of income",
                               f"In {year}, surplus and SIPs together are {pct}% of income -- a strong rate."))

    if first.total_inflow:
        burden = first.total_emi / first.total_inflow * 100
        if burden >= 40:
            out.append(Insight(WARN, f"EMIs take {int(burden)}% of income",
                               "Lenders and planners commonly treat 40% as the upper limit.", "loans", "See loans"))
        elif burden >= 30:
            out.append(Insight(INFO, f"EMIs take {int(burden)}% of income",
                               "Above the 30% many planners prefer to stay under.", "loans", "See loans"))

    if first.net_worth < 0:
        turn = next((r for r in rows if r.net_worth >= 0), None)
        body = (f"Loans are larger than everything you hold today. On this plan net worth turns positive "
                f"in {format_month(turn.month)}.") if turn else \
            "Loans are larger than everything you hold, and net worth stays negative across this projection."
        out.append(Insight(INFO, "Net worth is negative today", body))

    # Idle cash: well above the emergency target while funds are assumed to earn more.
    bank_rate = Decimal(planner.bank_interest_pct)
    fund_rate = Decimal(planner.default_investment_return_pct)
    if first.ef_target > 0 and first.bank_balance > first.ef_target * Decimal("1.5") and fund_rate > bank_rate:
        spare = first.bank_balance - first.ef_target
        gain = spare * (fund_rate - bank_rate) / 100
        out.append(Insight(
            INFO, f"{_inr_short(spare)} above your emergency target sits in the bank",
            f"At {bank_rate}% it earns less than funds are assumed to (about {fund_rate}%) -- roughly "
            f"{_inr_short(gain)} a year of difference. Only you can weigh that against the risk.",
            "investments", "See investments",
        ))

    # SIPs growing faster than pay.
    last = rows[-1]
    if first.salary and last.salary and first.sip:
        was, now = first.sip / first.salary, last.sip / last.salary
        if now > Decimal("0.30") and now > was * Decimal("1.5"):
            out.append(Insight(
                WARN, "SIPs outgrow your salary",
                f"SIPs are {int(was * 100)}% of salary today but {int(now * 100)}% by {last.month.year}. "
                f"Check the step-up rates against your expected hikes.", "investments", "Review SIPs",
            ))

    if summary.ef_reached_month is None and first.ef_target > 0:
        out.append(Insight(WARN, "The emergency fund is never reached",
                           f"The bank does not get to {_inr_short(first.ef_target)} in this projection.",
                           "settings", "Check settings"))
    elif summary.ef_reached_month == first.month and first.ef_target > 0:
        out.append(Insight(GOOD, "The emergency fund is already covered",
                           f"The bank holds more than the {_inr_short(first.ef_target)} target."))

    order = {WARN: 0, INFO: 1, GOOD: 2}
    out.sort(key=lambda i: order[i.level])
    return out[:6]


def emergency_fund_meter(first, planner) -> dict:
    """How much of the emergency target the bank covers today."""
    if not first or first.ef_target <= 0:
        return {"pct": None, "months": None}
    pct = int(max(ZERO, min(Decimal(100), first.bank_balance / first.ef_target * 100)))
    months = None
    if planner.ef_target_fixed_amount is None and planner.ef_target_months:
        per_month = first.ef_target / planner.ef_target_months
        if per_month > 0:
            months = (first.bank_balance / per_month).quantize(Decimal("0.1"))
    return {"pct": pct, "months": months}
