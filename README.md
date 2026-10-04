# Personal Cashflow & Net Worth Planner

A Django app that replaces a multi-tab Excel cashflow model. Enter your income,
expenses, loans, investments and insurance once; the app projects a
month-by-month cashflow out to a year you choose, tracking four balances over
time — bank (which doubles as the emergency fund), your investments, the PF
corpus, and each loan's outstanding principal — and rolls them into

    Net worth = Bank + Investments + PF corpus − Loans outstanding

The **Project to year** box on the dashboard re-renders everything for a new
horizon without a page reload.

## Running it

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_sample      # optional: a realistic sample plan
.venv/bin/python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

`seed_sample --reset` replaces existing data. Everything it creates is editable
in the UI — it exists so the charts have something to draw on day one.

Tests:

```bash
.venv/bin/python manage.py test planner
```

### Database

SQLite by default. Set `POSTGRES_DB` (plus `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_HOST`, `POSTGRES_PORT`) to switch to PostgreSQL.

### There is no login

This is a single-user app. `planner.middleware.AutoUserMiddleware` pins every
request to one account (`PLANNER_OWNER_USERNAME`, default `owner`), so there is
no login page — **do not expose it on a network**. Every model still carries a
`user` foreign key, so becoming multi-user means adding a login and deleting the
middleware; no schema change.

## What is in the app

| Area | What it does |
|---|---|
| **Dashboard** | Net worth, emergency fund, loans, asset mix, insights ("worth a look"), milestones and financial-independence progress, spending and growth charts, and a month-by-month surplus calendar. |
| **Cashflow / Summary** | The projection month by month and year by year. |
| **Money / Assets** | Income, expenses, loans, investments, retirement and insurance, edited inline. Every page has headline tiles, sortable and filterable tables, **Copy** on each row, and **Undo** after a delete. |
| **Plan → Goals** | "I need ₹X by month M", checked against bank + funds after the goals due before it, with the extra monthly SIP that would close any gap. |
| **Plan → What-if** | Change inflation, hikes, returns, interest and SIP step-ups, or add a stretch with no salary or a market fall, and compare with the baseline. Nothing is saved; the scenario lives in the URL. |
| **Plan → Actuals** | Log real month-end balances and see the drift from the plan. |
| **Loan page** | Interest versus principal by year, and a prepayment simulator (interest and time saved). |
| **Settings** | Assumptions, plus **Backup and restore** of everything as one JSON file. |

The top bar has a privacy switch (blurs every figure) and a light/dark switch.
The dashboard prints cleanly (Print or save as PDF).

## Where the logic lives

```
planner/services/projection.py   the engine: one MonthRow per month
planner/services/amortization.py NPER, PV, one month of amortisation
planner/services/dates.py        all month arithmetic (the off-by-one-prone part)
planner/services/money.py        Decimal helpers; no float ever touches money
planner/whatif.py                scenario parsing and baseline-vs-scenario comparison
planner/goals.py                 goals measured against the projection
planner/actuals.py               logged balances against the plan
planner/insights.py              insights, milestones, FI progress, surplus heatmap
planner/prepay.py                loan prepayment what-if
planner/metrics.py               headline tiles above each data page
planner/backup.py                JSON export / restore
```

`build_projection(user, upto_year, scenario)` accepts an optional in-memory
`Scenario` of overrides (used by What-if); with none it is the baseline, and
nothing a scenario does is ever saved.

`build_projection(user, upto_year)` is a pure function of the database: nothing
is precomputed, cached or stored. That is why deleting a loan or a fund can
never corrupt anything — the next page load simply recomputes without it.

The chain, in order, per month:

1. **Salary** — the most recent `SalaryChange` on or before this month;
   otherwise last month's, grown by the default hike in the designated hike
   month. The hike never fires in month 0.
