from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/body/", views.dashboard_partial, name="dashboard_partial"),
    path("settings/", views.settings_view, name="settings"),

    path("income/", views.data_page, {"page": "income"}, name="income"),
    path("expenses/", views.data_page, {"page": "expenses"}, name="expenses"),
    path("loans/", views.data_page, {"page": "loans"}, name="loans"),
    path("loans/<int:pk>/", views.loan_detail, name="loan_detail"),
    path("investments/", views.data_page, {"page": "investments"}, name="investments"),
    path("retirement/", views.data_page, {"page": "retirement"}, name="retirement"),
    path("insurance/", views.data_page, {"page": "insurance"}, name="insurance"),

    path("cashflow/", views.cashflow, name="cashflow"),
    path("cashflow/export.csv", views.cashflow_csv, name="cashflow_csv"),
    path("summary/", views.summary, name="summary"),

    # Inline HTMX row editing, shared by every data table.
    path("rows/<slug:slug>/table/", views.row_table, name="row_table"),
    path("rows/<slug:slug>/new/", views.row_new, name="row_new"),
    path("rows/<slug:slug>/save/", views.row_save, name="row_create"),
    path("rows/<slug:slug>/<int:pk>/edit/", views.row_edit, name="row_edit"),
    path("rows/<slug:slug>/<int:pk>/save/", views.row_save, name="row_update"),
    path("rows/<slug:slug>/<int:pk>/delete/", views.row_delete, name="row_delete"),
]
