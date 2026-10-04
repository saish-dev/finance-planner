"""Backup export and restore."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from planner import backup
from planner.middleware import get_owner
from planner.models import ActualBalance, Expense, Goal, Loan, PlannerSettings, SalaryChange

from .factories import make_expense, make_holding, make_loan, make_planner, make_salary, make_user

D = Decimal


class BackupTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        make_planner(self.user, project_to_year=2031, expense_inflation_pct=D("7.5"))
        make_salary(self.user, amount="120000")
        make_expense(self.user, name="Rent", amount="30000")
        make_loan(self.user, name="Home")
        make_holding(self.user, name="Index fund", current_value=D("250000"))
        Goal.objects.create(user=self.user, name="Car", target_amount=D("900000"), target_month=dt.date(2028, 5, 1))
        ActualBalance.objects.create(user=self.user, month=dt.date(2026, 3, 1), bank_balance=D("55000"))

    def test_export_is_valid_json_with_every_table(self):
        payload = json.loads(backup.export_data(self.user))
        self.assertEqual(payload["app"], "finance-planner")
        self.assertEqual(len(payload["tables"]), len(backup.MODELS))
        self.assertEqual(len(payload["tables"]["planner.expense"]), 1)

    def test_export_only_contains_this_users_rows(self):
        other = make_user("someone-else")
        make_expense(other, name="Secret")
        self.assertNotIn("Secret", backup.export_data(self.user))

    def test_round_trip_restores_everything_after_a_wipe(self):
        raw = backup.export_data(self.user).encode()
        for model in (SalaryChange, Expense, Loan, Goal, ActualBalance):
            model.objects.filter(user=self.user).delete()
        PlannerSettings.objects.filter(user=self.user).delete()
        counts = backup.restore_data(self.user, raw)
        self.assertEqual(counts["expenses"], 1)
        self.assertEqual(Expense.objects.get(user=self.user).name, "Rent")
        self.assertEqual(Loan.objects.get(user=self.user).name, "Home")
        self.assertEqual(PlannerSettings.objects.get(user=self.user).expense_inflation_pct, D("7.50"))
        self.assertEqual(ActualBalance.objects.get(user=self.user).bank_balance, D("55000.00"))

    def test_restore_replaces_rather_than_merges(self):
        raw = backup.export_data(self.user).encode()
        make_expense(self.user, name="Added after the backup")
        backup.restore_data(self.user, raw)
        self.assertEqual(list(Expense.objects.filter(user=self.user).values_list("name", flat=True)), ["Rent"])

    def test_restoring_into_another_user_claims_the_rows_for_them(self):
        raw = backup.export_data(self.user).encode()
        newcomer = make_user("newcomer")
        backup.restore_data(newcomer, raw)
        self.assertEqual(Expense.objects.filter(user=newcomer).count(), 1)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)  # untouched

    def test_a_bad_file_changes_nothing(self):
        before = Expense.objects.filter(user=self.user).count()
        for junk in (b"not json", b"[]", json.dumps({"app": "other"}).encode(),
                     json.dumps({"app": "finance-planner", "version": 99, "tables": {}}).encode(),
                     json.dumps({"app": "finance-planner", "version": 1, "tables": {"auth.user": []}}).encode()):
            with self.assertRaises(backup.BackupError):
                backup.restore_data(self.user, junk)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), before)

    def test_a_file_that_breaks_halfway_rolls_back(self):
        payload = json.loads(backup.export_data(self.user))
        payload["tables"]["planner.loan"][0]["fields"]["monthly_emi"] = "not a number"
        with self.assertRaises(backup.BackupError):
            backup.restore_data(self.user, json.dumps(payload).encode())
        self.assertTrue(Expense.objects.filter(user=self.user, name="Rent").exists())
        self.assertTrue(Loan.objects.filter(user=self.user, name="Home").exists())

    def test_a_row_filed_under_the_wrong_table_is_refused(self):
        payload = json.loads(backup.export_data(self.user))
        payload["tables"]["planner.goal"].append(payload["tables"]["planner.expense"][0])
        with self.assertRaises(backup.BackupError):
            backup.restore_data(self.user, json.dumps(payload).encode())


class BackupViewTests(TestCase):
    def setUp(self):
        self.user = get_owner()
        make_planner(self.user)
        make_expense(self.user, name="Rent")

    def test_download_is_an_attachment(self):
        response = self.client.get(reverse("backup_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(b"Rent", response.content)

    def test_upload_restores_and_reports(self):
        raw = backup.export_data(self.user).encode()
        Expense.objects.all().delete()
        response = self.client.post(reverse("backup_import"),
                                    {"backup": SimpleUploadedFile("b.json", raw)}, follow=True)
        self.assertContains(response, "Backup restored")
        self.assertTrue(Expense.objects.filter(name="Rent").exists())

    def test_bad_upload_shows_an_error(self):
        response = self.client.post(reverse("backup_import"),
                                    {"backup": SimpleUploadedFile("b.json", b"nope")}, follow=True)
        self.assertContains(response, "not valid JSON")

    def test_upload_with_no_file_is_handled(self):
        response = self.client.post(reverse("backup_import"), follow=True)
        self.assertContains(response, "Choose a backup file")