2. **Bonus** — each `IncomeExtra` pays out in `payout_month`, or split evenly
   across every month from there through `payout_end_month` if set. That
   pattern repeats every year the bonus is active, bounded by its own real
   `start_month`/`end_month` (blank start = already running, blank end =
   forever) — separate from the month-of-year pattern, because a bonus is not
   guaranteed to be the same every year indefinitely.
3. **Living expenses** — inflating ones grow in 12-month steps from the
   projection start; flat ones never move. An `Expense` may be monthly,
   quarterly, half-yearly or yearly; a non-monthly one is charged in full in
   the real month it falls due (`(month − due_month) % frequency_months == 0`),
   never smoothed — it enters the emergency-fund target at a smoothed
   monthly equivalent instead, so the target does not lurch.
4. **Insurance** — the real premium in the real month it falls due
   (`(month − premium_month) % frequency_months == 0`), never smoothed.
5. **Loans** — flat EMI while `start ≤ month ≤ last EMI month`, balance
   amortised at `rate/12` monthly, per loan, any number of them.
6. **SIPs** — each stepped up from **that fund's own** `sip_start_month`, not
   from the global projection start.
6b. **Retirement (EPF)** — sits *outside* the cashflow chain entirely. See below.
7. **One-time expenses** — a `OneTimeExpense` is a single purchase (a phone, a
   trip): just a name, an amount and a month, added with `month == the row's
   month`. No frequency, no inflation adjustment, and it never enters the EF
   target — a one-off purchase is not an ongoing essential cost. Kept as its
   own table and its own cashflow column, deliberately separate from the
   recurring `Expense` model above.
8. **Net surplus** = inflow − (living + insurance + EMIs + SIPs + one-time).
9. **EF target** = fixed amount, or `N × (living + insurance monthly-equivalent
   + EMIs)`. Insurance enters here at its monthly equivalent, and only here, so
   an annual premium does not make the target lurch. Purely a milestone: it
   never caps or redirects anything below.
10. **Bank** — grows at `interest/12`, then takes the whole net surplus,
    uncapped. There is no sweep and no separate surplus pool: every rupee of
    surplus just accumulates here, forever, even past the EF target.
11. **Investments** — your funds. They receive SIPs and growth, and *nothing
    else*: never topped up from the bank, never sold to cover a shortfall.
12. **Net worth** = bank + investments + PF corpus − total loan principal.

### Money put in vs money earned

Investments and the PF corpus each carry a `*_capital` and a `*_gains` figure,
so `balance == capital + gains` always holds. Capital is what went in (opening
value, then SIPs / contributions); gains are the residual.

**Capital is measured from the projection start.** The app has no record of
what you originally paid for a holding, so the value you already had on day one
counts as capital. These are therefore gains *over the projection*, not
lifetime returns — a fund you bought at ₹1L and that is worth ₹5L today starts
here as ₹5L of capital, not ₹1L of capital and ₹4L of gains.

Investment gains *can* go negative, and are shown in red when they do, since a
holding with a negative expected return should look like a loss rather than be
clamped at zero.

### Three balances, three jobs

| Balance | Receives | Compounds at | Can be spent? |
|---|---|---|---|
| Bank | surplus, interest | bank rate | yes — the only one that ever is |
| Investments | SIPs only | blended fund return | **never sold** |
| PF corpus | contributions | its own rate | **never sold** |

There is no cap and no separate surplus pool: every rupee of net surplus lands
in the bank and stays there, growing at the bank rate, whether or not it has
already passed the emergency-fund target. The target is shown purely as a
milestone (`ef_goal_met`) — nothing is ever moved because of it.

The consequence is that a deficit shows up immediately as a **negative bank
balance**, flagged in red, rather than being papered over by silently selling
SIP units or drawing on some other buffer. That is the point: a month the plan
cannot fund should look like one.

### Why PF is not an investment holding

`RetirementAccount` is a separate model rather than an `InvestmentHolding` with
a flag, because PF breaks two assumptions the investment path is built on:

