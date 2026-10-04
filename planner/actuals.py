"""Actual balances against the plan: the check-in chart and headline figures."""

from __future__ import annotations

import json
from decimal import Decimal

from .crud import _actual_drift, _actual_net_worth, _planned_net_worth
from .models import ActualBalance
from .services.dates import format_month
from .services.projection import build_projection

ZERO = Decimal("0")


def analyse(user, planner) -> dict:
    entries = list(ActualBalance.objects.filter(user=user).order_by("month"))
    if not entries:
        return {"entries": [], "chart_json": "", "latest": None}

    rows = build_projection(user)
    ctx = {"projection": {row.month: row for row in rows}}

    points = []
    for entry in entries:
        points.append({
            "entry": entry,
            "actual": _actual_net_worth(entry, ctx),
            "planned": _planned_net_worth(entry, ctx),
            "drift": _actual_drift(entry, ctx),
        })
    compared = [p for p in points if p["drift"] is not None]

    # Plan line over the stretch you have logged, actuals as markers on it.
    first, last = entries[0].month, entries[-1].month
    window = [row for row in rows if first <= row.month <= last]
    actual_by_month = {p["entry"].month: float(p["actual"]) for p in points}
    chart = {
        "labels": [format_month(row.month) for row in window],
        "planned": [float(row.net_worth) for row in window],
        "actual": [actual_by_month.get(row.month) for row in window],
    }
    return {
        "entries": entries,
        "points": points,
        "latest": points[-1],
        "latest_drift": compared[-1]["drift"] if compared else None,
        "latest_planned": compared[-1]["planned"] if compared else None,
        "drift_pct": (
            round(compared[-1]["drift"] / compared[-1]["planned"] * 100, 1)
            if compared and compared[-1]["planned"] else None
        ),
        "chart_json": json.dumps(chart) if len(window) > 1 else "",
    }
