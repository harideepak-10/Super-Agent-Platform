from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AuditEvent
from .serializers import AuditEventSerializer


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_list(request):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(workspace=workspace).order_by("-created_at")

    event_type = request.query_params.get("event_type")
    if event_type:
        events = events.filter(event_type=event_type)

    resource_type = request.query_params.get("resource_type")
    resource_id = request.query_params.get("resource_id")
    if resource_type:
        events = events.filter(resource_type=resource_type)
    if resource_id:
        events = events.filter(resource_id=resource_id)

    actor_id = request.query_params.get("actor_id")
    if actor_id:
        events = events.filter(actor_id=actor_id)

    # Date range
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    if from_date:
        events = events.filter(created_at__date__gte=from_date)
    if to_date:
        events = events.filter(created_at__date__lte=to_date)

    page_size = min(int(request.query_params.get("page_size", 50)), 200)
    events = events[:page_size]
    return Response(AuditEventSerializer(events, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_event_types(request):
    return Response([
        {"value": choice[0], "label": choice[1]}
        for choice in AuditEvent.EventType.choices
    ])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_summary(request):
    from django.db.models import Count
    workspace = _get_workspace(request)
    summary = (
        AuditEvent.objects
        .filter(workspace=workspace)
        .values("event_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return Response(list(summary))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_by_resource(request, resource_type, resource_id):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(
        workspace=workspace,
        resource_type=resource_type,
        resource_id=resource_id,
    ).order_by("-created_at")
    return Response(AuditEventSerializer(events, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_actor(request, actor_id):
    workspace = _get_workspace(request)
    events = AuditEvent.objects.filter(
        workspace=workspace, actor_id=actor_id
    ).order_by("-created_at")[:100]
    return Response(AuditEventSerializer(events, many=True).data)