- **It must not touch the surplus.** `monthly_in_hand` is already net of the
  employee contribution, and the employer's share never reaches your bank at
  all. Running it through the SIP column — which is subtracted from the
  surplus — would deduct the employee half a second time and invent an outflow
  for the employer half that never happened. So the PF columns are recorded and
  displayed, but never enter `total_outflow`.
- **It must not be spendable.** On a deficit month the bank balance simply goes
  negative. EPF cannot be sold to pay rent, so the corpus is never offered up
  to cover it, and it never counts towards the emergency fund target. A
  shortfall still shows as a shortfall with ₹50 lakh sitting in PF — which is
  the honest answer.

Contributions **track your salary**: you enter today's rupee amount, and it
moves in the same proportion as your pay, whether that comes from a
`SalaryChange` row or the default annual hike. You never restate it after a
raise. The anchor is the salary in the account's first projected month; if no
salary is entered at all, the contribution stays flat rather than dividing by
zero. Each account compounds at its own rate — two accounts are never pooled
into one blended rate the way investments are.

**The monthly cap** stops that tracking where your real contribution stops. EPF
computed on the statutory wage ceiling is 12% of ₹15,000 from each side, so
`monthly_cap = 3600`: contributions rise with your pay until they reach it and
stay flat afterwards. Both halves are scaled by the same factor when the cap
bites, so the employee/employer split of the capped total is preserved. Leave it
blank if your employer contributes on full basic rather than the ceiling —
without a cap, a salary-tracked contribution grows for the whole horizon.

Out of scope for now: VPF, NPS, gratuity, and the EPS pension split (the
employer's 8.33% that goes to pension rather than the corpus — enter the
employer figure net of it, as the sample does).

### Return basis on a holding

Each holding stores three rates — **3Y trailing**, **5Y trailing** and **your
own estimate** — and a **Project using** selector that decides which one the
projection compounds at. The other two are kept for reference, so the number
you chose is visible next to the numbers you rejected.

Whichever you pick is applied **flat for the whole horizon**, with no taper.
That is deliberate: a rate you can read off the fund's page and check by hand
beats one the app quietly adjusts behind your back. The consequence is worth
saying out loud — a 22% five-year trailing figure will project 22% a year for
fifteen years, which is almost certainly too optimistic, because a trailing
return is history and often captured near a market peak. The Investments table
flags any holding running on a trailing basis for exactly that reason.

