"""
Shared fixtures for all Phase 2 Django tests.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.authentication.models import Workspace
from apps.team.models import TeamMembership

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def create_user_with_workspace():
    """Factory: creates a user + workspace + membership. Returns (user, workspace, client)."""
    def _make(email="test@krypsos.tech", password="Test@1234", name="Test User"):
        user = User.objects.create_user(email=email, password=password, name=name)
        workspace = Workspace.objects.create(
            name=f"{name}'s Workspace",
            slug=email.split("@")[0].replace(".", "-"),
            owner=user,
        )
        TeamMembership.objects.create(
            workspace=workspace,
            user=user,
            role=TeamMembership.Role.OWNER,
        )
        client = APIClient()
        # Get JWT token
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
        return user, workspace, client
    return _make
