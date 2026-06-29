"""Tests for team endpoints."""
import pytest
from django.contrib.auth import get_user_model
from apps.team.models import TeamMembership, TeamInvitation

User = get_user_model()
pytestmark = pytest.mark.django_db


class TestMemberList:
    def test_list_shows_owner(self, create_user_with_workspace):
        user, _, client = create_user_with_workspace()
        res = client.get("/api/v1/team/members/")
        assert res.status_code == 200
        # TeamMemberSerializer has flat "email" field, not nested user dict
        emails = [m["email"] for m in res.data]
        assert user.email in emails

    def test_list_requires_auth(self, api_client):
        res = api_client.get("/api/v1/team/members/")
        assert res.status_code == 401


class TestInviteMember:
    def test_invite_sends_invitation(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/team/invite/", {
            "email": "newmember@krypsos.tech",
            "role": "member",
        })
        assert res.status_code == 201
        assert TeamInvitation.objects.filter(email="newmember@krypsos.tech").exists()

    def test_invite_requires_email(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/team/invite/", {"role": "member"})
        assert res.status_code == 400

    def test_invite_requires_auth(self, api_client):
        res = api_client.post("/api/v1/team/invite/", {
            "email": "x@k.tech", "role": "member"
        })
        assert res.status_code == 401


class TestAcceptInvite:
    def _invite(self, client, email):
        client.post("/api/v1/team/invite/", {"email": email, "role": "member"})
        return TeamInvitation.objects.get(email=email)

    def test_accept_invite_adds_membership(self, create_user_with_workspace):
        _, _, owner_client = create_user_with_workspace("owner@k.tech")
        member_user, _, member_client = create_user_with_workspace("member@k.tech")
        invitation = self._invite(owner_client, "member@k.tech")
        res = member_client.post("/api/v1/team/accept-invite/", {"token": invitation.token})
        assert res.status_code == 200

    def test_accept_bad_token(self, create_user_with_workspace):
        _, _, client = create_user_with_workspace()
        res = client.post("/api/v1/team/accept-invite/", {"token": "bad-token"})
        assert res.status_code == 404

    def test_accept_requires_auth(self, api_client):
        res = api_client.post("/api/v1/team/accept-invite/", {"token": "x"})
        assert res.status_code == 401


class TestUpdateMemberRole:
    def test_update_role(self, create_user_with_workspace):
        owner, ws, owner_client = create_user_with_workspace("ur_owner@k.tech")
        member, _, _ = create_user_with_workspace("ur_member@k.tech")
        membership = TeamMembership.objects.create(
            workspace=ws, user=member, role=TeamMembership.Role.MEMBER
        )
        res = owner_client.patch(f"/api/v1/team/members/{membership.id}/role/", {"role": "admin"})
        assert res.status_code == 200
        membership.refresh_from_db()
        assert membership.role == TeamMembership.Role.ADMIN


class TestRemoveMember:
    def test_remove_member(self, create_user_with_workspace):
        owner, ws, owner_client = create_user_with_workspace("rm_owner@k.tech")
        member, _, _ = create_user_with_workspace("rm_member@k.tech")
        membership = TeamMembership.objects.create(
            workspace=ws, user=member, role=TeamMembership.Role.MEMBER
        )
        res = owner_client.delete(f"/api/v1/team/members/{membership.id}/remove/")
        assert res.status_code == 204
        assert not TeamMembership.objects.filter(id=membership.id).exists()

    def test_cannot_remove_self(self, create_user_with_workspace):
        user, ws, client = create_user_with_workspace()
        own_membership = TeamMembership.objects.get(workspace=ws, user=user)
        res = client.delete(f"/api/v1/team/members/{own_membership.id}/remove/")
        assert res.status_code == 400
