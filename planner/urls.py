from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/body/", views.dashboard_partial, name="dashboard_partial"),
    path("settings/", views.settings_view, name="settings"),

    path("income/", views.data_page, {"page": "income"}, name="income"),
    path("expenses/", views.data_page, {"page": "expenses"}, name="expenses"),
    path("loans/", views.data_page, {"page": "loans"}, name="loans"),
    path("metrics/<slug:page>/", views.page_metrics_partial, name="page_metrics"),
    path("loans/<int:pk>/", views.loan_detail, name="loan_detail"),
    path("loans/<int:pk>/prepay/", views.loan_prepay, name="loan_prepay"),
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
    path("rows/<slug:slug>/<int:pk>/duplicate/", views.row_duplicate, name="row_duplicate"),
    path("rows/undo/", views.row_undo, name="row_undo"),

    path("backup/export/", views.backup_export, name="backup_export"),
    path("backup/import/", views.backup_import, name="backup_import"),
]
