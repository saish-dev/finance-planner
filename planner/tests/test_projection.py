"""Engine tests.

The first class hand-calculates every column for a tiny scenario -- if the
formula chain is ever reordered or a rounding rule changes, these break with a
number you can check on paper. The later classes cover the cases the spec
flags as most likely to hide off-by-one-month bugs.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.test import TestCase

from planner.models import InsurancePolicy, InvestmentHolding
from planner.services.projection import (
    annual_rollup,
    blended_return_pct,
    build_projection,
    resolve_horizon,
    summarise,
)

from .factories import (
    JAN_2026,
    make_bonus,
    make_expense,
    make_holding,
    make_loan,
    make_one_time_expense,
    make_pf,
    make_planner,
    make_policy,
    make_salary,
    make_user,
)

D = Decimal


def annuity(rate: str, periods: int) -> Decimal:
    """((1+r)^n - 1) / r -- future value of 1 per period."""
    r = D(rate)
    return ((D(1) + r) ** periods - 1) / r


class HandCalculatedScenarioTests(TestCase):
    """Salary 1,00,000 | expenses 40,000 | 1 loan | 1 fund | no inflation.

    Month 1 by hand:
        inflow   100000
        outflow   40000 living + 10000 EMI + 10000 SIP = 60000
        surplus   40000
        loan      100000 * 1.01 - 10000            =  91000
        bank      0 + 40000 surplus, uncapped      =  40000
        invest    0 + 10000 SIP (the bank balance is not units bought) = 10000
        net worth 40000 + 10000 - 91000            = -41000
    """

    def setUp(self):
        self.user = make_user()
        self.planner = make_planner(self.user)
        make_salary(self.user)
        make_expense(self.user)
        make_loan(self.user)
        make_holding(self.user)
        self.rows = build_projection(self.user)

    def test_projection_covers_start_month_to_december(self):
        self.assertEqual(len(self.rows), 12)
        self.assertEqual(self.rows[0].month, JAN_2026)
        self.assertEqual(self.rows[-1].month, dt.date(2026, 12, 1))

    def test_month_1_every_column(self):
        row = self.rows[0]
        self.assertEqual(row.salary, D("100000.00"))
        self.assertEqual(row.bonus, D("0.00"))
        self.assertEqual(row.total_inflow, D("100000.00"))
        self.assertEqual(row.living_expenses, D("40000.00"))
        self.assertEqual(row.insurance, D("0.00"))
        self.assertEqual(row.total_emi, D("10000.00"))
        self.assertEqual(row.sip, D("10000.00"))
        self.assertEqual(row.total_outflow, D("60000.00"))
        self.assertEqual(row.net_surplus, D("40000.00"))
        self.assertEqual(row.total_loan_balance, D("91000.00"))
        # No cap: the whole 40000 surplus lands straight in the bank.
        self.assertEqual(row.bank_balance, D("40000.00"))
        # The SIP alone -- the bank balance is not units you bought.
        self.assertEqual(row.investment_balance, D("10000.00"))
        self.assertEqual(row.net_worth, D("-41000.00"))

    def test_month_2_and_3(self):
        # Loan: 91000*1.01-10000 = 81910 ; 81910*1.01-10000 = 72729.10
        self.assertEqual(self.rows[1].total_loan_balance, D("81910.00"))
        self.assertEqual(self.rows[2].total_loan_balance, D("72729.10"))
        # Investments (SIP only): 10000*1.01+10000 = 20100 ; then 30301
        self.assertEqual(self.rows[1].investment_balance, D("20100.00"))
        self.assertEqual(self.rows[2].investment_balance, D("30301.00"))
        # Bank (0% interest in this scenario): 40000 accumulates a month.
        self.assertEqual(self.rows[1].bank_balance, D("80000.00"))
        self.assertEqual(self.rows[2].bank_balance, D("120000.00"))
        # 120000 + 30301 - 72729.10
        self.assertEqual(self.rows[2].net_worth, D("77571.90"))

    def test_final_emi_is_capped_at_what_is_actually_owed(self):
        # 1,00,000 at 12% with a 10,000 EMI clears in month 11, and that last
        # instalment is the payoff amount, not another full 10,000.
        emis = [row.total_emi for row in self.rows]
        self.assertEqual(emis[9], D("10000.00"))
        self.assertEqual(emis[10], D("5898.48"))
        self.assertEqual(emis[11], D("0.00"))
        self.assertEqual(self.rows[10].total_loan_balance, D("0.00"))

    def test_per_loan_columns_are_exposed(self):
        row = self.rows[0]
        self.assertEqual(len(row.loans), 1)
        self.assertEqual(row.loans[0].name, "Loan")
        self.assertEqual(row.loans[0].emi, D("10000.00"))
        self.assertEqual(row.loans[0].balance, D("91000.00"))


class ClosedFormTests(TestCase):
    """Longer horizons checked against the annuity formula, not by hand."""

    def test_investment_balance_at_months_3_12_and_24(self):
        user = make_user()
        make_planner(user, project_to_year=2028)
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        make_holding(user, monthly_sip=D("10000"), expected_return_pct=D("12"))
        rows = build_projection(user)

        # The fund receives the 10,000 SIP and compounds it; the 50,000
        # surplus accumulates in the bank, which earns 0% in this scenario.
        for months in (3, 12, 24):
            row = rows[months - 1]
            expected = D("10000") * annuity("0.01", months)
            self.assertAlmostEqual(
                row.investment_balance, expected, delta=D("1.00"),
                msg=f"investment balance at month {months}",
            )
            self.assertEqual(row.bank_balance, D("50000") * months)
            # No loans, so net worth is bank + fund.
            self.assertEqual(row.net_worth, row.bank_balance + row.investment_balance)

    def test_loan_balance_at_months_3_12_and_24(self):
        user = make_user()
        make_planner(user, project_to_year=2028)
        make_salary(user, amount="200000")
        make_loan(
            user,
            principal_outstanding_today=D("500000"),
            annual_interest_pct=D("12"),
            monthly_emi=D("20000"),
            tenure_months=30,
        )
        rows = build_projection(user)

        principal, emi, r = D("500000"), D("20000"), D("0.01")
        for months in (3, 12, 24):
            expected = principal * (1 + r) ** months - emi * annuity("0.01", months)
            self.assertAlmostEqual(
                rows[months - 1].total_loan_balance, expected, delta=D("1.00"),
                msg=f"loan balance at month {months}",
            )


class SalaryTests(TestCase):
    def test_explicit_change_applies_from_its_own_month(self):
        user = make_user()
        make_planner(user)
        make_salary(user, amount="100000")
        make_salary(user, month=dt.date(2026, 7, 1), amount="150000", note="Switched company")
        rows = build_projection(user)

        self.assertEqual(rows[5].salary, D("100000.00"))  # June
        self.assertEqual(rows[6].salary, D("150000.00"))  # July
        self.assertEqual(rows[11].salary, D("150000.00"))

    def test_default_hike_lands_only_in_the_hike_month(self):
        user = make_user()
        make_planner(user, project_to_year=2027, default_salary_hike_pct=D("10"), default_hike_month=4)
        make_salary(user, amount="100000")
        rows = build_projection(user)

        self.assertEqual(rows[0].salary, D("100000.00"))   # Jan 2026
        self.assertEqual(rows[2].salary, D("100000.00"))   # Mar 2026
        self.assertEqual(rows[3].salary, D("110000.00"))   # Apr 2026
        self.assertEqual(rows[14].salary, D("110000.00"))  # Mar 2027
        self.assertEqual(rows[15].salary, D("121000.00"))  # Apr 2027

    def test_hike_does_not_fire_in_month_zero(self):
        # Starting the projection in the hike month must not immediately
        # inflate the salary the user just typed in.
        user = make_user()
        make_planner(user, start_month=dt.date(2026, 4, 1), project_to_year=2027,
                     default_salary_hike_pct=D("10"), default_hike_month=4)
        make_salary(user, month=dt.date(2026, 4, 1), amount="100000")
        rows = build_projection(user)

        self.assertEqual(rows[0].salary, D("100000.00"))
        self.assertEqual(rows[12].salary, D("110000.00"))  # Apr 2027

    def test_explicit_change_in_the_hike_month_wins_over_the_default_hike(self):
        user = make_user()
        make_planner(user, default_salary_hike_pct=D("10"), default_hike_month=4)
        make_salary(user, amount="100000")
        make_salary(user, month=dt.date(2026, 4, 1), amount="105000", note="Modest year")
        rows = build_projection(user)

        self.assertEqual(rows[3].salary, D("105000.00"))

    def test_salary_before_the_first_future_change_falls_back_to_it(self):
        user = make_user()
        make_planner(user)
        make_salary(user, month=dt.date(2026, 6, 1), amount="90000")
        rows = build_projection(user)

        self.assertEqual(rows[0].salary, D("90000.00"))
        self.assertEqual(rows[5].salary, D("90000.00"))

    def test_bonus_lands_in_its_payout_month_only(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_bonus(user, amount="120000", month=4)
        rows = build_projection(user)

        self.assertEqual(rows[3].bonus, D("120000.00"))
        self.assertEqual(rows[3].total_inflow, D("220000.00"))
        self.assertEqual(sum(r.bonus for r in rows), D("120000.00"))

    def test_bonus_split_across_a_chosen_month_range(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_bonus(user, amount="100000", month=4, split_end_month=6)  # Apr, May, Jun
        rows = build_projection(user)

        self.assertEqual(rows[2].bonus, D("0.00"))       # March -- before the window
        self.assertEqual(rows[3].bonus, D("33333.33"))   # April
        self.assertEqual(rows[4].bonus, D("33333.33"))   # May
        self.assertEqual(rows[5].bonus, D("33333.33"))   # June
        self.assertEqual(rows[6].bonus, D("0.00"))       # July -- after the window

    def test_bonus_split_wraps_across_the_calendar_year(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_bonus(user, amount="100000", month=6, split_end_month=3)  # June through March
        rows = build_projection(user)

        # 10 months hit every year: Jun-Dec plus the following Jan-Mar.
        hit_months = {1, 2, 3, 6, 7, 8, 9, 10, 11, 12}
        for row in rows:
            expected = D("10000.00") if row.month.month in hit_months else D("0.00")
            self.assertEqual(row.bonus, expected, row.month)
        self.assertEqual(sum(r.bonus for r in rows), D("100000.00"))

    def test_bonus_does_not_apply_before_its_start_month(self):
        user = make_user()
        make_planner(user, project_to_year=2027)
        make_salary(user)
        make_bonus(user, amount="120000", month=4, start_month=dt.date(2027, 1, 1))
        rows = build_projection(user)

        self.assertEqual(rows[3].bonus, D("0.00"))    # April 2026 -- before it starts
        self.assertEqual(rows[15].bonus, D("120000.00"))  # April 2027 -- now active

    def test_bonus_stops_after_its_end_month(self):
        user = make_user()
        make_planner(user, project_to_year=2028)
        make_salary(user)
        make_bonus(user, amount="120000", month=4, end_month=dt.date(2026, 12, 1))
        rows = build_projection(user)

        self.assertEqual(rows[3].bonus, D("120000.00"))  # April 2026 -- still active
        self.assertEqual(rows[15].bonus, D("0.00"))    # April 2027 -- stopped
        self.assertEqual(rows[27].bonus, D("0.00"))    # April 2028 -- stopped


class LoanTests(TestCase):
    def test_end_month_override_beats_tenure(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        loan = make_loan(user, tenure_months=12, end_month_override=dt.date(2026, 6, 1))
        rows = build_projection(user)

        self.assertEqual(loan.last_emi_month(JAN_2026), dt.date(2026, 6, 1))
        self.assertEqual(rows[5].loans[0].emi, D("10000.00"))  # June, the last EMI
        self.assertEqual(rows[6].loans[0].emi, D("0.00"))      # July, nothing
        # The EMIs stop before the loan is repaid, so a residue is left
        # standing rather than being silently written off.
        self.assertGreater(rows[6].loans[0].balance, D("0"))
        self.assertEqual(rows[6].loans[0].balance, rows[11].loans[0].balance)

    def test_opening_balance_is_back_solved_when_principal_is_blank(self):
        user = make_user()
        make_planner(user)
        loan = make_loan(user, principal_outstanding_today=None, tenure_months=12)

        # PV(1%, 12, -10000) = 1,12,550.77
        self.assertAlmostEqual(loan.opening_balance(JAN_2026), D("112550.77"), delta=D("0.50"))
        rows = build_projection(user)
        self.assertAlmostEqual(rows[0].total_loan_balance, D("103676.28"), delta=D("0.50"))
        self.assertEqual(rows[11].total_loan_balance, D("0.00"))

    def test_loan_starting_after_the_projection_start(self):
        user = make_user()
        make_planner(user, project_to_year=2027)
        make_salary(user)
        make_loan(user, start_month=dt.date(2026, 7, 1), principal_outstanding_today=D("100000"),
                  tenure_months=12)
        rows = build_projection(user)

        self.assertEqual(rows[5].total_loan_balance, D("0.00"))   # June, not yet drawn
        self.assertEqual(rows[5].total_emi, D("0.00"))
        self.assertEqual(rows[6].total_emi, D("10000.00"))        # July, first EMI
        self.assertEqual(rows[6].total_loan_balance, D("91000.00"))

    def test_several_loans_are_tracked_independently(self):
        user = make_user()
        make_planner(user)
        make_salary(user, amount="300000")
        make_loan(user, name="Car", principal_outstanding_today=D("100000"), tenure_months=12)
        make_loan(user, name="Home", principal_outstanding_today=D("500000"),
                  monthly_emi=D("20000"), tenure_months=60)
        make_loan(user, name="Education", principal_outstanding_today=D("200000"),
                  monthly_emi=D("8000"), tenure_months=36)
        rows = build_projection(user)

        self.assertEqual(len(rows[0].loans), 3)
        self.assertEqual(rows[0].total_emi, D("38000.00"))
        self.assertEqual({entry.name for entry in rows[0].loans}, {"Car", "Home", "Education"})

    def test_no_loans_at_all_does_not_crash(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        rows = build_projection(user)

        self.assertEqual(rows[0].total_emi, D("0.00"))
        self.assertEqual(rows[0].total_loan_balance, D("0.00"))
        self.assertEqual(rows[0].loans, [])


class SipTests(TestCase):
    def test_stepup_is_anchored_to_each_funds_own_sip_start(self):
        user = make_user()
        make_planner(user, project_to_year=2029)
        make_salary(user)
        # Running since the projection start -- steps up in Jan 2027.
        make_holding(user, name="Old fund", monthly_sip=D("10000"), annual_stepup_pct=D("10"))
        # Starts three years in -- its first step-up is Jan 2028, not Jan 2027.
        make_holding(user, name="New fund", monthly_sip=D("10000"), annual_stepup_pct=D("10"),
                     sip_start_month=dt.date(2027, 1, 1))
        rows = build_projection(user)

        self.assertEqual(rows[11].sip, D("10000.00"))  # Dec 2026: old fund only
        self.assertEqual(rows[12].sip, D("21000.00"))  # Jan 2027: 11000 + 10000
        self.assertEqual(rows[23].sip, D("21000.00"))  # Dec 2027: unchanged
        self.assertEqual(rows[24].sip, D("23100.00"))  # Jan 2028: 12100 + 11000
        self.assertEqual(rows[36].sip, D("25410.00"))  # Jan 2029: 13310 + 12100

    def test_sip_stops_at_its_end_month(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_holding(user, monthly_sip=D("10000"), sip_end_month=dt.date(2026, 6, 1))
        rows = build_projection(user)

        self.assertEqual(rows[5].sip, D("10000.00"))
        self.assertEqual(rows[6].sip, D("0.00"))

    def test_blended_return_is_value_weighted(self):
        user = make_user()
        make_planner(user, default_investment_return_pct=D("12"))
        make_holding(user, name="Debt", current_value=D("100000"), expected_return_pct=D("10"))
        make_holding(user, name="Equity", current_value=D("300000"), expected_return_pct=D("14"))
        holdings = list(user.investmentholdings.all())

        self.assertEqual(blended_return_pct(holdings, D("12")), D("13"))

    def test_the_return_basis_picks_which_figure_projects(self):
        user = make_user()
        make_planner(user, default_investment_return_pct=D("12"))
        holding = make_holding(
            user, expected_return_pct=D("11"), return_3y_pct=D("18"), return_5y_pct=D("22"),
        )

        holding.return_basis = InvestmentHolding.BASIS_CUSTOM
        self.assertEqual(holding.effective_return_pct(D("12")), D("11"))
        holding.return_basis = InvestmentHolding.BASIS_3Y
        self.assertEqual(holding.effective_return_pct(D("12")), D("18"))
        holding.return_basis = InvestmentHolding.BASIS_5Y
        self.assertEqual(holding.effective_return_pct(D("12")), D("22"))

    def test_a_trailing_basis_is_applied_flat_for_the_whole_horizon(self):
        user = make_user()
        make_planner(user, project_to_year=2027)
        # No income and no SIP: the balance moves only by compounding, so the
        # rate under test is the only thing the numbers can come from.
        make_holding(user, current_value=D("100000"), monthly_sip=D("0"),
                     return_basis=InvestmentHolding.BASIS_5Y, return_5y_pct=D("24"),
                     expected_return_pct=D("10"))
        rows = build_projection(user)

        # 24% a year is 2% a month, held flat: no taper, no adjustment.
        self.assertEqual(rows[0].investment_balance, D("102000.00"))
        self.assertEqual(rows[1].investment_balance, D("104040.00"))
        # 100000 * 1.02^24 is 160843.72 unrounded; the engine rounds every
        # month, so it lands a paisa lower. That gap is the rounding rule
        # working, not drift.
        self.assertEqual(rows[23].investment_balance, D("160843.71"))

    def test_a_blank_basis_falls_back_instead_of_crashing(self):
        user = make_user()
        make_planner(user, default_investment_return_pct=D("12"))
        # Basis says 5Y but the field was never filled in.
        holding = make_holding(user, return_basis=InvestmentHolding.BASIS_5Y,
                               return_5y_pct=None, expected_return_pct=D("9"))
        self.assertEqual(holding.effective_return_pct(D("12")), D("9"))
        self.assertIn("using your own estimate", holding.return_basis_label(D("12")))

        holding.expected_return_pct = None
        self.assertEqual(holding.effective_return_pct(D("12")), D("12"))
        self.assertEqual(holding.return_basis_label(D("12")), "Planner default")

    def test_blended_return_uses_each_holdings_chosen_basis(self):
        user = make_user()
        make_planner(user, default_investment_return_pct=D("12"))
        make_holding(user, name="Debt", current_value=D("100000"),
                     return_basis=InvestmentHolding.BASIS_CUSTOM, expected_return_pct=D("10"))
        make_holding(user, name="Equity", current_value=D("300000"),
                     return_basis=InvestmentHolding.BASIS_5Y, return_5y_pct=D("18"),
                     expected_return_pct=D("14"))
        holdings = list(user.investmentholdings.all())

        # (100000*10 + 300000*18) / 400000 = 16, not 13 as it would be if the
        # basis were ignored and expected_return_pct used throughout.
        self.assertEqual(blended_return_pct(holdings, D("12")), D("16"))

    def test_blended_return_falls_back_to_the_default(self):
        user = make_user()
        make_planner(user, default_investment_return_pct=D("11"))
        self.assertEqual(blended_return_pct([], D("11")), D("11"))


class RetirementTests(TestCase):
    """EPF: outside the cashflow, inside net worth, never available to spend."""

    def test_contributions_never_reduce_the_surplus(self):
        user = make_user()
        make_planner(user)
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        make_pf(user, employee_monthly=D("10000"), employer_monthly=D("10000"))
        rows = build_projection(user)

        # monthly_in_hand is already net of the employee contribution, and the
        # employer's share never reaches the bank, so the surplus is unchanged
        # at 100000 - 40000. Charging either one here would double-count it.
        self.assertEqual(rows[0].total_outflow, D("40000.00"))
        self.assertEqual(rows[0].net_surplus, D("60000.00"))
        self.assertEqual(rows[0].pf_employee, D("10000.00"))
        self.assertEqual(rows[0].pf_employer, D("10000.00"))
        self.assertEqual(rows[0].pf_contribution, D("20000.00"))

    def test_corpus_compounds_and_counts_towards_net_worth(self):
        user = make_user()
        make_planner(user, ef_target_fixed_amount=D("10000000"))  # nothing sweeps
        make_salary(user, amount="100000")
        make_pf(user, current_balance=D("500000"), employee_monthly=D("10000"),
                employer_monthly=D("10000"), annual_return_pct=D("12"))
        rows = build_projection(user)

        # 500000 * 1.01 + 20000 = 525000 ; 525000 * 1.01 + 20000 = 550250
        self.assertEqual(rows[0].pf_balance, D("525000.00"))
        self.assertEqual(rows[1].pf_balance, D("550250.00"))
        self.assertEqual(rows[0].net_worth, rows[0].bank_balance + D("525000.00"))

    def test_contributions_track_the_salary(self):
        user = make_user()
        make_planner(user, project_to_year=2027, default_salary_hike_pct=D("10"),
                     default_hike_month=4)
        make_salary(user, amount="100000")
        make_salary(user, month=dt.date(2026, 7, 1), amount="150000", note="Switch")
        make_pf(user, employee_monthly=D("10000"), employer_monthly=D("10000"))
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_contribution, D("20000.00"))   # Jan, baseline
        # Apr: the 10% default hike lifts pay to 110000, so PF moves with it.
        self.assertEqual(rows[3].pf_contribution, D("22000.00"))
        # Jul: pay jumps to 150000, i.e. 1.5x the anchor.
        self.assertEqual(rows[6].pf_contribution, D("30000.00"))

    def test_the_monthly_cap_stops_contributions_rising_with_salary(self):
        user = make_user()
        make_planner(user, project_to_year=2028, default_salary_hike_pct=D("50"),
                     default_hike_month=4)
        make_salary(user, amount="100000")
        # EPF on the statutory ceiling: 1800 each side, 3600 together.
        make_pf(user, employee_monthly=D("1800"), employer_monthly=D("1800"),
                monthly_cap=D("3600"))
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_contribution, D("3600.00"))
        # Pay jumps 50% in April but the contribution cannot follow it.
        self.assertEqual(rows[3].salary, D("150000.00"))
        self.assertEqual(rows[3].pf_contribution, D("3600.00"))
        self.assertEqual(rows[-1].pf_contribution, D("3600.00"))
        self.assertEqual(max(r.pf_contribution for r in rows), D("3600.00"))

    def test_the_cap_preserves_the_employee_employer_split(self):
        user = make_user()
        make_planner(user, project_to_year=2027, default_salary_hike_pct=D("100"),
                     default_hike_month=4)
        make_salary(user, amount="100000")
        # 3:1 split, capped well below what the salary would otherwise drive.
        make_pf(user, employee_monthly=D("3000"), employer_monthly=D("1000"),
                monthly_cap=D("2000"))
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_contribution, D("2000.00"))
        self.assertEqual(rows[0].pf_employee, D("1500.00"))
        self.assertEqual(rows[0].pf_employer, D("500.00"))
        # Still 3:1 after the salary doubles.
        self.assertEqual(rows[3].pf_contribution, D("2000.00"))
        self.assertEqual(rows[3].pf_employee, D("1500.00"))

    def test_contributions_below_the_cap_still_track_the_salary(self):
        user = make_user()
        make_planner(user, project_to_year=2027, default_salary_hike_pct=D("10"),
                     default_hike_month=4)
        make_salary(user, amount="100000")
        make_pf(user, employee_monthly=D("1000"), employer_monthly=D("1000"),
                monthly_cap=D("3600"))
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_contribution, D("2000.00"))
        self.assertEqual(rows[3].pf_contribution, D("2200.00"))   # +10%
        self.assertEqual(rows[15].pf_contribution, D("2420.00"))  # +10% again

    def test_no_cap_leaves_contributions_uncapped(self):
        user = make_user()
        make_planner(user, project_to_year=2027, default_salary_hike_pct=D("50"),
                     default_hike_month=4)
        make_salary(user, amount="100000")
        make_pf(user, employee_monthly=D("1800"), employer_monthly=D("1800"),
                monthly_cap=None)
        rows = build_projection(user)

        self.assertEqual(rows[3].pf_contribution, D("5400.00"))

    def test_a_flat_contribution_when_there_is_no_salary_to_track(self):
        user = make_user()
        make_planner(user)
        make_pf(user, employee_monthly=D("10000"), employer_monthly=D("10000"))
        rows = build_projection(user)

        # No salary entered: hold the contribution flat rather than dividing
        # by a zero anchor.
        self.assertEqual(rows[0].pf_contribution, D("20000.00"))
        self.assertEqual(rows[11].pf_contribution, D("20000.00"))

    def test_the_corpus_is_never_sold_to_cover_a_shortfall(self):
        user = make_user()
        make_planner(user, bank_balance_today=D("0"), ef_target_months=0)
        make_salary(user, amount="20000")
        make_expense(user, amount="100000")
        make_pf(user, current_balance=D("5000000"), employee_monthly=D("0"),
                employer_monthly=D("0"), annual_return_pct=D("0"))
        rows = build_projection(user)

        # 80,000 short every month with 50 lakh sitting in EPF: the bank still
        # goes negative, because you cannot spend a provident fund.
        self.assertEqual(rows[0].bank_balance, D("-80000.00"))
        self.assertTrue(rows[0].cash_shortfall)
        self.assertEqual(rows[0].pf_balance, D("5000000.00"))

    def test_the_corpus_does_not_count_towards_the_emergency_fund(self):
        user = make_user()
        make_planner(user, ef_target_months=6, bank_balance_today=D("0"))
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        make_pf(user, current_balance=D("5000000"))
        rows = build_projection(user)

        self.assertEqual(rows[0].ef_target, D("240000.00"))
        self.assertFalse(rows[0].ef_goal_met)  # 60000 in the bank, not 50 lakh

    def test_contributions_stop_at_the_end_month_but_the_corpus_keeps_growing(self):
        user = make_user()
        make_planner(user, ef_target_fixed_amount=D("10000000"))
        make_salary(user, amount="100000")
        make_pf(user, current_balance=D("100000"), employee_monthly=D("5000"),
                employer_monthly=D("5000"), annual_return_pct=D("12"),
                end_month=dt.date(2026, 6, 1))
        rows = build_projection(user)

        self.assertEqual(rows[5].pf_contribution, D("10000.00"))   # June
        self.assertEqual(rows[6].pf_contribution, D("0.00"))       # July
        # Still compounding at 1% a month with no new money going in.
        self.assertEqual(rows[7].pf_balance, q2_mul(rows[6].pf_balance))

    def test_contributions_start_late(self):
        user = make_user()
        make_planner(user, project_to_year=2027)
        make_salary(user, amount="100000")
        make_pf(user, employee_monthly=D("5000"), employer_monthly=D("5000"),
                start_month=dt.date(2026, 7, 1))
        rows = build_projection(user)

        self.assertEqual(rows[5].pf_contribution, D("0.00"))
        self.assertEqual(rows[6].pf_contribution, D("10000.00"))

    def test_two_accounts_compound_at_their_own_rates(self):
        user = make_user()
        make_planner(user, ef_target_fixed_amount=D("10000000"))
        make_salary(user, amount="100000")
        make_pf(user, name="EPF", current_balance=D("100000"), employee_monthly=D("0"),
                employer_monthly=D("0"), annual_return_pct=D("12"))
        make_pf(user, name="Old employer EPF", current_balance=D("200000"),
                employee_monthly=D("0"), employer_monthly=D("0"), annual_return_pct=D("6"))
        rows = build_projection(user)

        # 100000*1.01 + 200000*1.005 -- pooling them at one rate would give a
        # different number.
        self.assertEqual(rows[0].pf_balance, D("302000.00"))

    def test_no_retirement_accounts_leaves_every_column_at_zero(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_contribution, D("0.00"))
        self.assertEqual(rows[0].pf_balance, D("0.00"))


def q2_mul(balance):
    """One month of 1% growth, rounded the way the engine rounds."""
    from planner.services.money import q2
    return q2(balance + q2(balance * D("0.01")))


class InsuranceTests(TestCase):
    def test_quarterly_premium_repeats_every_third_month(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_policy(user, premium_amount=D("6000"), frequency=InsurancePolicy.QUARTERLY,
                    premium_month=2)
        rows = build_projection(user)

        charged = [i for i, row in enumerate(rows) if row.insurance > 0]
        self.assertEqual(charged, [1, 4, 7, 10])  # Feb, May, Aug, Nov
        self.assertEqual(rows[1].insurance, D("6000.00"))
        self.assertEqual(rows[2].insurance, D("0.00"))

    def test_half_yearly_and_monthly_frequencies(self):
        user = make_user()
        make_planner(user)
        make_salary(user)
        make_policy(user, name="Health", premium_amount=D("15000"),
                    frequency=InsurancePolicy.HALF_YEARLY, premium_month=3)
        make_policy(user, name="Motor", premium_amount=D("1000"),
                    frequency=InsurancePolicy.MONTHLY, premium_month=1)
        rows = build_projection(user)

        self.assertEqual(rows[0].insurance, D("1000.00"))
        self.assertEqual(rows[2].insurance, D("16000.00"))  # Mar: monthly + half-yearly
        self.assertEqual(rows[8].insurance, D("16000.00"))  # Sep
        self.assertEqual(sum(r.insurance for r in rows), D("42000.00"))

    def test_premium_is_not_charged_before_start_or_after_end(self):
        user = make_user()
        make_planner(user, project_to_year=2027)
        make_salary(user)
        make_policy(user, premium_amount=D("12000"), frequency=InsurancePolicy.YEARLY,
                    premium_month=1, start_month=dt.date(2026, 6, 1), end_month=dt.date(2027, 5, 1))
        rows = build_projection(user)

        self.assertEqual(rows[0].insurance, D("0.00"))    # Jan 2026, before it starts
        self.assertEqual(rows[12].insurance, D("12000.00"))  # Jan 2027
        self.assertEqual(sum(r.insurance for r in rows), D("12000.00"))

    def test_term_years_fills_in_the_end_month(self):
        user = make_user()
        make_planner(user)
        policy = make_policy(user, start_month=dt.date(2024, 4, 1))
        policy.term_years = 16
        policy.apply_term_years(JAN_2026)

        # A 16-year term from Apr 2024 covers through Mar 2040.
        self.assertEqual(policy.end_month, dt.date(2040, 3, 1))

    def test_term_years_without_a_start_uses_the_planner_start(self):
        user = make_user()
        make_planner(user)
        policy = make_policy(user, start_month=None)
        policy.term_years = 10
        policy.apply_term_years(JAN_2026)

        self.assertEqual(policy.end_month, dt.date(2035, 12, 1))

    def test_insurance_enters_the_ef_target_as_a_monthly_equivalent(self):
        user = make_user()
        make_planner(user, ef_target_months=6)
        make_salary(user)
        make_expense(user, amount="40000")
        make_policy(user, premium_amount=D("12000"), frequency=InsurancePolicy.YEARLY)
        rows = build_projection(user)

        # 6 * (40000 living + 1000 insurance/month) -- the target must not
        # jump in the month the annual premium actually lands.
        self.assertEqual(rows[0].ef_target, D("246000.00"))
        self.assertEqual(rows[6].ef_target, D("246000.00"))


class ExpenseTests(TestCase):
    def test_inflation_steps_every_twelve_months(self):
        user = make_user()
        make_planner(user, project_to_year=2028, expense_inflation_pct=D("10"))
        make_salary(user)
        make_expense(user, name="Groceries", amount="1000", inflates=True)
        make_expense(user, name="Rent", amount="2000", inflates=False)
        rows = build_projection(user)

        self.assertEqual(rows[0].living_expenses, D("3000.00"))
        self.assertEqual(rows[11].living_expenses, D("3000.00"))
        self.assertEqual(rows[12].living_expenses, D("3100.00"))
        self.assertEqual(rows[23].living_expenses, D("3100.00"))
        self.assertEqual(rows[24].living_expenses, D("3210.00"))

    def test_yearly_expense_charges_once_a_year_in_its_due_month(self):
        user = make_user()
        make_planner(user, project_to_year=2026, expense_inflation_pct=D("0"))
        make_salary(user)
        make_expense(user, name="Property tax", amount="12000", inflates=False,
                     frequency="yearly", due_month=3)
        rows = build_projection(user)

        self.assertEqual(rows[0].living_expenses, D("0.00"))  # January
        self.assertEqual(rows[2].living_expenses, D("12000.00"))  # March
        self.assertEqual(rows[3].living_expenses, D("0.00"))  # April
        self.assertEqual([item.name for item in rows[2].lumpy_items], ["Property tax"])
        self.assertEqual(rows[2].lumpy_items[0].amount, D("12000.00"))
        self.assertFalse(rows[0].is_lumpy)
        self.assertTrue(rows[2].is_lumpy)

    def test_yearly_expense_enters_the_ef_target_smoothed_not_lumpy(self):
        user = make_user()
        make_planner(user, project_to_year=2026, ef_target_months=6)
        make_salary(user)
        make_expense(user, name="Property tax", amount="12000", inflates=False,
                     frequency="yearly", due_month=3)
        rows = build_projection(user)

        # 6 * 1000/month equivalent -- the target must not jump in March,
        # the one month the full 12000 actually lands.
        self.assertEqual(rows[0].ef_target, D("6000.00"))
        self.assertEqual(rows[2].ef_target, D("6000.00"))


class OneTimeExpenseTests(TestCase):
    """A single purchase, added with its own month -- no frequency at all."""

    def test_charges_exactly_once_in_its_own_month(self):
        user = make_user()
        make_planner(user, project_to_year=2028)
        make_salary(user)
        make_one_time_expense(user, name="Phone", amount="50000", month=dt.date(2026, 6, 1))
        rows = build_projection(user)

        self.assertEqual(rows[4].one_time_expense, D("0.00"))    # May 2026
        self.assertEqual(rows[5].one_time_expense, D("50000.00"))  # June 2026
        self.assertEqual(rows[6].one_time_expense, D("0.00"))    # July 2026
        # Never recurs -- not the following June, or any year after that.
        self.assertEqual(rows[17].one_time_expense, D("0.00"))   # June 2027
        self.assertEqual(rows[29].one_time_expense, D("0.00"))   # June 2028
        self.assertEqual(sum(r.one_time_expense for r in rows), D("50000.00"))

    def test_counts_towards_outflow_and_net_surplus(self):
        user = make_user()
        make_planner(user, project_to_year=2026)
        make_salary(user, amount="100000")
        make_one_time_expense(user, amount="50000", month=JAN_2026)
        rows = build_projection(user)

        self.assertEqual(rows[0].total_outflow, D("50000.00"))
        self.assertEqual(rows[0].net_surplus, D("50000.00"))
        self.assertEqual(rows[1].total_outflow, D("0.00"))

    def test_never_enters_the_emergency_fund_target(self):
        user = make_user()
        make_planner(user, project_to_year=2026, ef_target_months=6)
        make_salary(user)
        make_one_time_expense(user, amount="50000", month=JAN_2026)
        rows = build_projection(user)

        # No other expenses, so the target is zero throughout -- even in the
        # one month the phone is actually charged.
        self.assertEqual(rows[0].ef_target, D("0.00"))

    def test_is_flagged_lumpy_and_never_smoothed(self):
        user = make_user()
        make_planner(user, project_to_year=2026)
        make_salary(user)
        make_one_time_expense(user, name="Phone", amount="50000", month=dt.date(2026, 6, 1))
        rows = build_projection(user)

        self.assertFalse(rows[4].is_lumpy)
        self.assertTrue(rows[5].is_lumpy)
        self.assertEqual([item.name for item in rows[5].lumpy_items], ["Phone"])
        self.assertEqual(rows[5].lumpy_items[0].amount, D("50000.00"))

    def test_multiple_one_time_expenses_in_the_same_month_add_up(self):
        user = make_user()
        make_planner(user, project_to_year=2026)
        make_salary(user)
        make_one_time_expense(user, name="Phone", amount="50000", month=JAN_2026)
        make_one_time_expense(user, name="Laptop", amount="90000", month=JAN_2026)
        rows = build_projection(user)

        self.assertEqual(rows[0].one_time_expense, D("140000.00"))
        self.assertEqual({item.name for item in rows[0].lumpy_items}, {"Phone", "Laptop"})


class BankAccumulationTests(TestCase):
    """No cap, no pool: net surplus just piles up in the bank, forever."""

    def test_surplus_keeps_accumulating_past_the_target(self):
        user = make_user()
        make_planner(user, bank_balance_today=D("1000000"), ef_target_months=6)
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        rows = build_projection(user)

        self.assertEqual(rows[0].ef_target, D("240000.00"))
        # Already well past the target, and nothing sweeps it back down.
        self.assertEqual(rows[0].bank_balance, D("1060000.00"))
        self.assertEqual(rows[0].investment_balance, D("0.00"))
        self.assertTrue(rows[0].ef_goal_met)

    def test_ef_goal_met_flips_true_once_the_balance_crosses_the_target_and_keeps_growing(self):
        user = make_user()
        make_planner(user, bank_balance_today=D("0"), ef_target_months=6)
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        rows = build_projection(user)

        # Target 240000, surplus 60000/month.
        self.assertEqual(rows[0].bank_balance, D("60000.00"))
        self.assertEqual(rows[2].bank_balance, D("180000.00"))
        self.assertFalse(rows[2].ef_goal_met)
        self.assertEqual(rows[3].bank_balance, D("240000.00"))
        self.assertTrue(rows[3].ef_goal_met)
        # No cap: month 5 keeps growing past the target rather than holding there.
        self.assertEqual(rows[4].bank_balance, D("300000.00"))
        self.assertTrue(rows[4].ef_goal_met)

    def test_deficit_makes_the_bank_go_negative_with_no_backstop(self):
        user = make_user()
        make_planner(user, bank_balance_today=D("0"), ef_target_months=0)
        make_salary(user, amount="20000")
        make_expense(user, amount="100000")
        make_holding(user, current_value=D("500000"), monthly_sip=D("0"),
                     expected_return_pct=D("0"))
        rows = build_projection(user)

        self.assertEqual(rows[0].bank_balance, D("-80000.00"))
        self.assertTrue(rows[0].cash_shortfall)
        self.assertEqual(rows[1].bank_balance, D("-160000.00"))
        self.assertTrue(rows[1].cash_shortfall)

        # Fund units are never sold, however deep the hole gets.
        for row in rows:
            self.assertEqual(row.investment_balance, D("500000.00"))

    def test_bank_interest_is_credited_monthly(self):
        user = make_user()
        # The EF target is irrelevant to the bank balance now -- it is purely
        # informational, so set a big one to document that it changes nothing.
        make_planner(user, bank_balance_today=D("120000"), bank_interest_pct=D("12"),
                     ef_target_fixed_amount=D("500000"))
        make_salary(user, amount="0")
        make_expense(user, amount="0")
        rows = build_projection(user)

        self.assertEqual(rows[0].bank_interest, D("1200.00"))
        self.assertEqual(rows[0].bank_balance, D("121200.00"))
        self.assertFalse(rows[0].ef_goal_met)


class HorizonTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.planner = make_planner(self.user)
        make_salary(self.user)

    def test_year_before_the_start_is_clamped_not_rejected(self):
        horizon = resolve_horizon(self.planner, 2020)
        self.assertEqual(horizon.end_year, 2026)
        self.assertTrue(horizon.notices)

        rows = build_projection(self.user, upto_year=2020)
        self.assertEqual(len(rows), 12)

    def test_more_than_thirty_years_is_capped(self):
        horizon = resolve_horizon(self.planner, 2100)
        self.assertEqual(horizon.end_year, 2055)
        self.assertIn("30 years", horizon.notices[0])

        rows = build_projection(self.user, upto_year=2100)
        self.assertEqual(len(rows), 30 * 12)
        self.assertEqual(rows[-1].month, dt.date(2055, 12, 1))

    def test_upto_year_overrides_the_saved_setting(self):
        self.assertEqual(len(build_projection(self.user)), 12)
        self.assertEqual(len(build_projection(self.user, upto_year=2030)), 60)

    def test_a_mid_year_start_still_runs_to_december(self):
        self.planner.start_month = dt.date(2026, 9, 1)
        self.planner.save()
        rows = build_projection(self.user, upto_year=2027)

        self.assertEqual(rows[0].month, dt.date(2026, 9, 1))
        self.assertEqual(len(rows), 16)


class EmptyPlannerTests(TestCase):
    def test_settings_only_produces_zero_rows_without_crashing(self):
        user = make_user()
        make_planner(user)
        rows = build_projection(user)

        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0].total_inflow, D("0.00"))
        self.assertEqual(rows[0].net_worth, D("0.00"))
        self.assertEqual(rows[0].ef_target, D("0.00"))

    def test_no_planner_settings_returns_nothing(self):
        user = make_user()
        self.assertEqual(build_projection(user), [])


class CapitalVersusGrowthTests(TestCase):
    """Every growing balance splits into money put in and money earned."""

    def test_investment_capital_is_the_opening_value_plus_sips(self):
        user = make_user()
        make_planner(user, ef_target_fixed_amount=D("10000000"))  # nothing sweeps
        make_salary(user, amount="100000")
        make_holding(user, current_value=D("100000"), monthly_sip=D("10000"),
                     expected_return_pct=D("12"))
        rows = build_projection(user)

        # Month 1: 100000*1.01 + 10000 = 111000, of which 110000 is capital.
        self.assertEqual(rows[0].investment_balance, D("111000.00"))
        self.assertEqual(rows[0].invested_capital, D("110000.00"))
        self.assertEqual(rows[0].investment_gains, D("1000.00"))

        # Capital is just the running total of what went in.
        self.assertEqual(rows[11].invested_capital, D("220000.00"))
        self.assertEqual(
            rows[11].investment_gains,
            rows[11].investment_balance - rows[11].invested_capital,
        )

    def test_gains_go_negative_when_a_holding_loses_money(self):
        user = make_user()
        make_planner(user, ef_target_fixed_amount=D("10000000"))
        make_holding(user, current_value=D("100000"), monthly_sip=D("0"),
                     expected_return_pct=D("-12"))
        rows = build_projection(user)

        self.assertEqual(rows[0].invested_capital, D("100000.00"))
        self.assertEqual(rows[0].investment_gains, D("-1000.00"))

    def test_pf_capital_is_the_opening_corpus_plus_contributions(self):
        user = make_user()
        make_planner(user)
        make_salary(user, amount="100000")
        make_pf(user, current_balance=D("500000"), employee_monthly=D("10000"),
                employer_monthly=D("10000"), annual_return_pct=D("12"))
        rows = build_projection(user)

        self.assertEqual(rows[0].pf_capital, D("520000.00"))
        self.assertEqual(rows[0].pf_balance, D("525000.00"))
        self.assertEqual(rows[0].pf_gains, D("5000.00"))

    def test_totals_add_up_across_both_balances(self):
        user = make_user()
        make_planner(user, ef_target_months=0)
        make_salary(user, amount="100000")
        make_expense(user, amount="40000")
        make_holding(user, current_value=D("100000"), monthly_sip=D("10000"))
        make_pf(user, current_balance=D("200000"))
        row = build_projection(user)[11]

        self.assertEqual(row.total_capital, row.invested_capital + row.pf_capital)
        self.assertEqual(row.total_gains, row.investment_gains + row.pf_gains)
        # Balance is always capital plus growth, by construction.
        self.assertEqual(row.investment_balance, row.invested_capital + row.investment_gains)
        self.assertEqual(row.pf_balance, row.pf_capital + row.pf_gains)

    def test_annual_rollup_carries_the_split(self):
        user = make_user()
        make_planner(user, project_to_year=2027, ef_target_months=0)
        make_salary(user, amount="100000")
        make_holding(user, current_value=D("100000"), monthly_sip=D("10000"))
        rows = build_projection(user)
        years = annual_rollup(rows)

        self.assertEqual(years[0].invested_capital, rows[11].invested_capital)
        self.assertEqual(years[1].investment_gains, rows[23].investment_gains)


class RollupTests(TestCase):
    def setUp(self):
        self.user = make_user()
        make_planner(self.user, project_to_year=2028)
        make_salary(self.user, amount="100000")
        make_expense(self.user, amount="40000")
        make_loan(self.user, principal_outstanding_today=D("100000"), tenure_months=12)
        make_holding(self.user)
        self.rows = build_projection(self.user)

    def test_annual_rollup_totals_the_months_and_takes_year_end_balances(self):
        years = annual_rollup(self.rows)

        self.assertEqual([y.year for y in years], [2026, 2027, 2028])
        self.assertEqual(years[0].months, 12)
        self.assertEqual(years[0].inflow, D("1200000.00"))
        self.assertEqual(years[0].net_worth, self.rows[11].net_worth)
        self.assertEqual(years[0].bank_balance, self.rows[11].bank_balance)

    def test_summary_headlines(self):
        summary = summarise(self.rows)

        self.assertEqual(summary.opening_loan_balance, D("91000.00"))
        # The loan clears in month 11, so debt-free from month 12.
        self.assertEqual(summary.debt_free_month, dt.date(2026, 12, 1))
        self.assertEqual(summary.lowest_surplus, min(r.net_surplus for r in self.rows))
        self.assertEqual(summary.shortfall_months, 0)
        self.assertIs(summary.last, self.rows[-1])

    def test_summary_of_an_empty_projection(self):
        summary = summarise([])
        self.assertIsNone(summary.first)
        self.assertIsNone(summary.debt_free_month)
