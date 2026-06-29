import uuid
from django.db import models
from django.conf import settings


class DailyCost(models.Model):
    """Aggregated daily cost per workspace."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="daily_costs"
    )
    date = models.DateField()
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    total_tokens = models.PositiveBigIntegerField(default=0)
    task_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "daily_costs"
        unique_together = ("workspace", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.workspace} — {self.date}: ${self.total_cost_usd}"


class Budget(models.Model):
    class Period(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    class AlertStatus(models.TextChoices):
        OK = "ok", "OK"
        WARNING = "warning", "Warning (>80%)"
        CRITICAL = "critical", "Critical (>95%)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="budgets"
    )
    period = models.CharField(max_length=10, choices=Period.choices, default=Period.MONTHLY)
    limit_usd = models.DecimalField(max_digits=10, decimal_places=2)
    alert_threshold_pct = models.PositiveSmallIntegerField(default=80)
    alert_status = models.CharField(max_length=10, choices=AlertStatus.choices, default=AlertStatus.OK)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "budgets"
        unique_together = ("workspace", "period")

    def __str__(self):
        return f"{self.workspace} — {self.period} budget: ${self.limit_usd}"
