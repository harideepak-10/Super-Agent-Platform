"""
ListCustomerProfilesTool — list all customer profiles in the workspace.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
from core.tools.base_tool import BaseTool, ToolZone


class ListCustomerProfilesTool(BaseTool):
    """List all customer profiles with their interaction summary.

    Input::

        {
            "workspace_id": "...",    (required)
            "limit":        20        (optional, default 20)
        }

    Returns::

        {
            "profiles": [
                {
                    "id":                  "...",
                    "email":               "vendor@example.com",
                    "name":                "Arun Kumar",
                    "communication_style": "formal",
                    "last_interaction":    "2026-07-01",
                    "interaction_count":   5
                }
            ],
            "total": 12
        }
    """

    name: str = "list_customer_profiles"
    description: str = (
        "List all known customer profiles in the workspace. "
        "Input JSON: {\"workspace_id\": \"...\", \"limit\": 20}. "
        "Returns a list of customers with their communication preferences and last interaction date."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            data = {}

        workspace_id = data.get("workspace_id", "")
        limit        = int(data.get("limit", 20))

        if not workspace_id:
            return json.dumps({"error": "'workspace_id' is required."})

        try:
            from apps.memory.models import CustomerProfile
            profiles = CustomerProfile.objects.filter(
                workspace_id=workspace_id
            ).order_by("-updated_at")[:limit]

            return json.dumps({
                "profiles": [
                    {
                        "id":                  str(p.id),
                        "email":               p.email,
                        "name":                p.name or "",
                        "company":             p.company or "",
                        "communication_style": p.communication_style or "",
                        "last_interaction":    p.updated_at.strftime("%Y-%m-%d") if p.updated_at else "",
                        "tags":                p.tags or [],
                    }
                    for p in profiles
                ],
                "total": CustomerProfile.objects.filter(workspace_id=workspace_id).count(),
            }, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "limit":        {"type": "integer"},
                },
                "required": ["workspace_id"],
            },
        }}
