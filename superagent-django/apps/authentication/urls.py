from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health_check, name="auth-health"),
    path("register/", views.register, name="auth-register"),
    path("login/", views.login, name="auth-login"),
    path("google/", views.google_login, name="auth-google"),
    path("logout/", views.logout, name="auth-logout"),
    path("token/refresh/", views.token_refresh, name="auth-token-refresh"),
    path("forgot-password/", views.forgot_password, name="auth-forgot-password"),
    path("reset-password/", views.reset_password, name="auth-reset-password"),
    path("test-email/", views.test_email, name="auth-test-email"),
    path("emergency-reset/", views.emergency_reset, name="auth-emergency-reset"),
]
