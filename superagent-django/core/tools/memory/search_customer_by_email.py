"""
SearchCustomerByEmailTool — quick customer profile lookup by email address.

Zone: GREEN — runs automatically, no human approval required.
"""
from __future__ import annotations
import json
from core.tools.base_tool import BaseTool, ToolZone


class SearchCustomerByEmailTool(BaseTool):
    """Look up a customer profile by email address.

    Faster than get_customer_memory when you just need to check if
    a customer exists before deciding what to do.

    Input::

        {
            "email":        "vendor@example.com",
            "workspace_id": "..."      (optional)
        }

    Returns::

        {
            "found":   true,
            "profile": {
                "id":                  "...",
                "email":               "vendor@example.com",
                "name":                "Arun Kumar",
                "company":             "ABC Corp",
                "communication_style": "formal",
                "preferred_language":  "English",
                "custom_instructions": "Always CC accounts@abc.com",
                "tags":                ["supplier", "priority"],
                "notes":               "Long-term vendor since 2024"
            }
        }
        OR
        {"found": false, "profile": null}
    """

    name: str = "search_customer_by_email"
    description: str = (
        "Look up a customer profile by email address OR name. "
        "Input JSON: {\"email\": \"arun@example.com\"} to search by email, "
        "OR {\"name\": \"Arun\"} to search by name (partial match). "
        "Returns the full profile if found, or found=false if unknown. "
        "Use this to resolve names to email addresses before creating a meeting."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        email        = (data.get("email") or "").strip().lower()
        name_query   = (data.get("name") or "").strip()
        workspace_id = data.get("workspace_id", "")

        if not email and not name_query:
            return json.dumps({"error": "Either 'email' or 'name' is required."})

        try:
            from apps.memory.models import CustomerProfile

            if email:
                qs = CustomerProfile.objects.filter(email=email)
            else:
                # Name search — case-insensitive partial match on name or company
                qs = CustomerProfile.objects.filter(name__icontains=name_query)

            if workspace_id:
                qs = qs.filter(workspace_id=workspace_id)

            # If name search returns multiple, return all matches
            if name_query and not email:
                profiles = list(qs[:5])
                if not profiles:
                    return json.dumps({"found": False, "profile": None, "matches": []})
                results = [
                    {
                        "id":    str(p.id),
                        "email": p.email,
                        "name":  p.name or "",
                        "company": p.company or "",
                        "communication_style": p.communication_style or "",
                    }
                    for p in profiles
                ]
                return json.dumps({
                    "found":   True,
                    "matches": results,
                    "profile": results[0],   # best match = first
                }, ensure_ascii=False, default=str)

            profile = qs.first()
            if not profile:
                return json.dumps({"found": False, "profile": None})

            return json.dumps({
                "found": True,
                "profile": {
                    "id":                  str(profile.id),
                    "email":               profile.email,
                    "name":                profile.name or "",
                    "company":             profile.company or "",
                    "communication_style": profile.communication_style or "",
                    "preferred_language":  profile.preferred_language or "English",
                    "custom_instructions": profile.custom_instructions or "",
                    "tags":                profile.tags or [],
                    "notes":               profile.notes or "",
                    "last_updated":        profile.updated_at.strftime("%Y-%m-%d") if profile.updated_at else "",
                },
            }, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "email":        {"type": "string"},
                    "workspace_id": {"type": "string"},
                },
                "required": ["email"],
            },
        }}
