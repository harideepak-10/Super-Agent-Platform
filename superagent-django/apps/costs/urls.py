from django.urls import path
from . import views

urlpatterns = [
    path("summary/",      views.cost_summary,    name="cost-summary"),
    path("daily/",        views.cost_daily,       name="cost-daily"),
    path("by-agent/",     views.cost_by_agent,    name="cost-by-agent"),
    path("by-creator/",   views.cost_by_creator,  name="cost-by-creator"),
    path("range/",        views.cost_range,        name="cost-range"),
    path("budget/",       views.budget_detail,    name="cost-budget"),
    path("budget/update/", views.budget_update,   name="cost-budget-update"),
]
