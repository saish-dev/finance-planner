"""What-if scenarios: parse the sliders, run the engine twice, line the results up.

The engine does the arithmetic (see `Scenario` in services/projection.py);
this module only turns query-string values into a Scenario and compares two
finished projections. Nothing is saved -- the scenario lives in the URL.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .services.dates import format_month, month_start
from .services.projection import (
    Scenario,
    annual_rollup,
    blended_return_pct,
    build_projection,
    summarise,
)

ZERO = Decimal("0")


def _dec(raw, default=None):
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return default


def _month(raw):
    try:
        year, month = str(raw).split("-")[:2]
        return dt.date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None


def baseline_values(user, planner) -> dict:
    """What the sliders show when nothing has been changed."""
    blended = blended_return_pct(user.investmentholdings.all(), planner.default_investment_return_pct)
    return {
        "infl": Decimal(planner.expense_inflation_pct).quantize(Decimal("0.01")),
        "hike": Decimal(planner.default_salary_hike_pct).quantize(Decimal("0.01")),
        "ret": Decimal(blended).quantize(Decimal("0.01")),
        "bank": Decimal(planner.bank_interest_pct).quantize(Decimal("0.01")),
        "step": ZERO,
        "jl_start": "",
        "jl_months": 0,
        "md_month": "",
        "md_pct": ZERO,
    }


def parse_scenario(params, base: dict) -> tuple[Scenario, dict, bool]:
    """Turn the query string into (Scenario, values for the form, changed?).

    A slider left at its baseline value is not an override: the investment
    return in particular is a blended figure rounded for display, so passing
    it back unchanged must not nudge the projection.
    """
    values = dict(base)
    scenario = Scenario()

    def pick(key, low, high):
        raw = _dec(params.get(key))
        if raw is None:
            return None
        raw = max(low, min(high, raw))
        values[key] = raw
        return None if raw == base[key] else raw

    scenario.expense_inflation_pct = pick("infl", Decimal("0"), Decimal("25"))
    scenario.salary_hike_pct = pick("hike", Decimal("0"), Decimal("40"))
    scenario.investment_return_pct = pick("ret", Decimal("-10"), Decimal("40"))
    scenario.bank_interest_pct = pick("bank", Decimal("0"), Decimal("15"))
    step = pick("step", Decimal("-20"), Decimal("20"))
    scenario.sip_stepup_delta_pct = step or ZERO

    start = _month(params.get("jl_start"))
    months = _dec(params.get("jl_months"), ZERO)
    if start and months and months > 0:
        scenario.job_loss_start = start
        scenario.job_loss_months = int(min(months, Decimal("60")))
        values["jl_start"] = start.strftime("%Y-%m")
        values["jl_months"] = scenario.job_loss_months

    drop_month = _month(params.get("md_month"))
    drop_pct = _dec(params.get("md_pct"), ZERO)
    if drop_month and drop_pct and drop_pct > 0:
        scenario.market_drop_month = drop_month
        scenario.market_drop_pct = min(drop_pct, Decimal("90"))
        values["md_month"] = drop_month.strftime("%Y-%m")
        values["md_pct"] = scenario.market_drop_pct

    changed = (
        any(v is not None for v in (
            scenario.expense_inflation_pct, scenario.salary_hike_pct,
            scenario.investment_return_pct, scenario.bank_interest_pct,
        ))
        or scenario.sip_stepup_delta_pct != 0
        or scenario.has_job_loss
        or scenario.has_market_drop
    )
    return scenario, values, changed


@dataclass
class CompareRow:
    label: str
    kind: str            # money | month | count
    base: object
    scen: object
    good: str            # which direction is better: "up" or "down"
    foot: str = ""

    @property
    def delta(self):
        if self.kind == "month":
            if self.base is None or self.scen is None:
                return None
            return (self.scen.year - self.base.year) * 12 + (self.scen.month - self.base.month)
        if self.base is None or self.scen is None:
            return None
        return self.scen - self.base

    @property
    def verdict(self) -> str:
        """"better", "worse" or "same" -- what the delta means, not its sign."""
        delta = self.delta
        if delta is None or delta == 0:
            return "same"
        up = delta > 0
        return "better" if up == (self.good == "up") else "worse"


def _lowest_bank(rows):
    worst = min(rows, key=lambda r: r.bank_balance)
    return worst.bank_balance, worst.month


def compare(user, planner, upto, scenario: Scenario) -> dict:
    base_rows = build_projection(user, upto_year=upto)
    scen_rows = build_projection(user, upto_year=upto, scenario=scenario)
    if not base_rows:
        return {"empty": True}

    base_sum, scen_sum = summarise(base_rows), summarise(scen_rows)
    base_low, base_low_month = _lowest_bank(base_rows)
    scen_low, scen_low_month = _lowest_bank(scen_rows)
    end_year = base_rows[-1].month.year

    rows = [
        CompareRow(f"Net worth, Dec {end_year}", "money", base_sum.last.net_worth, scen_sum.last.net_worth, "up"),
        CompareRow("Investments at the end", "money", base_sum.last.investment_balance,
                   scen_sum.last.investment_balance, "up"),
        CompareRow("Lowest bank balance", "money", base_low, scen_low, "up",
                   foot=f"{format_month(base_low_month)} vs {format_month(scen_low_month)}"),
        CompareRow("Months with no cash", "count", base_sum.shortfall_months, scen_sum.shortfall_months, "down"),
        CompareRow("Emergency fund reached", "month", base_sum.ef_reached_month, scen_sum.ef_reached_month, "down"),
        CompareRow("Debt-free from", "month", base_sum.debt_free_month, scen_sum.debt_free_month, "down"),
    ]
    rows = [r for r in rows if not (r.kind == "month" and r.base is None and r.scen is None)]

    base_years, scen_years = annual_rollup(base_rows), annual_rollup(scen_rows)
    chart = {
        "years": [str(y.year) for y in base_years],
        "nwBase": [float(y.net_worth) for y in base_years],
        "nwScen": [float(y.net_worth) for y in scen_years],
        "months": [format_month(r.month) for r in base_rows],
        "bankBase": [float(r.bank_balance) for r in base_rows],
        "bankScen": [float(r.bank_balance) for r in scen_rows],
        "event": {
            "jobLoss": [format_month(scenario.job_loss_start),
                        format_month(month_start(_add(scenario.job_loss_start, scenario.job_loss_months - 1)))]
            if scenario.has_job_loss else None,
            "marketDrop": format_month(scenario.market_drop_month) if scenario.has_market_drop else None,
        },
    }
    headline = rows[0]
    return {
        "empty": False,
        "rows": rows,
        "headline": headline,
        "end_year": end_year,
        "chart": chart,
        "chart_json": json.dumps(chart),
        "ran_dry": scen_sum.shortfall_months > base_sum.shortfall_months,
        "scen_shortfall_start": scen_sum.first_shortfall_month,
    }


def _add(date, months):
    from .services.dates import add_months
    return add_months(date, months)


# Quick starting points. Each is a dict of query-string values layered over
# the baseline, so the sliders land where the preset says.
def presets(base: dict, today_month: dt.date) -> list[dict]:
    soon = (today_month.replace(day=1))
    next_year = dt.date(soon.year + 1, soon.month, 1).strftime("%Y-%m")
    return [
        {"label": "Lose your job for 6 months", "params": {"jl_start": next_year, "jl_months": 6}},
        {"label": "Market falls 25%", "params": {"md_month": next_year, "md_pct": 25}},
        {"label": "Inflation 2 points higher", "params": {"infl": base["infl"] + 2}},
        {"label": "Returns 3 points lower", "params": {"ret": base["ret"] - 3}},
        {"label": "No salary hikes", "params": {"hike": 0}},
        {"label": "Step SIPs up 5 points more", "params": {"step": 5}},
    ]
