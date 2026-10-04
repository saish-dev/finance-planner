"""Export and restore all of a user's planner data as one JSON file.

The file is a plain Django serialisation of every table the user owns, keyed
by model label. Restoring *replaces* what is there: the user's rows are
cleared and the file's rows created in their place, all inside one
transaction, so a bad file leaves the existing data untouched.
"""

from __future__ import annotations

import datetime as dt
import json

from django.core import serializers
from django.core.serializers.base import DeserializationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from .models import (
    Expense,
    IncomeExtra,
    InsurancePolicy,
    InvestmentHolding,
    Loan,
    OneTimeExpense,
    PlannerSettings,
    RetirementAccount,
    SalaryChange,
)

FORMAT_VERSION = 1
MAX_BYTES = 5 * 1024 * 1024

MODELS = [
    PlannerSettings, SalaryChange, IncomeExtra, Expense, OneTimeExpense, Loan,
    InvestmentHolding, RetirementAccount, InsurancePolicy,
]
BY_LABEL = {model._meta.label_lower: model for model in MODELS}


class BackupError(ValueError):
    """The uploaded file is not a usable backup. The message is shown to the user."""


def export_data(user) -> str:
    payload = {
        "app": "finance-planner",
        "version": FORMAT_VERSION,
        "exported": dt.datetime.now().isoformat(timespec="seconds"),
        "tables": {
            model._meta.label_lower: json.loads(serializers.serialize("json", model.objects.filter(user=user)))
            for model in MODELS
        },
    }
    return json.dumps(payload, indent=2, cls=DjangoJSONEncoder)


def _parse(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise BackupError("That file is too large to be a backup.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupError("That file is not valid JSON.")
    if not isinstance(payload, dict) or payload.get("app") != "finance-planner":
        raise BackupError("That does not look like a backup from this app.")
    if payload.get("version") != FORMAT_VERSION:
        raise BackupError(f"This backup is version {payload.get('version')}; this app reads version {FORMAT_VERSION}.")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BackupError("The backup has no tables in it.")
    unknown = set(tables) - set(BY_LABEL)
    if unknown:
        raise BackupError(f"The backup contains unknown data: {', '.join(sorted(unknown))}.")
    return tables


def restore_data(user, raw: bytes) -> dict[str, int]:
    """Replace the user's data with the file's. Returns rows restored per table."""
    tables = _parse(raw)
    counts: dict[str, int] = {}
    try:
        with transaction.atomic():
            for model in reversed(MODELS):
                model.objects.filter(user=user).delete()
            for model in MODELS:
                records = tables.get(model._meta.label_lower, [])
                for record in records:
                    # Only this model's rows, always rewritten to belong to
                    # the restoring user and given a fresh primary key.
                    if record.get("model") != model._meta.label_lower:
                        raise BackupError("The backup is inconsistent: a row is filed under the wrong table.")
                    record = {**record, "pk": None, "fields": {**record["fields"], "user": user.pk}}
                    for obj in serializers.deserialize("json", json.dumps([record])):
                        obj.object.user = user
                        obj.object.pk = None
                        obj.object.save()
                counts[model._meta.verbose_name_plural] = len(records)
    except (DeserializationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("Some of that backup could not be read, so nothing was changed.") from exc
    return counts
