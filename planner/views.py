"""Views.

Nothing in here does financial arithmetic -- every number rendered by every
page comes out of planner.services.projection. The views' job is to pick the
horizon, run the projection once, and shape the result for a template.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .crud import PAGE_TABLES, get_config
from .forms import PlannerSettingsForm
from .models import Loan, PlannerSettings
from .services.dates import format_month
from .services.projection import (
    annual_rollup,
    blended_return_pct,
    build_projection,
    loan_schedule,
    resolve_horizon,
    summarise,
)

PAGE_TITLES = {
    "income": "Income",
    "expenses": "Expenses",
    "loans": "Loans",
    "investments": "Investments",
    "retirement": "Retirement",
    "insurance": "Insurance",
}


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def get_planner(user) -> PlannerSettings:
    """The user's settings row, created with sane defaults on first visit."""
    planner, _ = PlannerSettings.objects.get_or_create(
        user=user,
        defaults={
            "start_month": dt.date.today().replace(day=1),
            "project_to_year": dt.date.today().year + 15,
        },
    )
    return planner


def requested_year(request) -> int | None:
    raw = request.GET.get("upto") or request.POST.get("upto")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _crud_context(planner) -> dict:
    """Values the derived CRUD columns need."""
    return {
        "planner_start": planner.start_month,
        "default_return_pct": planner.default_investment_return_pct,
        "inflation_pct": planner.expense_inflation_pct,
    }


def build_table(request, slug: str, planner) -> dict:
    """Everything partials/table.html needs for one editable table."""
    config = get_config(slug)
    ctx = _crud_context(planner)
    queryset = config.model.objects.filter(user=request.user)
    if config.order_by:
        queryset = queryset.order_by(*config.order_by)

    rows = []
    for obj in queryset:
        cells = []
        for column in config.columns:
            try:
                value = column.get(obj, ctx)
            except Exception:
                # A half-entered row must never take the whole page down; the
                # row warnings below explain what is missing.
                value = None
            cells.append({
                "kind": column.kind,
                "value": value,
                "derived": column.derived,
                "align_right": column.align_right,
            })
        rows.append({
            "obj": obj,
            "cells": cells,
            "warnings": config.row_warnings(obj, ctx) if config.row_warnings else [],
            "detail_url": reverse(config.detail_url_name, args=[obj.pk]) if config.detail_url_name else "",
        })

    return {"config": config, "rows": rows, "planner": planner}


def render_table(request, slug: str, planner) -> HttpResponse:
    return render(request, "planner/partials/table.html", build_table(request, slug, planner))


def _form_for(config, request, planner, instance=None, data=None):
    kwargs = {"instance": instance}
    if config.needs_planner:
        kwargs["planner"] = planner
    return config.form_class(data, **kwargs) if data is not None else config.form_class(**kwargs)


def _render_form_row(request, config, form, planner, instance=None) -> HttpResponse:
    """Re-render the inline form, retargeting itself so errors stay in place."""
    html = render_to_string(
        "planner/partials/form_row.html",
        {"config": config, "form": form, "object": instance, "planner": planner},
        request=request,
    )
    response = HttpResponse(html)
    response["HX-Retarget"] = f"#form-row-{config.slug}"
    response["HX-Reswap"] = "outerHTML"
    return response


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def _chart_payload(years) -> str:
    """Year-end series for Chart.js.

    Decimals become floats here and only here -- at the very edge, for JSON.
    Every number that feeds a decision was computed in Decimal.
    """
    return json.dumps({
        "labels": [y.year for y in years],
        "bank": [float(y.bank_balance) for y in years],
        "investments": [float(y.investment_balance) for y in years],
        "pf": [float(y.pf_balance) for y in years],
        "loans": [float(y.total_loan_balance) for y in years],
        "netWorth": [float(y.net_worth) for y in years],
        "surplus": [float(y.surplus) for y in years],
    })


def _upcoming_lumpy(rows, count=12) -> list[dict]:
    """Non-monthly expenses/premiums due in the year ahead, soonest first.

    A quick early warning for the cash a spreadsheet-style cashflow table
    would otherwise bury in a scrolling row of numbers.
    """
    upcoming = []
    for row in rows[:count]:
        for item in row.lumpy_items:
            upcoming.append({"month": row.month, "name": item.name, "amount": item.amount})
    return upcoming


