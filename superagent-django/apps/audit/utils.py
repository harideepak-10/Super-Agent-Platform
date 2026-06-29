from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rest_framework.request import Request
    from apps.authentication.models import Workspace


def log_event(
    request,
    event_type: str,
    resource_type: str = "",
    resource_id: str = "",
    workspace=None,
    metadata: dict | None = None,
):
    """Convenience helper to create an AuditEvent."""
    try:
        from .models import AuditEvent

        ip = _get_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "") if request else ""
        user = getattr(request, "user", None)
        if user and not user.is_authenticated:
            user = None

        AuditEvent.objects.create(
            workspace=workspace,
            actor=user,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            ip_address=ip,
            user_agent=ua,
        )
    except Exception:
        pass  # Audit log must never break the main flow


def _get_ip(request) -> str | None:
    if not request:
        return None
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
