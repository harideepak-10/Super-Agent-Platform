"""
CustomerMemoryTool — read/write persistent customer context (GREEN zone).

Allows the agent to:
1. Look up a customer profile before drafting a reply
2. Update the profile after an interaction
"""

from __future__ import annotations
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


class GetCustomerMemoryTool(BaseTool):
    name = "get_customer_memory"
    description = (
        "Retrieve persistent memory for a customer by their email address. "
        "Input: { email: str }. "
        "Returns customer preferences, communication style, escalation contacts, "
        "previous interaction summary, and custom instructions for this customer. "
        "Always call this before drafting a reply to a known customer."
    )
    zone = ToolZone.GREEN

    def run(self, tool_input: dict[str, Any]) -> Any:
        email = tool_input.get("email", "").strip().lower()
        workspace_id = tool_input.get("workspace_id")

        if not email:
            return {"error": "email is required"}

        try:
            import django
            from apps.memory.models import CustomerProfile

            qs = CustomerProfile.objects.filter(email=email)
            if workspace_id:
                qs = qs.filter(workspace_id=workspace_id)

            profile = qs.first()
            if not profile:
                return {
                    "found": False,
                    "email": email,
                    "message": "No existing customer profile. This may be a new contact.",
                }

            return {"found": True, **profile.to_context_dict()}

        except Exception as exc:
            return {"error": str(exc), "found": False}


class UpdateCustomerMemoryTool(BaseTool):
    name = "update_customer_memory"
    description = (
        "Update the persistent memory for a customer after an interaction. "
        "Input: { email: str, workspace_id: str, updates: dict }. "
        "Allowed updates: agent_notes, interaction_summary, communication_style, "
        "preferred_language, common_topics, custom_instructions. "
        "Call this at the end of every task involving a customer."
    )
    zone = ToolZone.GREEN

    ALLOWED_FIELDS = {
        "agent_notes", "interaction_summary", "communication_style",
        "preferred_language", "common_topics", "custom_instructions",
        "name", "company", "role",
    }

    def run(self, tool_input: dict[str, Any]) -> Any:
        email = tool_input.get("email", "").strip().lower()
        workspace_id = tool_input.get("workspace_id")
        updates = tool_input.get("updates", {})

        if not email:
            return {"error": "email is required"}
        if not updates:
            return {"error": "updates dict is required"}

        # Filter to only allowed fields
        safe_updates = {k: v for k, v in updates.items() if k in self.ALLOWED_FIELDS}

        try:
            from django.utils import timezone
            from apps.memory.models import CustomerProfile

            profile, created = CustomerProfile.objects.get_or_create(
                email=email,
                workspace_id=workspace_id,
                defaults={"name": updates.get("name", ""), "company": updates.get("company", "")},
            )

            for field, value in safe_updates.items():
                setattr(profile, field, value)

            profile.interaction_count += 1
            profile.last_interaction_at = timezone.now()
            profile.save()

            return {
                "updated": True,
                "email": email,
                "created_new": created,
                "fields_updated": list(safe_updates.keys()),
                "interaction_count": profile.interaction_count,
            }

        except Exception as exc:
            return {"error": str(exc), "updated": False}
