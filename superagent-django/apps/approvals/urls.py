from django.urls import path
from . import views

urlpatterns = [
    path("", views.approval_list, name="approval-list"),
    path("pending/", views.pending_approvals, name="approval-pending"),
    path("inbox/", views.approval_inbox, name="approval-inbox"),
    path("history/", views.approval_history, name="approval-history"),
    path("rules/", views.approval_rules, name="approval-rules"),
    path("rules/<uuid:pk>/", views.approval_rule_detail, name="approval-rule-detail"),
    path("<uuid:pk>/", views.approval_detail, name="approval-detail"),
    path("<uuid:pk>/review/", views.approval_review_detail, name="approval-review-detail"),
    path("<uuid:pk>/confirm/", views.approval_confirm, name="approval-confirm"),
    path("<uuid:pk>/decide/", views.approval_decide, name="approval-decide"),
]
