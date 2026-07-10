from django.urls import path
from . import views

urlpatterns = [
    path("", views.notification_list, name="notification-list"),
    path("count/", views.notification_count, name="notification-count"),
    path("mark-all-read/", views.mark_all_read, name="notification-mark-all-read"),
    path("settings/", views.notification_settings, name="notification-settings"),
    path("<uuid:pk>/", views.notification_detail, name="notification-detail"),
]
