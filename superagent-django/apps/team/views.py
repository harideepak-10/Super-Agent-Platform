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
    from django.core.mail import send_mail
    from django.conf import settings

    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace found."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = InviteMemberSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data["email"]
    role = serializer.validated_data["role"]
    token = secrets.token_urlsafe(32)

    invitation = TeamInvitation.objects.create(
        workspace=workspace,
        email=email,
        role=role,
        invited_by=request.user,
        token=token,
        expires_at=timezone.now() + timedelta(days=7),
    )

    invite_url = f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')}/accept-invite?token={token}"
    send_mail(
        subject=f"You're invited to join {workspace.name} on Super Agent",
        message=f"Click here to accept: {invite_url}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=True,
    )

    log_event(request, "team_invited", "invitation", str(invitation.id), workspace, {"email": email})
    return Response(TeamInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_invite(request):
    token = request.data.get("token")
    if not token:
        return Response({"detail": "token required."}, status=status.HTTP_400_BAD_REQUEST)

    invitation = get_object_or_404(TeamInvitation, token=token, status=TeamInvitation.Status.PENDING)
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
    return Response(TeamMemberSerializer(membership).data)
