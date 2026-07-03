from django.urls import path
from . import views

urlpatterns = [
    path("", views.agent_list, name="agent-list"),
    path("create/", views.agent_create, name="agent-create"),
    path("create-form/", views.agent_create_form, name="agent-create-form"),
    path("templates/", views.agent_templates, name="agent-templates"),
    path("templates/<slug:slug>/activate/", views.agent_template_activate, name="agent-template-activate"),
    path("<uuid:pk>/", views.agent_detail, name="agent-detail"),
    path("<uuid:pk>/update/", views.agent_update, name="agent-update"),
    path("<uuid:pk>/delete/", views.agent_delete, name="agent-delete"),
    path("<uuid:pk>/tasks/", views.agent_tasks, name="agent-tasks"),
    path("<uuid:pk>/live/", views.agent_live, name="agent-live"),
    path("<uuid:pk>/audit-log/", views.agent_audit_log, name="agent-audit-log"),
]
