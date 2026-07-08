from django.urls import path
from . import views

urlpatterns = [
    path("", views.integration_list, name="integration-list"),
    path("available/", views.available_integrations, name="integration-available"),
    path("connect/", views.integration_connect, name="integration-connect"),
    path("gmail/emails/", views.gmail_emails, name="integration-gmail-emails"),
    path("gmail/auth-url/", views.gmail_auth_url, name="integration-gmail-auth-url"),
    path("gmail/callback/", views.gmail_callback, name="integration-gmail-callback"),
    path("drive/auth-url/", views.drive_auth_url, name="integration-drive-auth-url"),
    path("drive/callback/", views.drive_callback, name="integration-drive-callback"),
    path("<uuid:pk>/", views.integration_detail, name="integration-detail"),
    path("<uuid:pk>/disconnect/", views.integration_disconnect, name="integration-disconnect"),
    path("<uuid:pk>/refresh/", views.integration_refresh, name="integration-refresh"),
    path("<uuid:pk>/status/", views.integration_status, name="integration-status"),
]
