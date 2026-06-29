from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Notification, NotificationSettings
from .serializers import NotificationSerializer, NotificationSettingsSerializer


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_list(request):
    workspace = _get_workspace(request)
    notifications = Notification.objects.filter(
        user=request.user, workspace=workspace
    ).order_by("-created_at")

    unread_only = request.query_params.get("unread")
    if unread_only == "true":
        notifications = notifications.filter(is_read=False)

    return Response(NotificationSerializer(notifications[:50], many=True).data)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def notification_detail(request, pk):
    workspace = _get_workspace(request)
    notification = get_object_or_404(Notification, id=pk, user=request.user, workspace=workspace)

    if request.method == "DELETE":
        notification.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        # Mark single notification as read
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])

    return Response(NotificationSerializer(notification).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_all_read(request):
    workspace = _get_workspace(request)
    Notification.objects.filter(
        user=request.user, workspace=workspace, is_read=False
    ).update(is_read=True, read_at=timezone.now())
    return Response({"detail": "All notifications marked as read."})


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def notification_settings(request):
    workspace = _get_workspace(request)
    settings_obj, _ = NotificationSettings.objects.get_or_create(
        user=request.user, workspace=workspace
    )
    if request.method == "GET":
        return Response(NotificationSettingsSerializer(settings_obj).data)

    serializer = NotificationSettingsSerializer(settings_obj, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(NotificationSettingsSerializer(settings_obj).data)
