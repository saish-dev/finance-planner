"""Planning tools: what-if scenarios (more to come: goals, actuals)."""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import whatif
from .views import get_planner, requested_year


def _whatif_context(request) -> dict:
    planner = get_planner(request.user)
    base = whatif.baseline_values(request.user, planner)
    # A preset arrives as plain query values, same as a hand-moved slider.
    scenario, values, changed = whatif.parse_scenario(request.GET, base)
    upto = requested_year(request)
    result = whatif.compare(request.user, planner, upto, scenario)
    options = whatif.presets(base, planner.start_month)
    for option in options:
        option["href"] = "?" + urlencode(option["params"])
    return {
        "planner": planner,
        "values": values,
        "base": base,
        "changed": changed,
        "result": result,
        "presets": options,
        "page": "whatif",
        "title": "What-if",
    }


@require_GET
def whatif_view(request):
    return render(request, "planner/whatif.html", _whatif_context(request))


@require_GET
def whatif_results(request):
    return render(request, "planner/partials/whatif_results.html", _whatif_context(request))
