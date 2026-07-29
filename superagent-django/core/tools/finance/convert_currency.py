"""
ConvertCurrencyTool — live currency conversion.

Zone: GREEN — runs automatically, no human approval required.

Uses the free, no-API-key exchange rate API at https://api.frankfurter.app
"""

from __future__ import annotations

import json
import logging

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class ConvertCurrencyTool(BaseTool):
    """Convert an amount from one currency to another using live exchange rates.

    Input format (JSON string)::

        {"amount": 100, "from": "USD", "to": "INR"}

    Returns::

        {"amount": 100, "from": "USD", "to": "INR", "rate": 83.12, "converted": 8312.0, "date": "2026-07-28"}
    """

    name: str = "convert_currency"
    description: str = (
        "Convert an amount from one currency to another using live exchange rates. "
        "Input JSON: {\"amount\": 100, \"from\": \"USD\", \"to\": \"INR\"}. "
        "Use standard 3-letter currency codes (USD, EUR, INR, GBP, JPY, etc)."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        try:
            amount = float(data.get("amount", 0))
        except (TypeError, ValueError):
            return json.dumps({"error": "'amount' must be a number."})

        from_currency = str(data.get("from", "")).upper().strip()
        to_currency = str(data.get("to", "")).upper().strip()

        if not from_currency or not to_currency:
            return json.dumps({"error": "'from' and 'to' currency codes are required."})

        try:
            import requests
            resp = requests.get(
                "https://api.frankfurter.app/latest",
                params={"amount": amount, "from": from_currency, "to": to_currency},
                timeout=10,
            )
            resp.raise_for_status()
            data_resp = resp.json()
            rates = data_resp.get("rates", {})
            if to_currency not in rates:
                return json.dumps({"error": f"Could not get a rate for '{to_currency}'. Check the currency code."})
            converted = rates[to_currency]
            rate = round(converted / amount, 6) if amount else 0
            return json.dumps({
                "amount": amount,
                "from": from_currency,
                "to": to_currency,
                "rate": rate,
                "converted": round(converted, 2),
                "date": data_resp.get("date", ""),
            }, ensure_ascii=False)
        except Exception as exc:
            logger.warning("ConvertCurrencyTool failed: %s", exc)
            return json.dumps({"error": f"Currency conversion failed: {exc}"})

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "from": {"type": "string", "description": "3-letter currency code, e.g. USD"},
                    "to": {"type": "string", "description": "3-letter currency code, e.g. INR"},
                },
                "required": ["amount", "from", "to"],
            },
        }}