"""
Review draft tool — QA Agent reviews agent output for completeness and clarity.

Zone: GREEN — runs automatically, no human approval required.

Performs rule-based checks on draft text:
  - Required sections present
  - No placeholder text left (e.g. [TODO], [INSERT])
  - Minimum length check
  - No obvious formatting errors
Returns a structured review result with pass/fail and issues found.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATTERNS = [
    r"\[TODO\]", r"\[INSERT\]", r"\[TBD\]", r"\[PLACEHOLDER\]",
    r"\[FILL IN\]", r"\[ADD HERE\]", r"<TODO>", r"<INSERT>",
    r"Lorem ipsum",
]

_MIN_DRAFT_LENGTH = 20  # characters


class ReviewDraftTool(BaseTool):
    """Review a draft for completeness, placeholders, and basic quality.

    Input format (JSON string)::

        {
            "draft":             "Full text of the draft to review",
            "required_sections": ["Summary", "Totals", "Recommendations"],  // optional
            "min_length":        100   // optional, default 20
        }

    Returns:
        JSON dict with:
            ``passed``          : bool
            ``issues``          : list of issue strings
            ``warnings``        : list of warning strings
            ``word_count``      : int
            ``char_count``      : int
            ``placeholders_found``: list of found placeholder strings
    """

    name: str = "review_draft"
    description: str = (
        "Reviews a draft text for completeness, placeholder text, and basic quality. "
        "Input JSON: {\"draft\": \"...\", \"required_sections\": [...], "
        "\"min_length\": 100}. "
        "Returns JSON with passed, issues, warnings, word_count."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            draft: str = params.get("draft", "")
            required_sections: list[str] = params.get("required_sections", [])
            min_length: int = int(params.get("min_length", _MIN_DRAFT_LENGTH))

            issues: list[str] = []
            warnings: list[str] = []

            # 1. Length check
            if len(draft) < min_length:
                issues.append(
                    f"Draft is too short: {len(draft)} chars (minimum: {min_length})."
                )

            # 2. Placeholder check
            placeholders_found: list[str] = []
            for pattern in _PLACEHOLDER_PATTERNS:
                if re.search(pattern, draft, re.IGNORECASE):
                    placeholders_found.append(pattern.strip(r"\\[]<>"))
                    issues.append(f"Placeholder text found: '{pattern}'")

            # 3. Required sections
            for section in required_sections:
                if section.lower() not in draft.lower():
                    issues.append(f"Required section missing: '{section}'")

            # 4. Warnings (non-blocking)
            if draft.isupper():
                warnings.append("Draft is ALL CAPS — check formatting.")
            if len(draft.split()) < 5:
                warnings.append("Draft is very short — may be incomplete.")

            word_count = len(draft.split())
            char_count = len(draft)

            return json.dumps({
                "passed": len(issues) == 0,
                "issues": issues,
                "warnings": warnings,
                "word_count": word_count,
                "char_count": char_count,
                "placeholders_found": placeholders_found,
            })
        except Exception as exc:
            logger.error(f"ReviewDraftTool error: {exc}")
            return json.dumps({"error": str(exc), "passed": False})

    @staticmethod
    def _parse_input(input_str: str) -> dict[str, Any]:
        if not input_str or not input_str.strip():
            return {}
        s = input_str.strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                pass
        # Plain string treated as the draft text
        return {"draft": s}