Selecting a basis whose field you left blank is a validation error rather than
a silent fallback, so the dropdown can never claim a rate the engine isn't
using. (The model method still falls back rather than raising, so a
half-entered row can't break the projection.)

### Decisions worth knowing about

- **The final EMI is exactly what clears the loan.** Every EMI is capped at what
  is actually owed (the spreadsheet charged a flat EMI in the payoff month and
  floored the balance at zero, which spends money that was never due), and the
  *last* EMI goes the other way too: it is whatever it takes to reach zero, as a
  lender's final instalment is. If the EMI and tenure you enter do not quite
  clear the balance you enter, the shortfall is paid in that last month -- the
  loans page notes the bigger payment -- and the balance is zero from then on,
  never left standing unpaid and interest-free.
- **The bank may go negative.** If a month's outflow exceeds the surplus and
  whatever is already in the bank, the balance goes negative and the row is
  flagged (red in the cashflow table, a banner on the dashboard) rather than
  being quietly floored at zero or covered from anywhere else. A month the
  plan cannot fund is exactly what you want to see.
- **The bank is never capped.** Once it passes the emergency-fund target it
  just keeps growing — the target is a milestone, not a ceiling.
- **Blended investment return is computed once** from today's values and held
  constant, matching the spreadsheet. Re-weighting it monthly would move the
  30-year figure by a few percent and make it uncheckable by hand. Noted in
  `blended_return_pct`.
- **Horizons are clamped, never rejected**: a year before the start clamps to the
  start year, a year beyond 30 years clamps to the cap, each with an explanation.

## Simple by default, detail on demand

The engine tracks a lot, so the screens show a readable subset and keep the
rest one click away. Nothing is removed — only hidden.

- **Cashflow** renders `Month, In, Out, Surplus` plus the balances: 11 columns
  that fit without a sideways scroll. Three checkboxes — Income, Spending,
  Balances — add or remove blocks of columns, and the choice lives in the query
  string so it survives pagination and a refresh. `v=1` marks a submitted form,
  so un-ticking everything means "none" rather than falling back to the default.
- **Per-loan EMI columns are deliberately not on the Cashflow table.** Each is a
  constant, so it cost width without carrying information. Per-loan detail is on
  `/loans/<id>/`, and the CSV export still has every per-loan column.
- **Summary** collapses living costs, insurance, EMIs and SIPs into one `Out`
  column, with `?detail=1` breaking them back out.
- **Investments** shows one `Return` cell — `13% (my own estimate)` — instead of
  separate 3Y, 5Y, rate and basis columns. All four are still on the edit form.
- **The dashboard is four cards.** What the other four said now sits in the
  footnote line of the card it belongs to.

### Info indicators

Column headers and card labels carry a small `i` explaining how the number is
arrived at — `{% info "..." %}` in a template, or `info=` on a `crud.Column`.
It is CSS-only, shown on hover *and* keyboard focus, and mirrored into
`aria-label`, so it is not mouse-only. The tooltip opens downward because these
sit inside a horizontally scrolling container that would clip one drawn upward.

> Django's `{# … #}` comment syntax is **single-line only**. A multi-line one is
> emitted verbatim into the HTML — and inside `<head>` the browser hoists it
> into the body as visible text. Use `{% comment %}` blocks instead; there is a
> test asserting no page leaks `{#` or `{%`.

## Pages

| Page | What it does |
|---|---|
| `/` | Headline cards, net-worth line chart, annual-surplus bars, the year control |
| `/settings/` | Start month, horizon, inflation, hike, bank, return, EF target |
| `/income/` | Salary change table (any order) + bonus streams |
| `/expenses/` | Living expenses (monthly/quarterly/half-yearly/yearly, with an inflates toggle) plus a separate one-time expenses table for single purchases |
| `/loans/` | Any number of loans; derived columns shown greyed next to your inputs |
| `/loans/<id>/` | Month-by-month amortisation + balance chart |
| `/investments/` | Holdings, each with its own SIP window, step-up and return |
| `/retirement/` | EPF accounts: balance, employee + employer contributions, rate |
| `/insurance/` | Policies; enter a term in years and the end month fills itself in |
| `/cashflow/` | The full month-by-month table, year filter, paginated, CSV export |
| `/summary/` | Year-by-year rollup plus the same two charts |

## Testing

111 tests. `test_projection.py` hand-calculates every column of a small scenario
(the numbers are in the docstring, so you can check them on paper), then checks
months 3, 12 and 24 against closed-form annuity formulas. After that it covers
the cases most likely to hide off-by-one-month bugs: a mid-projection salary
change, the default hike landing only in its month, an `end_month_override`, a
SIP starting mid-projection with a step-up anchored to its own start, quarterly
and half-yearly premiums, inflation stepping every 12 months, a bonus split
across a chosen month range (including wrapping across the calendar year), a
one-time expense in its own month, and a deficit taking the bank negative with
no backstop. The retirement tests pin down the two rules that are easy to get
wrong: contributions never reduce the surplus, and the corpus is never sold to
cover a shortfall. `test_views.py` covers every page, the CSV export, the HTMX
row round-trip, and each validation rule.

## Not built (from the brief's "nice to haves")

Scenario comparison (two saved settings snapshots overlaid on one chart) and the
emergency-fund notification. CSV export of the cashflow table *is* built.
