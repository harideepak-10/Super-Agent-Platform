"""
CategorizeExpensesTool — categorize and summarize a list of expenses.

Zone: GREEN — runs automatically, no human approval required.

Note: this does NOT persist expenses across separate tasks/conversations.
Each call works only on the expenses passed in this request. Persistent
expense tracking across time would require a database table.
"""

from __future__ import annotations

import json
from collections import defaultdict

from core.tools.base_tool import BaseTool, ToolZone


_DEFAULT_CATEGORY_KEYWORDS = {
    "food": ["restaurant", "swiggy", "zomato", "food", "grocery", "cafe", "coffee", "lunch", "dinner"],
    "transport": ["uber", "ola", "fuel", "petrol", "diesel", "taxi", "cab", "metro", "bus", "flight", "train"],
    "housing": ["rent", "emi", "mortgage", "maintenance", "electricity", "water bill", "gas bill"],
    "shopping": ["amazon", "flipkart", "myntra", "shopping", "clothes", "electronics"],
    "entertainment": ["netflix", "spotify", "movie", "prime video", "hotstar", "game", "concert"],
    "health": ["pharmacy", "hospital", "doctor", "medicine", "clinic", "insurance"],
    "subscriptions": ["subscription", "saas", "software", "membership"],
    "utilities": ["internet", "wifi", "phone bill", "recharge"],
    "other": [],
}


class CategorizeExpensesTool(BaseTool):
    """Categorize and summarize a list of expenses.

    Input format (JSON string)::

        {
            "expenses": [
                {"description": "Swiggy order", "amount": 450, "date": "2026-07-20"},
                {"description": "Uber to office", "amount": 180, "date": "2026-07-21"}
            ],
            "currency": "INR"  # optional, defaults to INR
        }

    Returns::

        {
            "total": 630,
            "currency": "INR",
            "by_category": {"food": 450, "transport": 180},
            "breakdown": [
                {"description": "...", "amount": ..., "category": "food", "date": "..."},
                ...
            ],
            "top_category": "food"
        }
    """

    name: str = "categorize_expenses"
    description: str = (
        "Categorize and summarize a list of expenses into spending categories "
        "(food, transport, housing, shopping, entertainment, health, subscriptions, "
        "utilities, other). Input JSON: {\"expenses\": [{\"description\": \"...\", "
        "\"amount\": 450, \"date\": \"...(optional)\"}], \"currency\": \"INR (optional)\"}. "
        "Does NOT persist expenses across separate tasks — only summarizes what's passed in."
    )
    zone: ToolZone = ToolZone.GREEN

    @staticmethod
    def _categorize(description: str) -> str:
        text = (description or "").lower()
        for category, keywords in _DEFAULT_CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return category
        return "other"

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        expenses = data.get("expenses", [])
        currency = data.get("currency", "INR")

        if not isinstance(expenses, list) or not expenses:
            return json.dumps({"error": "'expenses' must be a non-empty list."})

        by_category: dict = defaultdict(float)
        breakdown = []
        total = 0.0

        for exp in expenses:
            if not isinstance(exp, dict):
                continue
            try:
                amount = float(exp.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0.0
            description = exp.get("description", "")
            category = exp.get("category") or self._categorize(description)

            by_category[category] += amount
            total += amount
            breakdown.append({
                "description": description,
                "amount": amount,
                "category": category,
                "date": exp.get("date", ""),
            })

        top_category = max(by_category, key=by_category.get) if by_category else ""

        result = {
            "total": round(total, 2),
            "currency": currency,
            "by_category": {k: round(v, 2) for k, v in by_category.items()},
            "breakdown": breakdown,
            "top_category": top_category,
            "count": len(breakdown),
        }
        return json.dumps(result, ensure_ascii=False)

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "expenses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "date": {"type": "string"},
                                "category": {"type": "string"},
                            },
                            "required": ["description", "amount"],
                        },
                    },
                    "currency": {"type": "string", "description": "Currency code, default INR"},
                },
                "required": ["expenses"],
            },
        }}