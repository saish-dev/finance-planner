"""Goals measured against the projection.

A goal is "I need ₹X by month M". Nothing is deducted from the cashflow when
it falls due -- instead we ask whether the bank and the funds, taken together,
hold enough by then. PF is left out on purpose: it is locked money.

Goals compete for the same pot, so they are taken in date order and each one
sees the pot net of the goals that fall due before it. That deliberately
ignores the growth those earlier amounts would have earned had they stayed
invested -- slightly pessimistic, and easy to check by hand.
"""

from __future__ import annotations

from decimal import Decimal

from .models import Goal
from .services.dates import month_diff
from .services.money import monthly_rate, q2
from .services.projection import blended_return_pct, build_projection

ZERO = Decimal("0")

ON_TRACK, SHORT, PAST, BEYOND = "on", "short", "past", "beyond"


def _extra_sip_needed(shortfall: Decimal, months: int, annual_pct: Decimal) -> Decimal:
    """Monthly contribution that would close `shortfall` in `months` months."""
    if shortfall <= 0 or months <= 0:
        return ZERO
    rate = monthly_rate(annual_pct)
    if rate == 0:
        return q2(shortfall / months)
    factor = ((Decimal(1) + rate) ** months - 1) / rate
    return q2(shortfall / factor)


def analyse(user, planner) -> dict:
    goals = list(Goal.objects.filter(user=user))
    if not goals:
        return {"cards": [], "on_track": 0, "counted": 0, "extra_sip": ZERO, "total_target": ZERO}

    last_year = max(goal.target_month.year for goal in goals)
    rows = build_projection(user, upto_year=last_year)
    by_month = {row.month: row for row in rows}
    pct = blended_return_pct(user.investmentholdings.all(), planner.default_investment_return_pct)

    cards = []
    earlier = ZERO
    for goal in goals:  # model ordering is by target month
        target = Decimal(goal.target_amount)
        card = {
            "goal": goal,
            "target": target,
            "month": goal.target_month,
            "note": goal.note,
            "earlier": earlier,
            "projected": None,
            "shortfall": ZERO,
            "extra_sip": ZERO,
            "pct": 0,
            "status": ON_TRACK,
        }
        row = by_month.get(goal.target_month)
        if goal.target_month < planner.start_month:
            card["status"] = PAST
        elif row is None:
            card["status"] = BEYOND
        else:
            pot = row.bank_balance + row.investment_balance - earlier
            card["projected"] = pot
            card["shortfall"] = max(ZERO, target - pot)
            card["pct"] = int(max(ZERO, min(Decimal(100), pot / target * 100))) if target > 0 else 100
            card["status"] = ON_TRACK if card["shortfall"] == 0 else SHORT
            card["extra_sip"] = _extra_sip_needed(card["shortfall"], month_diff(row.month, planner.start_month) + 1, pct)
            earlier += target
        cards.append(card)

    counted = [c for c in cards if c["status"] in (ON_TRACK, SHORT)]
    return {
        "cards": cards,
        "on_track": sum(1 for c in counted if c["status"] == ON_TRACK),
        "counted": len(counted),
        "extra_sip": sum((c["extra_sip"] for c in counted), ZERO),
        "total_target": sum((c["target"] for c in cards), ZERO),
    }
