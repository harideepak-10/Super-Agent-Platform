"""
QuickTask — pinned/auto-promoted shortcut tasks shown on the dashboard.

Rules:
  - Max 4 quick tasks per user per workspace.
  - Source 'default'  → seeded on first use (can be removed, never re-seeded).
  - Source 'manual'   → user added manually.
  - Source 'auto'     → auto-promoted when the same prompt is run 3+ times.
  - run_count is incremented each time the quick task is triggered via the run endpoint.
"""

import uuid
from django.conf import settings
from django.db import models


class QuickTask(models.Model):
    class Source(models.TextChoices):
        DEFAULT = "default", "Default"
        MANUAL  = "manual",  "Manual"
        AUTO    = "auto",    "Auto-promoted"

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace   = models.ForeignKey(
        "authentication.Workspace",
        on_delete=models.CASCADE,
        related_name="quick_tasks",
    )
    user        = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quick_tasks",
    )

    title       = models.CharField(max_length=120)
    prompt      = models.TextField()
    agent_type  = models.CharField(max_length=50, blank=True, default="")
    icon        = models.CharField(max_length=50, default="zap")

    run_count   = models.PositiveIntegerField(default=0)
    source      = models.CharField(max_length=20, choices=Source.choices, default=Source.DEFAULT)
    order       = models.PositiveIntegerField(default=0)

    created_at  = models.DateTimeField(auto_now_add=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering          = ["order", "-run_count"]
        # Prevent exact duplicate prompts per user
        unique_together   = [["workspace", "user", "title"]]

    def __str__(self):
        return f"{self.title} ({self.user_id})"
