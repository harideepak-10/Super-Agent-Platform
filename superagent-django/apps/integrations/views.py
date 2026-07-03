from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
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
    integrations = Integration.objects.filter(
        workspace=workspace, user=request.user
    ).exclude(status=Integration.Status.REVOKED)
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
        import os
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from core.tools.gmail.read_emails import ReadEmailsTool

        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        gmail_service = build("gmail", "v1", credentials=creds)
        tool = ReadEmailsTool(gmail_service=gmail_service)
        max_results = int(request.query_params.get("max_results", 10))
        import json as _json
        result = tool.run(_json.dumps({"limit": max_results}))
        return Response(_json.loads(result))
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


# ---------------------------------------------------------------------------
# Gmail OAuth flow
# ---------------------------------------------------------------------------

def _gmail_oauth_client():
    """Return (client_id, client_secret) from settings."""
    from django.conf import settings
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    client_secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set.")
    return client_id, client_secret


def _gmail_redirect_uri(request):
    from django.conf import settings
    base = getattr(settings, "BACKEND_URL", "").rstrip("/")
    if not base:
        base = request.build_absolute_uri("/").rstrip("/")
    return base + "/api/v1/integrations/gmail/callback/"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gmail_auth_url(request):
    """
    GET /api/v1/integrations/gmail/auth-url/
    Returns the Google OAuth consent-screen URL.
    The mobile app opens this URL in a browser; after consent Google
    redirects to /api/v1/integrations/gmail/callback/.
    """
    try:
        client_id, _ = _gmail_oauth_client()
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    import urllib.parse
    redirect_uri = _gmail_redirect_uri(request)

    # Encode the user ID in state so the callback knows which user to save for
    state = str(request.user.id)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return Response({"auth_url": auth_url, "redirect_uri": redirect_uri})


@api_view(["GET"])
@permission_classes([AllowAny])
def gmail_callback(request):
    """
    GET /api/v1/integrations/gmail/callback/
    Google redirects here after the user grants (or denies) consent.
    Exchanges the auth code for tokens and saves the integration.
    """
    code  = request.query_params.get("code")
    state = request.query_params.get("state")  # user ID
    error = request.query_params.get("error")

    if error:
        return Response({"detail": "OAuth denied: {}".format(error)}, status=status.HTTP_400_BAD_REQUEST)
    if not code or not state:
        return Response({"detail": "Missing code or state."}, status=status.HTTP_400_BAD_REQUEST)

    # Look up the user from state
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user = User.objects.get(id=state)
    except (User.DoesNotExist, Exception):
        return Response({"detail": "Invalid state."}, status=status.HTTP_400_BAD_REQUEST)

    # Exchange code for tokens
    try:
        client_id, client_secret = _gmail_oauth_client()
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    import requests as http_requests
    redirect_uri = _gmail_redirect_uri(request)
    token_resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if not token_resp.ok:
        return Response({"detail": "Token exchange failed.", "error": token_resp.json()},
                        status=status.HTTP_400_BAD_REQUEST)

    tokens = token_resp.json()
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    scopes        = tokens.get("scope", "").split()

    # Fetch user's Gmail address
    profile_resp = http_requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        headers={"Authorization": "Bearer " + access_token},
    )
    email_address = ""
    if profile_resp.ok:
        email_address = profile_resp.json().get("email", "")

    # Find the user's workspace
    membership = user.memberships.select_related("workspace").first()
    if not membership:
        return Response({"detail": "User has no workspace."}, status=status.HTTP_400_BAD_REQUEST)
    workspace = membership.workspace

    # Save / update the integration
    integration, _ = Integration.objects.update_or_create(
        workspace=workspace,
        user=user,
        provider=Integration.Provider.GMAIL,
        defaults={
            "status": Integration.Status.ACTIVE,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scopes": scopes,
            "metadata": {"email": email_address},
        },
    )

    # Return a simple success page the mobile WebView can detect
    from django.http import HttpResponse
    return HttpResponse(
        "<html><body><h2>Gmail connected!</h2>"
        "<p>Your Gmail account <b>{}</b> is now linked. You can close this window.</p>"
        "</body></html>".format(email_address),
        content_type="text/html",
    )
