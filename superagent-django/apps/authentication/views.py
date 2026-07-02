import secrets
from django.contrib.auth import get_user_model, authenticate
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .serializers import (
    UserSerializer, LoginSerializer, RegisterSerializer,
    GoogleLoginSerializer, ForgotPasswordSerializer, ResetPasswordSerializer,
    UpdateProfileSerializer, ChangePasswordSerializer,
)

User = get_user_model()

_password_reset_tokens: dict = {}


def _get_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    return Response(
        {"user": UserSerializer(user).data, "tokens": _get_tokens(user)},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(
        request,
        username=serializer.validated_data["email"],
        password=serializer.validated_data["password"],
    )
    if not user:
        return Response(
            {"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
        )
    return Response({"user": UserSerializer(user).data, "tokens": _get_tokens(user)})


@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    serializer = GoogleLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        id_info = google_id_token.verify_oauth2_token(
            serializer.validated_data["id_token"],
            google_requests.Request(),
            settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"]["client_id"],
        )
        email = id_info["email"]
        name = id_info.get("name", "")
        google_id = id_info["sub"]
        avatar = id_info.get("picture", "")
    except Exception:
        return Response({"detail": "Invalid Google token."}, status=status.HTTP_400_BAD_REQUEST)

    from django.utils.text import slugify
    import uuid as _uuid
    from .models import Workspace
    from apps.team.models import TeamMembership

    user, created = User.objects.get_or_create(
        email=email,
        defaults={"name": name, "google_id": google_id, "avatar_url": avatar},
    )
    if not user.google_id:
        user.google_id = google_id
        user.save(update_fields=["google_id"])

    if created and not user.memberships.exists():
        base_slug = slugify(email.split("@")[0]) or "workspace"
        slug = base_slug
        if Workspace.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{_uuid.uuid4().hex[:6]}"
        workspace = Workspace.objects.create(
            name=f"{name or email.split(chr(64))[0]}'s Workspace",
            slug=slug,
            owner=user,
        )
        TeamMembership.objects.create(
            workspace=workspace,
            user=user,
            role=TeamMembership.Role.OWNER,
        )

    return Response({"user": UserSerializer(user).data, "tokens": _get_tokens(user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    try:
        refresh_token = request.data.get("refresh")
        if refresh_token:
            token = RefreshToken(refresh_token)
            token.blacklist()
    except TokenError:
        pass
    return Response({"detail": "Logged out."}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def token_refresh(request):
    refresh_token = request.data.get("refresh")
    if not refresh_token:
        return Response({"detail": "refresh token required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        token = RefreshToken(refresh_token)
        return Response({"access": str(token.access_token)})
    except TokenError as e:
        return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["POST"])
@permission_classes([AllowAny])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    email = serializer.validated_data["email"]

    try:
        user = User.objects.get(email=email)
        token = secrets.token_urlsafe(32)
        _password_reset_tokens[token] = user.pk
        reset_url = f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')}/reset-password?token={token}"
        send_mail(
            subject="Reset your Super Agent password",
            message=f"Click here to reset: {reset_url}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=True,
        )
    except User.DoesNotExist:
        pass

    return Response({"detail": "If that email exists, a reset link has been sent."})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    token = serializer.validated_data["token"]
    user_pk = _password_reset_tokens.pop(token, None)
    if not user_pk:
        return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = User.objects.get(pk=user_pk)
        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password reset successful."})
    except User.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)


# Profile views

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user, context={"request": request}).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def update_profile(request):
    serializer = UpdateProfileSerializer(request.user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(UserSerializer(request.user, context={"request": request}).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = request.user
    if not user.check_password(serializer.validated_data["current_password"]):
        return Response({"detail": "Current password incorrect."}, status=status.HTTP_400_BAD_REQUEST)
    user.set_password(serializer.validated_data["new_password"])
    user.save(update_fields=["password"])
    return Response({"detail": "Password changed."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_settings(request):
    from apps.integrations.models import Integration
    from apps.approvals.models import ApprovalRule
    from apps.costs.models import DailyCost, Budget
    from apps.team.models import TeamMembership
    from apps.notifications.models import NotificationSettings
    import datetime

    user = request.user
    membership = user.memberships.select_related("workspace").first()
    workspace = membership.workspace if membership else None

    role_label = {"owner": "Owner", "admin": "Admin", "member": "Member", "viewer": "Viewer"}.get(
        membership.role if membership else "", "Member"
    )
    plan_label = "Pro plan"
    badge_label = "%s · %s" % (role_label, plan_label)

    connected_count = 0
    if workspace:
        connected_count = Integration.objects.filter(
            workspace=workspace, user=user, status=Integration.Status.ACTIVE
        ).count()

    notif_settings = None
    notifications_label = "All alerts on"
    if workspace:
        notif_settings = NotificationSettings.objects.filter(user=user, workspace=workspace).first()
    if notif_settings:
        flags = [
            notif_settings.push_enabled,
            notif_settings.email_on_task_complete,
            notif_settings.email_on_task_failed,
            notif_settings.email_on_approval_needed,
            notif_settings.email_on_budget_alert,
        ]
        if all(flags):
            notifications_label = "All alerts on"
        elif not any(flags):
            notifications_label = "All alerts off"
        else:
            notifications_label = "Some alerts on"

    rules_count = 0
    if workspace:
        rules_count = ApprovalRule.objects.filter(workspace=workspace).count()
    rules_label = "%d rule%s active" % (rules_count, "s" if rules_count != 1 else "")

    today = datetime.date.today()
    month_start = today.replace(day=1)
    monthly_cost = 0.0
    budget_label = "No budget set"
    if workspace:
        from django.db.models import Sum
        agg = DailyCost.objects.filter(workspace=workspace, date__gte=month_start).aggregate(total=Sum("total_cost_usd"))
        monthly_cost = float(agg["total"] or 0)
        budget = Budget.objects.filter(workspace=workspace, period=Budget.Period.MONTHLY).first()
        if budget:
            budget_label = "\u20ac%.2f / month" % float(budget.limit_usd)

    member_count = 0
    if workspace:
        member_count = TeamMembership.objects.filter(workspace=workspace).count()

    return Response({
        "header": {
            "name": user.name or user.email.split("@")[0],
            "email": user.email,
            "avatar_url": user.avatar_url or None,
            "role": membership.role if membership else "member",
            "role_label": role_label,
            "plan": "pro",
            "plan_label": plan_label,
            "badge_label": badge_label,
        },
        "integrations": {"items": [{"key": "connected_apps", "title": "Connected apps",
            "subtitle": "%d connected" % connected_count, "icon": "grid",
            "route": "/integrations", "badge_count": connected_count}]},
        "control": {"items": [
            {"key": "notifications", "title": "Notifications", "subtitle": notifications_label, "icon": "bell"},
            {"key": "approval_rules", "title": "Approval rules", "subtitle": rules_label, "icon": "shield", "badge_count": rules_count},
            {"key": "costs", "title": "Costs", "subtitle": "\u20ac%.2f this month" % monthly_cost, "icon": "euro-sign"},
            {"key": "budget_limit", "title": "Budget limit", "subtitle": budget_label, "icon": "piggy-bank"},
        ]},
        "team": {"items": [{"key": "team_members", "title": "Team members",
            "subtitle": "%d member%s" % (member_count, "s" if member_count != 1 else ""),
            "icon": "users", "badge_count": member_count}]},
        "account": {"items": [
            {"key": "change_password", "title": "Change password", "is_destructive": False},
            {"key": "sign_out", "title": "Sign out", "is_destructive": False},
            {"key": "delete_account", "title": "Delete account", "is_destructive": True},
        ]},
    })


@api_view(["GET"])
@permission_classes([])
def health_check(request):
    from django.db import connection
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    return Response({
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
    }, status=200)
