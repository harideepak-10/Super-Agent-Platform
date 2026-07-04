import secrets
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import TeamMembership, TeamInvitation
from .serializers import (
    TeamMemberSerializer, InviteMemberSerializer,
    TeamInvitationSerializer, UpdateRoleSerializer,
)
from apps.audit.utils import log_event


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def member_list(request):
    workspace = _get_workspace(request)
    members = TeamMembership.objects.filter(workspace=workspace).select_related("user")
    return Response(TeamMemberSerializer(members, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def invite_member(request):
    """
    POST /api/v1/team/invite/
    A invites B by email. B must already have an account.
    No email sent — B sees the invite in-app via GET /team/invites/
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = InviteMemberSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data["email"]
    role  = serializer.validated_data["role"]

    # B must have an account
    if not User.objects.filter(email=email).exists():
        return Response(
            {"detail": "No account found with that email. Ask them to sign up first."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Don't invite someone already in the workspace
    if TeamMembership.objects.filter(workspace=workspace, user__email=email).exists():
        return Response({"detail": "This person is already in your workspace."}, status=status.HTTP_400_BAD_REQUEST)

    # Don't send duplicate pending invite
    if TeamInvitation.objects.filter(workspace=workspace, email=email, status=TeamInvitation.Status.PENDING).exists():
        return Response({"detail": "An invite is already pending for this email."}, status=status.HTTP_400_BAD_REQUEST)

    invitation = TeamInvitation.objects.create(
        workspace=workspace,
        email=email,
        role=role,
        invited_by=request.user,
        token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + timedelta(days=30),
    )

    log_event(request, "team_invited", "invitation", str(invitation.id), workspace, {"email": email})
    return Response(TeamInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_invites(request):
    """
    GET /api/v1/team/invites/
    Returns pending invites for the logged-in user.
    B calls this to see who invited them.
    """
    invites = TeamInvitation.objects.filter(
        email=request.user.email,
        status=TeamInvitation.Status.PENDING,
    ).select_related("invited_by", "workspace")

    data = []
    for inv in invites:
        data.append({
            "id":             str(inv.id),
            "workspace_name": inv.workspace.name,
            "workspace_id":   str(inv.workspace.id),
            "invited_by":     inv.invited_by.name or inv.invited_by.email,
            "invited_by_email": inv.invited_by.email,
            "role":           inv.role,
            "created_at":     inv.created_at.isoformat(),
            "expires_at":     inv.expires_at.isoformat(),
        })
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_invite(request, pk):
    """
    POST /api/v1/team/invites/<id>/accept/
    B accepts the invite — added to workspace as member.
    """
    invitation = get_object_or_404(
        TeamInvitation, id=pk,
        email=request.user.email,
        status=TeamInvitation.Status.PENDING,
    )
    if invitation.expires_at < timezone.now():
        invitation.status = TeamInvitation.Status.EXPIRED
        invitation.save(update_fields=["status"])
        return Response({"detail": "Invitation expired."}, status=status.HTTP_400_BAD_REQUEST)

    membership, _ = TeamMembership.objects.get_or_create(
        workspace=invitation.workspace,
        user=request.user,
        defaults={"role": invitation.role, "invited_by": invitation.invited_by},
    )
    invitation.status = TeamInvitation.Status.ACCEPTED
    invitation.save(update_fields=["status"])
    log_event(request, "team_invite_accepted", "invitation", str(invitation.id), invitation.workspace)
    return Response({"detail": "You joined {}.".format(invitation.workspace.name),
                     "membership": TeamMemberSerializer(membership).data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reject_invite(request, pk):
    """
    POST /api/v1/team/invites/<id>/reject/
    B rejects the invite.
    """
    invitation = get_object_or_404(
        TeamInvitation, id=pk,
        email=request.user.email,
        status=TeamInvitation.Status.PENDING,
    )
    invitation.status = TeamInvitation.Status.DECLINED
    invitation.save(update_fields=["status"])
    log_event(request, "team_invite_rejected", "invitation", str(invitation.id), invitation.workspace)
    return Response({"detail": "Invite declined."})


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_member_role(request, pk):
    workspace = _get_workspace(request)
    membership = get_object_or_404(TeamMembership, id=pk, workspace=workspace)
    serializer = UpdateRoleSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    membership.role = serializer.validated_data["role"]
    membership.save(update_fields=["role"])
    return Response(TeamMemberSerializer(membership).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_member(request, pk):
    workspace = _get_workspace(request)
    membership = get_object_or_404(TeamMembership, id=pk, workspace=workspace)
    if membership.user == request.user:
        return Response({"detail": "Cannot remove yourself."}, status=status.HTTP_400_BAD_REQUEST)
    membership.delete()
    log_event(request, "team_removed", "membership", str(pk), workspace)
    return Response(status=status.HTTP_204_NO_CONTENT)
