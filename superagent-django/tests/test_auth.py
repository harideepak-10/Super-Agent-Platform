"""Tests for authentication endpoints."""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.authentication.models import Workspace
from apps.team.models import TeamMembership

User = get_user_model()
pytestmark = pytest.mark.django_db


class TestRegister:
    def test_register_success(self, api_client):
        res = api_client.post("/api/v1/auth/register/", {
            "email": "new@krypsos.tech",
            "password": "Test@1234",
            "name": "New User",
        })
        assert res.status_code == 201
        assert "tokens" in res.data
        assert "user" in res.data

    def test_register_creates_workspace(self, api_client):
        api_client.post("/api/v1/auth/register/", {
            "email": "ws@krypsos.tech",
            "password": "Test@1234",
            "name": "WS User",
        })
        user = User.objects.get(email="ws@krypsos.tech")
        assert user.memberships.exists()

    def test_register_duplicate_email_fails(self, api_client):
        data = {"email": "dup@krypsos.tech", "password": "Test@1234", "name": "Dup"}
        api_client.post("/api/v1/auth/register/", data)
        res = api_client.post("/api/v1/auth/register/", data)
        assert res.status_code == 400

    def test_register_missing_email_fails(self, api_client):
        res = api_client.post("/api/v1/auth/register/", {"password": "Test@1234"})
        assert res.status_code == 400

    def test_register_returns_access_and_refresh(self, api_client):
        res = api_client.post("/api/v1/auth/register/", {
            "email": "tokens@krypsos.tech",
            "password": "Test@1234",
            "name": "Token User",
        })
        assert "access" in res.data["tokens"]
        assert "refresh" in res.data["tokens"]


class TestLogin:
    def test_login_success(self, api_client):
        User.objects.create_user(email="login@krypsos.tech", password="Test@1234", name="Login")
        res = api_client.post("/api/v1/auth/login/", {
            "email": "login@krypsos.tech",
            "password": "Test@1234",
        })
        assert res.status_code == 200
        assert "tokens" in res.data

    def test_login_wrong_password(self, api_client):
        User.objects.create_user(email="wrong@krypsos.tech", password="Test@1234", name="Wrong")
        res = api_client.post("/api/v1/auth/login/", {
            "email": "wrong@krypsos.tech",
            "password": "WrongPass",
        })
        assert res.status_code == 401

    def test_login_nonexistent_user(self, api_client):
        res = api_client.post("/api/v1/auth/login/", {
            "email": "nobody@krypsos.tech",
            "password": "Test@1234",
        })
        assert res.status_code == 401


class TestMe:
    def test_me_returns_user(self, create_user_with_workspace):
        user, _, client = create_user_with_workspace()
        res = client.get("/api/v1/auth/me/")
        assert res.status_code == 200
        assert res.data["email"] == user.email

    def test_me_requires_auth(self, api_client):
        res = api_client.get("/api/v1/auth/me/")
        assert res.status_code == 401


class TestLogout:
    def test_logout_success(self, create_user_with_workspace):
        user, _, client = create_user_with_workspace()
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = str(RefreshToken.for_user(user))
        res = client.post("/api/v1/auth/logout/", {"refresh": refresh})
        assert res.status_code == 200

    def test_logout_requires_auth(self, api_client):
        res = api_client.post("/api/v1/auth/logout/", {"refresh": "invalid"})
        assert res.status_code == 401


class TestTokenRefresh:
    def test_refresh_returns_new_access(self, create_user_with_workspace):
        user, _, _ = create_user_with_workspace()
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = str(RefreshToken.for_user(user))
        client = APIClient()
        res = client.post("/api/v1/auth/token/refresh/", {"refresh": refresh})
        assert res.status_code == 200
        assert "access" in res.data

    def test_refresh_invalid_token(self, api_client):
        res = api_client.post("/api/v1/auth/token/refresh/", {"refresh": "badtoken"})
        assert res.status_code == 401
