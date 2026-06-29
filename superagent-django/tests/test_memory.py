"""Tests for memory (customer profiles) endpoints."""
import pytest
from apps.memory.models import CustomerProfile

pytestmark = pytest.mark.django_db


def make_profile(workspace, user, email="customer@example.com", name="Customer One"):
    return CustomerProfile.objects.create(
        workspace=workspace, created_by=user,
        email=email, name=name,
    )


class TestProfileList:
    def test_list_profiles(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_profile(ws, user)
        res = client.get("/api/v1/memory/")
        assert res.status_code == 200
        assert len(res.data) == 1

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/memory/")
        assert res.status_code == 401

    def test_search_by_email(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_profile(ws, user, email="searchme@example.com")
        res = client.get("/api/v1/memory/?q=searchme")
        assert len(res.data) == 1

    def test_workspace_isolation(self, create_user_with_workspace):
        user1, ws1, client1 = create_user_with_workspace("mp1@k.tech")
        user2, ws2, client2 = create_user_with_workspace("mp2@k.tech")
        make_profile(ws2, user2)
        res = client1.get("/api/v1/memory/")
        assert res.data == []


class TestProfileCreate:
    def test_create_profile(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/memory/create/", {
            "email": "new@example.com",
            "name": "New Customer",
        })
        assert res.status_code == 201
        assert res.data["email"] == "new@example.com"

    def test_create_requires_auth(self, api_client):
        res = api_client.post("/api/v1/memory/create/", {"email": "x@x.com"})
        assert res.status_code == 401


class TestProfileDetail:
    def test_get_profile(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        profile = make_profile(ws, user)
        res = client.get(f"/api/v1/memory/{profile.id}/")
        assert res.status_code == 200
        assert res.data["id"] == str(profile.id)

    def test_patch_profile(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        profile = make_profile(ws, user)
        res = client.patch(f"/api/v1/memory/{profile.id}/", {"name": "Updated"})
        assert res.status_code == 200
        assert res.data["name"] == "Updated"

    def test_delete_profile(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        profile = make_profile(ws, user)
        res = client.delete(f"/api/v1/memory/{profile.id}/")
        assert res.status_code == 204
        assert not CustomerProfile.objects.filter(id=profile.id).exists()


class TestProfileByEmail:
    def test_lookup_found(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        make_profile(ws, user, email="find@example.com")
        res = client.get("/api/v1/memory/lookup/?email=find@example.com")
        assert res.status_code == 200
        assert res.data["found"] is True

    def test_lookup_not_found(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/memory/lookup/?email=nope@example.com")
        assert res.status_code == 200
        assert res.data["found"] is False

    def test_lookup_no_param(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.get("/api/v1/memory/lookup/")
        assert res.status_code == 400


class TestProfileInteractions:
    def test_interactions_empty(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        profile = make_profile(ws, user)
        res = client.get(f"/api/v1/memory/{profile.id}/interactions/")
        assert res.status_code == 200
        assert res.data == []
