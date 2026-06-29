from django.urls import path
from . import views

urlpatterns = [
    path("", views.me, name="profile-me"),
    path("update/", views.update_profile, name="profile-update"),
    path("change-password/", views.change_password, name="profile-change-password"),
    path("settings/", views.profile_settings, name="profile-settings"),
]
