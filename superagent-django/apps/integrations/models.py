import uuid
from django.db import models
from django.conf import settings


class Integration(models.Model):
    class Provider(models.TextChoices):
        GMAIL = "gmail", "Gmail"
        GOOGLE_DRIVE = "google_drive", "Google Drive"
        GOOGLE_CALENDAR = "google_calendar", "Google Calendar"
        SLACK = "slack", "Slack"
        NOTION = "notion", "Notion"
        GITHUB = "github", "GitHub"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ERROR = "error", "Error"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="integrations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="integrations"
    )
    provider = models.CharField(max_length=50, choices=Provider.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INACTIVE)

    # Encrypted tokens (store as text, encrypt at application layer)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # OAuth scopes granted
    scopes = models.JSONField(default=list)

    # Provider-specific metadata (email address, channel IDs, etc.)
    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "integrations"
        unique_together = ("workspace", "user", "provider")

    def __str__(self):
        return f"{self.provider} — {self.user}"
