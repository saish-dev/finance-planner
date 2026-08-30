"""Display filters. Indian digit grouping is easy to get subtly wrong."""

from decimal import Decimal

from django.test import SimpleTestCase

from planner.templatetags.money import inr, inr_compact, month_label, pct

D = Decimal


class InrTests(SimpleTestCase):
    def test_indian_grouping(self):
        self.assertEqual(inr(D("0")), "₹0")
        self.assertEqual(inr(D("999")), "₹999")
        self.assertEqual(inr(D("1000")), "₹1,000")
        self.assertEqual(inr(D("99999")), "₹99,999")
        self.assertEqual(inr(D("100000")), "₹1,00,000")
        self.assertEqual(inr(D("1250000")), "₹12,50,000")
        self.assertEqual(inr(D("10000000")), "₹1,00,00,000")
        self.assertEqual(inr(D("972215806")), "₹97,22,15,806")

    def test_negatives_and_rounding(self):
        self.assertEqual(inr(D("-45000.60")), "-₹45,001")
        self.assertEqual(inr(D("45000.40")), "₹45,000")

    def test_missing_values_render_as_a_dash(self):
        self.assertEqual(inr(None), "--")
        self.assertEqual(inr("not a number"), "--")

    def test_compact_form(self):
        self.assertEqual(inr_compact(D("9500")), "₹9,500")
        self.assertEqual(inr_compact(D("1250000")), "₹12.5 L")
        self.assertEqual(inr_compact(D("97221581")), "₹9.72 Cr")
        self.assertEqual(inr_compact(D("-5000000")), "-₹50 L")


class OtherFilterTests(SimpleTestCase):
    def test_pct_rounds_derived_precision(self):
        self.assertEqual(pct(D("12.00")), "12%")
        self.assertEqual(pct(D("7.50")), "7.5%")
        # Decimal.normalize() alone would make this "5E+1%".
        self.assertEqual(pct(D("50.00")), "50%")
        # A value-weighted blended return arrives with full Decimal precision.
        self.assertEqual(pct(D("11.4836734693877551020408163")), "11.48%")

    def test_month_label(self):
        import datetime as dt
        self.assertEqual(month_label(dt.date(2026, 1, 1)), "Jan 2026")
        self.assertEqual(month_label(None), "--")
