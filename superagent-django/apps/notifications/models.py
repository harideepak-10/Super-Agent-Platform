import uuid
from django.db import models
from django.conf import settings


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        TASK_COMPLETE = "task_complete", "Task Complete"
        TASK_FAILED = "task_failed", "Task Failed"
        APPROVAL_NEEDED = "approval_needed", "Approval Needed"
        APPROVAL_DECIDED = "approval_decided", "Approval Decided"
        BUDGET_ALERT = "budget_alert", "Budget Alert"
        TEAM_INVITE = "team_invite", "Team Invite"
        INTEGRATION_ERROR = "integration_error", "Integration Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)

    # Link to related resource
    resource_type = models.CharField(max_length=50, blank=True)
    resource_id = models.CharField(max_length=36, blank=True)
    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.notification_type} for {self.user}"


class NotificationSettings(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_settings"
    )
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="notification_settings"
    )
    email_on_task_complete = models.BooleanField(default=True)
    email_on_task_failed = models.BooleanField(default=True)
    email_on_approval_needed = models.BooleanField(default=True)
    email_on_budget_alert = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_settings"
        unique_together = ("user", "workspace")

    def __str__(self):
        return f"NotifSettings for {self.user}"
