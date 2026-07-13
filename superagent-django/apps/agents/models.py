import uuid
from django.db import models
from django.conf import settings


class Agent(models.Model):
    class AgentType(models.TextChoices):
        EMAIL = "email", "Email Agent"
        CALENDAR = "calendar", "Calendar Agent"
        RESEARCH = "research", "Research Agent"
        CUSTOM = "custom", "Custom Agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="agents"
    )
    name = models.CharField(max_length=255)
    agent_type = models.CharField(max_length=50, choices=AgentType.choices, default=AgentType.CUSTOM)
    description = models.TextField(blank=True)
    system_prompt = models.TextField(blank=True)

    # Configuration
    max_steps = models.PositiveIntegerField(default=20)
    max_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=1.0)
    llm_model = models.CharField(max_length=100, default="llama-3.3-70b-versatile")

    # Enabled tools
    tools = models.JSONField(default=list)

    # Set when created from a ready-made template (1=Email, 2=Research, 3=Document, 4=Calendar, 5=Reporting)
    template_id      = models.PositiveSmallIntegerField(null=True, blank=True)
    # Tracks which template version this agent was last synced to.
    # If template.version > template_version, the agent will be auto-synced at next task run.
    template_version = models.PositiveSmallIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_agents"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.agent_type})"
