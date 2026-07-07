import uuid
from django.db import models
from django.conf import settings


class Task(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="tasks"
    )
    agent = models.ForeignKey(
        "agents.Agent", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tasks"
    )

    class Priority(models.TextChoices):
        ROUTINE = "routine", "Routine"
        URGENT  = "urgent",  "Urgent"

    prompt    = models.TextField()
    priority  = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    status    = models.CharField(max_length=30, choices=Status.choices, default=Status.QUEUED)
    result = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    # Celery task ID
    celery_task_id = models.CharField(max_length=255, blank=True)

    # Execution stats
    steps_taken = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=8, decimal_places=6, default=0)

    # Generic output files — populated by any agent that produces files
    # e.g. Document Agent → Drive links, Email Agent → [] (empty)
    # Shape: [{"name": "...", "url": "...", "type": "pdf|csv|drive|email|..."}]
    deliverables = models.JSONField(default=list, blank=True)

    # Optional estimate of total steps — used to compute progress_percent
    # Orchestrator sets this when splitting a task across agents
    total_steps_estimate = models.PositiveIntegerField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tasks"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Task {self.id} — {self.status}"


class TaskStep(models.Model):
    class StepType(models.TextChoices):
        THOUGHT = "thought", "Thought"
        TOOL_CALL = "tool_call", "Tool Call"
        TOOL_RESULT = "tool_result", "Tool Result"
        FINAL_ANSWER = "final_answer", "Final Answer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField()
    step_type = models.CharField(max_length=20, choices=StepType.choices)
    content = models.TextField()

    # For tool calls
    tool_name = models.CharField(max_length=100, blank=True)
    tool_input = models.JSONField(null=True, blank=True)
    tool_output = models.JSONField(null=True, blank=True)

    # Safety zone of the tool (green/yellow/red)
    tool_zone = models.CharField(max_length=10, blank=True)

    # Generic fields — work for Email Agent now, Orchestrator/Document Agent later
    # agent_name: which agent handled this step (task agent for now, sub-agent name for Orchestrator)
    agent_name = models.CharField(max_length=255, blank=True)
    # title: short human-readable label e.g. "Sending email", "Searching the web"
    title = models.CharField(max_length=255, blank=True)
    # detail: one-line description e.g. "To harideepak@...", "Query: AI news 2026"
    detail = models.TextField(blank=True)

    tokens_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_steps"
        ordering = ["step_number"]

    def __str__(self):
        return f"Step {self.step_number} ({self.step_type}) — Task {self.task_id}"
