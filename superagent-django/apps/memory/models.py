"""
Customer Context Memory — persistent per-customer profiles.

Stores communication preferences, historical context, escalation contacts,
and instructions so the Email Agent improves with every interaction.
"""

import uuid
from django.db import models
from django.conf import settings


class CustomerProfile(models.Model):
    """Persistent memory for a customer/contact."""

    class CommunicationStyle(models.TextChoices):
        FORMAL = "formal", "Formal"
        CASUAL = "casual", "Casual"
        TECHNICAL = "technical", "Technical"
        BRIEF = "brief", "Brief & Direct"

    class UrgencyPreference(models.TextChoices):
        RESPOND_SAME_DAY = "same_day", "Same Day"
        RESPOND_24H = "24h", "Within 24 Hours"
        RESPOND_48H = "48h", "Within 48 Hours"
        LOW_PRIORITY = "low", "Low Priority"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "authentication.Workspace", on_delete=models.CASCADE, related_name="customer_profiles"
    )

    # Identity
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    company = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=255, blank=True)

    # Communication preferences
    preferred_language = models.CharField(max_length=10, default="en")
    communication_style = models.CharField(
        max_length=20, choices=CommunicationStyle.choices, default=CommunicationStyle.FORMAL
    )
    urgency_preference = models.CharField(
        max_length=20, choices=UrgencyPreference.choices, default=UrgencyPreference.RESPOND_24H
    )

    # Custom instructions the agent should always follow for this customer
    custom_instructions = models.TextField(blank=True)

    # Escalation contacts
    escalation_contacts = models.JSONField(default=list)
    # e.g. [{"name": "John", "email": "john@co.com", "reason": "billing disputes"}]

    # Summary of previous interactions
    interaction_summary = models.TextField(blank=True)

    # Topics/products this customer usually discusses
    common_topics = models.JSONField(default=list)

    # Agent-generated notes (updated after each task)
    agent_notes = models.TextField(blank=True)

    interaction_count = models.PositiveIntegerField(default=0)
    last_interaction_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_profiles"
        unique_together = ("workspace", "email")
        ordering = ["-last_interaction_at"]

    def __str__(self):
        return f"{self.name or self.email} ({self.company})"

    def to_context_dict(self) -> dict:
        """Return a dict the agent can inject into its system prompt."""
        return {
            "email": self.email,
            "name": self.name,
            "company": self.company,
            "role": self.role,
            "preferred_language": self.preferred_language,
            "communication_style": self.communication_style,
            "urgency_preference": self.urgency_preference,
            "custom_instructions": self.custom_instructions,
            "escalation_contacts": self.escalation_contacts,
            "common_topics": self.common_topics,
            "interaction_summary": self.interaction_summary,
            "agent_notes": self.agent_notes,
            "interaction_count": self.interaction_count,
        }


class CustomerInteraction(models.Model):
    """Log of every agent interaction with a customer — builds up memory over time."""

    class InteractionType(models.TextChoices):
        EMAIL_READ = "email_read", "Email Read"
        EMAIL_CLASSIFIED = "email_classified", "Email Classified"
        DRAFT_CREATED = "draft_created", "Draft Created"
        EMAIL_SENT = "email_sent", "Email Sent"
        THREAD_SUMMARIZED = "thread_summarized", "Thread Summarized"
        ACTION_ITEMS_EXTRACTED = "action_items_extracted", "Action Items Extracted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        CustomerProfile, on_delete=models.CASCADE, related_name="interactions"
    )
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.SET_NULL, null=True, blank=True, related_name="customer_interactions"
    )
    interaction_type = models.CharField(max_length=30, choices=InteractionType.choices)
    summary = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_interactions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.interaction_type} — {self.customer}"
