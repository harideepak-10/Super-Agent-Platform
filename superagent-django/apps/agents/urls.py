from django.urls import path
from . import views

urlpatterns = [
    path("", views.agent_list, name="agent-list"),
    path("create/", views.agent_create, name="agent-create"),
    path("<uuid:pk>/", views.agent_detail, name="agent-detail"),
    path("<uuid:pk>/update/", views.agent_update, name="agent-update"),
    path("<uuid:pk>/delete/", views.agent_delete, name="agent-delete"),
    path("<uuid:pk>/tasks/", views.agent_tasks, name="agent-tasks"),
    path("mobile/", views.agent_mobile_list, name="agent-mobile-list"),
]
