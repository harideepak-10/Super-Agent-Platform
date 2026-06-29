from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Integration
from .serializers import IntegrationSerializer, ConnectIntegrationSerializer
from apps.audit.utils import log_event


def _get_workspace(request):
    membership = request.user.memberships.select_related("workspace").first()
    return membership.workspace if membership else None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def integration_list(request):
    workspace = _get_workspace(request)
    integrations = Integration.objects.filter(workspace=workspace, user=request.user)
    return Response(IntegrationSerializer(integrations, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def integration_connect(request):
    workspace = _get_workspace(request)
    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = ConnectIntegrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    integration, created = Integration.objects.get_or_create(
        workspace=workspace,
        user=request.user,
        provider=data["provider"],
        defaults={
            "status": Integration.Status.ACTIVE,
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "scopes": data.get("scopes", []),
        },
    )
    if not created:
        integration.access_token = data.get("access_token", integration.access_token)
        integration.refresh_token = data.get("refresh_token", integration.refresh_token)
        integration.scopes = data.get("scopes", integration.scopes)
        integration.status = Integration.Status.ACTIVE
        integration.save()

    log_event(request, "integration_connected", "integration", str(integration.id), workspace,
              {"provider": data["provider"]})
    return Response(IntegrationSerializer(integration).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def integration_detail(request, pk):
    workspace = _get_workspace(request)
    integration = get_object_or_404(Integration, id=pk, workspace=workspace, user=request.user)
    return Response(IntegrationSerializer(integration).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def integration_disconnect(request, pk):
    workspace = _get_workspace(request)
    integration = get_object_or_404(Integration, id=pk, workspace=workspace, user=request.user)
    integration.status = Integration.Status.REVOKED
    integration.access_token = ""
    integration.refresh_token = ""
    integration.save(update_fields=["status", "access_token", "refresh_token"])
    log_event(request, "integration_revoked", "integration", str(integration.id), workspace)
    return Response({"detail": "Integration disconnected."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def integration_refresh(request, pk):
    """Re-test / refresh the integration connection."""
    workspace = _get_workspace(request)
    integration = get_object_or_404(Integration, id=pk, workspace=workspace, user=request.user)
    # TODO: call provider-specific token refresh logic
    integration.status = Integration.Status.ACTIVE
    integration.save(update_fields=["status"])
    return Response(IntegrationSerializer(integration).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def integration_status(request, pk):
    workspace = _get_workspace(request)
    integration = get_object_or_404(Integration, id=pk, workspace=workspace, user=request.user)
    return Response({
        "id": str(integration.id),
        "provider": integration.provider,
        "status": integration.status,
        "last_updated": integration.updated_at,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gmail_emails(request):
    """Proxy: fetch emails via the Gmail integration."""
    workspace = _get_workspace(request)
    integration = Integration.objects.filter(
        workspace=workspace, user=request.user,
        provider=Integration.Provider.GMAIL, status=Integration.Status.ACTIVE,
    ).first()

    if not integration:
        return Response({"detail": "Gmail not connected."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from core.tools.gmail.auth import GmailAuth
        from core.tools.gmail.read_emails import ReadEmailsTool

        auth = GmailAuth(
            client_id=integration.metadata.get("client_id", ""),
            client_secret=integration.metadata.get("client_secret", ""),
            refresh_token=integration.refresh_token,
        )
        gmail_service = auth.get_service()
        tool = ReadEmailsTool(gmail_service=gmail_service)
        max_results = int(request.query_params.get("max_results", 10))
        result = tool.run({"max_results": max_results})
        return Response(result)
    except Exception as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def available_integrations(request):
    """List all available integration providers."""
    return Response([
        {"provider": choice[0], "label": choice[1]}
        for choice in Integration.Provider.choices
    ])
