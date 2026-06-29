import uuid
from django.db import models
from django.conf import settings


class AuditEvent(models.Model):
    class EventType(models.TextChoices):
        TASK_CREATED = "task_created", "Task Created"
        TASK_STARTED = "task_started", "Task Started"
        TASK_COMPLETED = "task_completed", "Task Completed"
        TASK_FAILED = "task_failed", "Task Failed"
        TASK_CANCELLED = "task_cancelled", "Task Cancelled"
        TOOL_CALLED = "tool_called", "Tool Called"
        APPROVAL_REQUESTED = "approval_requested", "Approval Requested"
        APPROVAL_GRANTED = "approval_granted", "Approval Granted"
        APPROVAL_REJECTED = "approval_rejected", "Approval Rejected"
        AUTH_LOGIN = "auth_login", "User Login"
        AUTH_LOGOUT = "auth_logout", "User Logout"
        INTEGRATION_CONNECTED = "integration_connected", "Integration Connected"
        INTEGRATION_REVOKED = "integration_revoked", "Integration Revoked"
        AGENT_CREATED = "agent_created", "Agent Created"
        AGENT_UPDATED = "agent_updated", "Agent Updated"
        AGENT_DELETED = "agent_deleted", "Agent Deleted"
        TEAM_INVITED = "team_invited", "Team Member Invited"
        TEAM_REMOVED = "team_removed", "Team Member Removed"
        BUDGET_ALERT = "budget_alert", "Budget Alert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="audit_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_events"
    )
    event_type = models.CharField(max_length=50, choices=EventType.choices)

    # Generic FK-style references (stored as UUIDs, not enforced by DB)
    resource_type = models.CharField(max_length=50, blank=True)  # "task", "agent", "approval", etc.
    resource_id = models.CharField(max_length=36, blank=True)

    metadata = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "event_type"]),
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self):
        return f"{self.event_type} by {self.actor} at {self.created_at}"
