"""
FindMissingDocsTool — GREEN zone.
Checks a required document checklist against what's available.
"""

from __future__ import annotations

import json
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


class FindMissingDocsTool(BaseTool):
    """Find required documents that are missing from a collection."""

    name = "find_missing_docs"
    description = (
        "Compare a list of required documents against available documents. "
        "Accepts 'required' (list of doc names/types) and 'available' (list of doc names/types). "
        "Returns missing, present, and a compliance percentage. "
        "Optional 'entity_name' for context in the report."
    )
    zone = ToolZone.GREEN

    def run(self, tool_input: str) -> str:
        try:
            data = json.loads(tool_input) if tool_input.strip().startswith("{") else {}
        except (json.JSONDecodeError, AttributeError):
            return json.dumps({"error": "Invalid JSON input"})

        required: list[str] = data.get("required", [])
        available: list[str] = data.get("available", [])
        entity_name: str = data.get("entity_name", "")

        if not required:
            return json.dumps({"error": "No required documents specified"})

        # Normalise for comparison (lowercase, strip whitespace)
        def _norm(s: str) -> str:
            return s.strip().lower()

        available_norm = {_norm(a) for a in available}

        missing: list[str] = []
        present: list[str] = []

        for doc in required:
            if _norm(doc) in available_norm:
                present.append(doc)
            else:
                missing.append(doc)

        total = len(required)
        compliance_pct = round((len(present) / total) * 100, 1) if total else 0.0

        result: dict[str, Any] = {
            "entity_name": entity_name,
            "total_required": total,
            "present_count": len(present),
            "missing_count": len(missing),
            "compliance_percentage": compliance_pct,
            "compliant": len(missing) == 0,
            "missing": missing,
            "present": present,
        }

        if missing:
            result["action_required"] = True
            result["summary"] = (
                f"{entity_name + ': ' if entity_name else ''}"
                f"{len(missing)} of {total} required document(s) missing."
            )
        else:
            result["action_required"] = False
            result["summary"] = (
                f"{entity_name + ': ' if entity_name else ''}"
                f"All {total} required documents are present."
            )

        return json.dumps(result)