def _dashboard_context(request) -> dict:
    planner = get_planner(request.user)
    upto = requested_year(request)
    horizon = resolve_horizon(planner, upto)
    rows = build_projection(request.user, upto_year=horizon.end_year)
    years = annual_rollup(rows)

    return {
        "planner": planner,
        "horizon": horizon,
        "rows": rows,
        "years": years,
        "summary": summarise(rows),
        "chart_data": _chart_payload(years),
        "current_year": horizon.end_year,
        "blended_return": blended_return_pct(
            request.user.investmentholdings.all(), planner.default_investment_return_pct
        ),
        "has_data": bool(rows) and any(r.total_inflow or r.total_outflow for r in rows),
        "upcoming_lumpy": _upcoming_lumpy(rows),
    }


def dashboard(request):
    return render(request, "planner/dashboard.html", _dashboard_context(request))


def dashboard_partial(request):
    """The HTMX swap target behind the "Project to year" box."""
    return render(request, "planner/partials/dashboard_body.html", _dashboard_context(request))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def settings_view(request):
    planner = get_planner(request.user)
    if request.method == "POST":
        form = PlannerSettingsForm(request.POST, instance=planner)
        if form.is_valid():
            planner = form.save()
            for notice in resolve_horizon(planner).notices:
                messages.warning(request, notice)
            messages.success(request, "Settings saved. Every table and chart now uses them.")
            return redirect("settings")
    else:
        form = PlannerSettingsForm(instance=planner)

    return render(request, "planner/settings.html", {
        "form": form,
        "planner": planner,
        "max_year": planner.max_year,
    })


# --------------------------------------------------------------------------
# Data pages (income, expenses, loans, investments, insurance)
# --------------------------------------------------------------------------

def data_page(request, page: str):
    planner = get_planner(request.user)
    tables = [build_table(request, slug, planner) for slug in PAGE_TABLES[page]]
    return render(request, "planner/data_page.html", {
        "page": page,
        "title": PAGE_TITLES[page],
        "tables": tables,
        "planner": planner,
    })


@require_http_methods(["GET"])
def row_new(request, slug: str):
    config = get_config(slug)
    planner = get_planner(request.user)
    form = _form_for(config, request, planner)
    return render(request, "planner/partials/form_row.html",
                  {"config": config, "form": form, "object": None, "planner": planner})


@require_http_methods(["GET"])
def row_edit(request, slug: str, pk: int):
    config = get_config(slug)
    planner = get_planner(request.user)
    instance = get_object_or_404(config.model, pk=pk, user=request.user)
    form = _form_for(config, request, planner, instance=instance)
    return render(request, "planner/partials/form_row.html",
                  {"config": config, "form": form, "object": instance, "planner": planner})


@require_POST
def row_save(request, slug: str, pk: int | None = None):
    config = get_config(slug)
    planner = get_planner(request.user)
    instance = get_object_or_404(config.model, pk=pk, user=request.user) if pk else None

    form = _form_for(config, request, planner, instance=instance, data=request.POST)
    if not form.is_valid():
        return _render_form_row(request, config, form, planner, instance)

    obj = form.save(commit=False)
    obj.user = request.user
    obj.save()
    # Success replaces the whole table, so the derived columns (last EMI
    # month, back-solved opening balance, cover window) refresh with it.
    return render_table(request, slug, planner)


@require_POST
def row_delete(request, slug: str, pk: int):
    planner = get_planner(request.user)
    config = get_config(slug)
    get_object_or_404(config.model, pk=pk, user=request.user).delete()
    # Projections are never stored, so a deleted row simply stops appearing in
    # the next recomputation -- there is no history to repair.
    return render_table(request, slug, planner)


@require_http_methods(["GET"])
def row_table(request, slug: str):
    """Used by Cancel to drop the inline form."""
    return render_table(request, slug, get_planner(request.user))


# --------------------------------------------------------------------------
# Loan detail
# --------------------------------------------------------------------------

def loan_detail(request, pk: int):
    planner = get_planner(request.user)
    loan = get_object_or_404(Loan, pk=pk, user=request.user)
    horizon = resolve_horizon(planner, requested_year(request))
    rows = build_projection(request.user, upto_year=horizon.end_year)
    schedule = loan_schedule(loan, rows)

    chart = json.dumps({
        "labels": [format_month(entry["month"]) for entry in schedule],
        "balance": [float(entry["closing"]) for entry in schedule],
        "interest": [float(entry["interest"]) for entry in schedule],
        "principal": [float(entry["principal"]) for entry in schedule],
    })

    return render(request, "planner/loan_detail.html", {
        "planner": planner,
        "horizon": horizon,
        "loan": loan,
        "schedule": schedule,
        "chart_data": chart,
        "total_interest": sum((entry["interest"] for entry in schedule), Decimal("0.00")),
        "total_paid": sum((entry["emi"] for entry in schedule), Decimal("0.00")),
        "residual": schedule[-1]["closing"] if schedule else Decimal("0.00"),
        "opening_balance": loan.opening_balance(planner.start_month),
        "last_emi_month": loan.last_emi_month(planner.start_month),
        "months_remaining": loan.months_remaining_at(planner.start_month),
    })


