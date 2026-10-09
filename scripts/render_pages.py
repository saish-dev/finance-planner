"""Render every page of the seeded sample plan to HTML (used by build_demo.py).

Runs against a throwaway in-memory database, never the real db.sqlite3.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.test.utils import setup_test_environment
from django.test.runner import DiscoverRunner
from django.test import Client
from django.core.management import call_command

setup_test_environment()
runner = DiscoverRunner(verbosity=0, interactive=False)
old = runner.setup_databases()
try:
    call_command("seed_sample", "--reset", verbosity=0)
    from planner.models import Loan, PlannerSettings
    from planner.middleware import get_owner
    c = Client()
    out = {}

    def get(path):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code)
        return r.content.decode()

    for p in ["", "settings/", "income/", "expenses/", "loans/", "investments/",
              "retirement/", "insurance/", "cashflow/", "summary/", "summary/?detail=1"]:
        out["/" + p] = get("/" + p)
    import re
    n = 2
    while re.search(r'href="[^"]*page=%d"' % n, out["/cashflow/"] if n == 2 else out[f"/cashflow/?page={n-1}"]):
        out[f"/cashflow/?page={n}"] = get(f"/cashflow/?page={n}")
        n += 1
    for loan in Loan.objects.all():
        out[f"/loans/{loan.pk}/"] = get(f"/loans/{loan.pk}/")
    ps = PlannerSettings.objects.get(user=get_owner())
    years = {}
    for y in range(ps.start_month.year, ps.max_year + 1):
        years[str(y)] = get(f"/dashboard/body/?upto={y}")
    out["__years__"] = years
    out["__current_year__"] = ps.project_to_year
    json.dump(out, open(sys.argv[1], "w"))
finally:
    runner.teardown_databases(old)
