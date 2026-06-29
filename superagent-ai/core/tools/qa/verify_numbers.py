"""
Verify numbers tool — extracts and cross-checks numeric values in text.

Zone: GREEN — runs automatically, no human approval required.

Finds all currency amounts and numbers in the draft text,
then checks them against a provided reference dict for accuracy.
Flags any discrepancy between stated and expected values.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_TWO = Decimal("0.01")
_AMOUNT_PATTERN = re.compile(
    r"(?:USD|EUR|GBP|INR|AED)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "")).quantize(_TWO, ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"Cannot parse as number: {value!r}")


class VerifyNumbersTool(BaseTool):
    """Extract numbers from text and verify them against expected values.

    Input format (JSON string)::

        {
            "text":     "Total invoices: 12. Grand total: USD 45,200.00.",
            "expected": {
                "total_invoices": 12,
                "grand_total": 45200.00
            },
            "tolerance": 0.01    // optional, default 0.01 (1 cent)
        }

    Returns:
        JSON dict with:
            ``passed``      : bool  — True if all checks pass
            ``checks``      : list of check result dicts
            ``issues``      : list of failing check descriptions
            ``numbers_found``: list of numbers extracted from text
    """

    name: str = "verify_numbers"
    description: str = (
        "Extracts numbers from text and verifies them against expected values. "
        "Input JSON: {\"text\": \"...\", \"expected\": {\"key\": value, ...}, "
        "\"tolerance\": 0.01}. "
        "Returns JSON with passed, checks, issues, numbers_found."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            text: str = params.get("text", "")
            expected: dict[str, Any] = params.get("expected", {})
            tolerance = _to_decimal(params.get("tolerance", "0.01"))

            numbers_found = self._extract_numbers(text)
            checks: list[dict[str, Any]] = []
            issues: list[str] = []

            for key, exp_val in expected.items():
                try:
                    exp_d = _to_decimal(exp_val)
                except ValueError:
                    issues.append(f"Cannot parse expected value for '{key}': {exp_val!r}")
                    continue

                # Search for this value in the text
                found_match = False
                for num in numbers_found:
                    try:
                        found_d = _to_decimal(num)
                        if abs(found_d - exp_d) <= tolerance:
                            found_match = True
                            checks.append({
                                "key": key,
                                "expected": str(exp_d),
                                "found": str(found_d),
                                "passed": True,
                            })
                            break
                    except ValueError:
                        continue

                if not found_match:
                    checks.append({
                        "key": key,
                        "expected": str(exp_d),
                        "found": None,
                        "passed": False,
                    })
                    issues.append(
                        f"Expected {key}={exp_d} not found in text "
                        f"(within tolerance {tolerance})."
                    )

            return json.dumps({
                "passed": len(issues) == 0,
                "checks": checks,
                "issues": issues,
                "numbers_found": numbers_found,
            })
        except Exception as exc:
            logger.error(f"VerifyNumbersTool error: {exc}")
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
        return {"text": s}

    @staticmethod
    def _extract_numbers(text: str) -> list[str]:
        """Return all numeric strings found in text (deduplicated, ordered)."""
        matches = _AMOUNT_PATTERN.findall(text)
        seen: set[str] = set()
        result: list[str] = []
        for m in matches:
            clean = m.replace(",", "")
            if clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result