# --------------------------------------------------------------------------
# Cashflow
# --------------------------------------------------------------------------

COLUMN_GROUPS = ["income", "spending", "balances"]


def active_column_groups(request) -> set:
    """Which optional blocks of cashflow columns to render.

    Balances only, by default: the table is readable on any screen and the
    other two blocks are one checkbox away. `v=1` marks a submitted form, so
    un-ticking everything means "none" rather than falling back to the default.
    """
    if request.GET.get("v"):
        return {g for g in request.GET.getlist("cols") if g in COLUMN_GROUPS}
    return {"balances"}


def cashflow(request):
    planner = get_planner(request.user)
    horizon = resolve_horizon(planner, requested_year(request))
    rows = build_projection(request.user, upto_year=horizon.end_year)
    groups = active_column_groups(request)

    years = sorted({row.year for row in rows})
    year_filter = request.GET.get("year")
    if year_filter and year_filter.isdigit() and int(year_filter) in years:
        year_filter = int(year_filter)
        rows = [row for row in rows if row.year == year_filter]
    else:
        year_filter = None

    paginator = Paginator(rows, 24)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "planner/cashflow.html", {
        "planner": planner,
        "horizon": horizon,
        "page_obj": page,
        # Per-loan EMI columns are deliberately not rendered: each one is a
        # constant, so it added width without adding information. The
        # breakdown lives on each loan's own schedule page, and the CSV export
        # still carries every per-loan column for offline analysis.
        "years": years,
        "year_filter": year_filter,
        "total_rows": paginator.count,
        "groups": groups,
        "show_income": "income" in groups,
        "show_spending": "spending" in groups,
        "show_balances": "balances" in groups,
    })


def cashflow_csv(request):
    planner = get_planner(request.user)
    horizon = resolve_horizon(planner, requested_year(request))
    rows = build_projection(request.user, upto_year=horizon.end_year)
    loans = list(Loan.objects.filter(user=request.user).order_by("name"))

    response = HttpResponse(content_type="text/csv")
    filename = f"cashflow-{horizon.start_month:%Y-%m}-to-{horizon.end_year}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    header = ["Month", "Salary", "Bonus", "Total inflow", "Living expenses", "Insurance"]
    for loan in loans:
        header += [f"{loan.name} EMI", f"{loan.name} balance"]
    header += [
        "Total EMI", "SIP", "One-time expenses", "Total outflow", "Net surplus",
        "PF employee", "PF employer", "PF balance", "EF target",
        "Bank balance", "Investments", "Total loans", "Net worth", "EF met",
    ]
    writer.writerow(header)

    for row in rows:
        line = [
            row.month.strftime("%Y-%m"), row.salary, row.bonus, row.total_inflow,
            row.living_expenses, row.insurance,
        ]
        for loan in loans:
            entry = row.loan_by_id(loan.pk)
            line += [entry.emi if entry else 0, entry.balance if entry else 0]
        line += [
            row.total_emi, row.sip, row.one_time_expense, row.total_outflow, row.net_surplus,
            row.pf_employee, row.pf_employer, row.pf_balance, row.ef_target,
            row.bank_balance, row.investment_balance, row.total_loan_balance,
            row.net_worth, "yes" if row.ef_goal_met else "no",
        ]
        writer.writerow(line)

    return response


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

def summary(request):
    planner = get_planner(request.user)
    horizon = resolve_horizon(planner, requested_year(request))
    rows = build_projection(request.user, upto_year=horizon.end_year)
    years = annual_rollup(rows)

    return render(request, "planner/summary.html", {
        "planner": planner,
        "horizon": horizon,
        "years": years,
        "summary": summarise(rows),
        "chart_data": _chart_payload(years),
        # The four outflow columns collapse into one "Out" unless asked for.
        "show_breakdown": bool(request.GET.get("detail")),
    })
