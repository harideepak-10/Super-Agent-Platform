import uuid
from django.db import models
from django.conf import settings


class Approval(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.CASCADE, related_name="approvals"
    )
    step = models.ForeignKey(
        "tasks.TaskStep", on_delete=models.SET_NULL, null=True, blank=True, related_name="approval"
    )

    tool_name = models.CharField(max_length=100)
    tool_input = models.JSONField()
    tool_zone = models.CharField(max_length=10, default="yellow")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_approvals"
    )
    reviewer_note = models.TextField(blank=True)

    # Snapshot of agent state needed to resume
    resume_snapshot = models.JSONField(default=dict)

    expires_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approvals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Approval for {self.tool_name} — {self.status}"


class ApprovalRule(models.Model):
    """Configures which tools require approval in a workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="approval_rules"
    )
    tool_name = models.CharField(max_length=100)

    # If True, tool always requires approval regardless of zone
    always_require = models.BooleanField(default=False)

    # If True, tool is completely blocked (RED zone override)
    always_block = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approval_rules"
        unique_together = ("workspace", "tool_name")

    def __str__(self):
        return f"Rule: {self.tool_name} in {self.workspace}"
