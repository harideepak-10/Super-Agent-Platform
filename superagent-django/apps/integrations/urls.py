from django.urls import path
from . import views

urlpatterns = [
    path("", views.integration_list, name="integration-list"),
    path("available/", views.available_integrations, name="integration-available"),
    path("connect/", views.integration_connect, name="integration-connect"),
    path("gmail/emails/", views.gmail_emails, name="integration-gmail-emails"),
    path("<uuid:pk>/", views.integration_detail, name="integration-detail"),
    path("<uuid:pk>/disconnect/", views.integration_disconnect, name="integration-disconnect"),
    path("<uuid:pk>/refresh/", views.integration_refresh, name="integration-refresh"),
    path("<uuid:pk>/status/", views.integration_status, name="integration-status"),
]
