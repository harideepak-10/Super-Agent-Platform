import logging
import secrets
import threading
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate
from django.core.mail import send_mail

logger = logging.getLogger(__name__)
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .models import PasswordResetToken
from .serializers import (
    UserSerializer, LoginSerializer, RegisterSerializer,
    GoogleLoginSerializer, ForgotPasswordSerializer, ResetPasswordSerializer,
    UpdateProfileSerializer, ChangePasswordSerializer,
)

User = get_user_model()

_TOKEN_TTL_HOURS = 1


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
            name=f"{name or email.split('@')[0]}'s Workspace",
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

        # Clean up old tokens for this user before creating a new one
        PasswordResetToken.objects.filter(user=user).delete()

        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(hours=_TOKEN_TTL_HOURS)
        PasswordResetToken.objects.create(token=token, user=user, expires_at=expires_at)

        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"

        # Send email in background so API returns immediately
        def _send():
            try:
                send_mail(
                    subject="Reset your Super Agent password",
                    message=f"Click here to reset your password:\n\n{reset_url}\n\nThis link expires in {_TOKEN_TTL_HOURS} hour.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
                logger.info("Password reset email sent to %s", email)
            except Exception as exc:
                logger.error("Failed to send password reset email to %s: %s", email, exc)

        threading.Thread(target=_send, daemon=True).start()
    except User.DoesNotExist:
        pass  # Don't reveal whether the email exists

    return Response({"detail": "If that email exists, a reset link has been sent."})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    token = serializer.validated_data["token"]

    try:
        reset_token = PasswordResetToken.objects.select_related("user").get(token=token)
    except PasswordResetToken.DoesNotExist:
        return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    if not reset_token.is_valid():
        return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    user = reset_token.user
    user.set_password(serializer.validated_data["password"])
    user.save(update_fields=["password"])

    # Mark token as used so it can't be replayed
    reset_token.used = True
    reset_token.save(update_fields=["used"])

    return Response({"detail": "Password reset successful."})


# ── Email debug (remove after confirming email works) ─────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def test_email(request):
    """Temporary debug endpoint — sends a test email and returns success/error directly."""
    to = request.data.get("email", "")
    if not to:
        return Response({"error": "Provide 'email' in body."}, status=400)
    try:
        send_mail(
            subject="Super Agent — test email",
            message="If you received this, email sending is working.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
        return Response({
            "status": "sent",
            "backend": settings.EMAIL_BACKEND,
            "host": settings.EMAIL_HOST,
            "user": settings.EMAIL_HOST_USER,
            "from": settings.DEFAULT_FROM_EMAIL,
        })
    except Exception as exc:
        return Response({
            "status": "failed",
            "error": str(exc),
            "backend": settings.EMAIL_BACKEND,
            "host": settings.EMAIL_HOST,
            "user": settings.EMAIL_HOST_USER,
        }, status=500)


# ── Profile views ─────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user, context={"request": request}).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def update_profile(request):
    user = request.user

    # Handle avatar file upload — accept "avatar" or "image" (Flutter image_picker)
    avatar_file = request.FILES.get("avatar") or request.FILES.get("image")
    if avatar_file:
        import os
        from django.conf import settings as django_settings

        ext = os.path.splitext(avatar_file.name)[1].lower() or ".jpg"
        filename = f"{user.id}{ext}"
        avatars_dir = os.path.join(django_settings.MEDIA_ROOT, "avatars")
        os.makedirs(avatars_dir, exist_ok=True)
        file_path = os.path.join(avatars_dir, filename)

        with open(file_path, "wb+") as dest:
            for chunk in avatar_file.chunks():
                dest.write(chunk)

        relative_url = f"{django_settings.MEDIA_URL}avatars/{filename}"
        user.avatar_url = request.build_absolute_uri(relative_url)
        user.save(update_fields=["avatar_url"])

    serializer = UpdateProfileSerializer(user, data=request.data, partial=True)
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

    role_label = {"owner": "Owner", "admin": "Operator", "member": "Member", "viewer": "Viewer"}.get(
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
        monthly_cost = round(float(agg["total"] or 0) * 0.92, 4)
        budget = Budget.objects.filter(workspace=workspace, period=Budget.Period.MONTHLY).first()
        if budget:
            budget_label = "€%.2f / month" % round(float(budget.limit_usd) * 0.92, 2)

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
            {"key": "costs", "title": "Costs", "subtitle": "€%.2f this month" % monthly_cost, "icon": "euro-sign"},
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
@permission_classes([IsAuthenticated])
def settings_summary(request):
    """
    GET /api/v1/profile/settings-summary/

    Returns the four key settings cards for the Settings screen.

    Response::

        {
            "connected_apps": {
                "count": 3,
                "apps":  ["gmail", "google_calendar", "google_drive"]
            },
            "cost": {
                "this_month_eur": 1.31,
                "currency":       "EUR"
            },
            "budget": {
                "set":        true,
                "limit_eur":  18.40,
                "period":     "monthly",
                "used_pct":   7,
                "status":     "ok"        // "ok" | "warning" | "critical"
            },
            "team": {
                "member_count": 4,
                "members": [
                    {"name": "Deepak", "email": "...", "role": "owner", "avatar_url": "..."},
                    ...
                ]
            }
        }
    """
    import datetime
    from django.db.models import Sum
    from apps.integrations.models import Integration
    from apps.costs.models import DailyCost, Budget
    from apps.team.models import TeamMembership

    membership = request.user.memberships.select_related("workspace").first()
    workspace  = membership.workspace if membership else None

    if not workspace:
        return Response({"detail": "No workspace."}, status=status.HTTP_400_BAD_REQUEST)

    # --- Connected Apps ---
    active_integrations = Integration.objects.filter(
        workspace=workspace, status=Integration.Status.ACTIVE
    )
    connected_apps = list(active_integrations.values_list("provider", flat=True).distinct())

    # --- Cost (this month) ---
    _USD_TO_EUR = 0.92
    today       = datetime.date.today()
    month_start = today.replace(day=1)
    agg = DailyCost.objects.filter(
        workspace=workspace, date__gte=month_start
    ).aggregate(total=Sum("total_cost_usd"))
    monthly_cost_eur = round(float(agg["total"] or 0) * _USD_TO_EUR, 4)

    # --- Budget ---
    budget = Budget.objects.filter(workspace=workspace, period=Budget.Period.MONTHLY).first()
    budget_data = {"set": False}
    if budget:
        limit_eur = round(float(budget.limit_usd) * _USD_TO_EUR, 2)
        used_pct  = int((monthly_cost_eur / limit_eur * 100)) if limit_eur > 0 else 0
        budget_data = {
            "set":       True,
            "limit_eur": limit_eur,
            "period":    budget.period,
            "used_pct":  min(used_pct, 100),
            "status":    budget.alert_status,
        }

    # --- Team ---
    memberships  = TeamMembership.objects.filter(workspace=workspace).select_related("user")
    team_members = [
        {
            "name":       m.user.name or m.user.email.split("@")[0],
            "email":      m.user.email,
            "role":       m.role,
            "avatar_url": m.user.avatar_url or None,
        }
        for m in memberships
    ]

    return Response({
        "connected_apps": {
            "count": len(connected_apps),
            "apps":  connected_apps,
        },
        "cost": {
            "this_month_eur": monthly_cost_eur,
            "currency":       "EUR",
        },
        "budget": budget_data,
        "team": {
            "member_count": len(team_members),
            "members":      team_members,
        },
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
