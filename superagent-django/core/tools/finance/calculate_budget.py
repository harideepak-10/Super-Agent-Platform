"""
CalculateBudgetTool — budget summaries, savings projections, and simple tax estimates.

Zone: GREEN — runs automatically, no human approval required.

Tax estimates here are simple flat/slab arithmetic on numbers the user supplies —
NOT tax advice and NOT built-in knowledge of any country's actual tax code.
Always tell the user to verify with a real tax professional for filing purposes.
"""

from __future__ import annotations

import json


from core.tools.base_tool import BaseTool, ToolZone


class CalculateBudgetTool(BaseTool):
    """Budget summary, savings projection, or simple tax estimate calculator.

    Input format (JSON string)::

        # mode: "budget"
        {
            "mode": "budget",
            "income": 80000,
            "expenses": {"rent": 25000, "food": 10000, "transport": 5000}
        }

        # mode: "savings_projection"
        {
            "mode": "savings_projection",
            "monthly_saving": 10000,
            "months": 12,
            "annual_interest_rate_pct": 6
        }

        # mode: "tax_estimate"  (simple slab-based estimate, NOT tax advice)
        {
            "mode": "tax_estimate",
            "annual_income": 1200000,
            "slabs": [
                {"up_to": 300000, "rate_pct": 0},
                {"up_to": 700000, "rate_pct": 5},
                {"up_to": 1000000, "rate_pct": 10},
                {"up_to": null, "rate_pct": 20}
            ]
        }
    """

    name: str = "calculate_budget"
    description: str = (
        "Calculate a budget summary, a savings projection, or a simple slab-based tax "
        "estimate. Input JSON with \"mode\": \"budget\" | \"savings_projection\" | \"tax_estimate\". "
        "For budget: {\"mode\":\"budget\",\"income\":80000,\"expenses\":{\"rent\":25000,...}}. "
        "For savings_projection: {\"mode\":\"savings_projection\",\"monthly_saving\":10000,"
        "\"months\":12,\"annual_interest_rate_pct\":6}. "
        "For tax_estimate: {\"mode\":\"tax_estimate\",\"annual_income\":1200000,\"slabs\":[...]} "
        "— this is a simple arithmetic estimate on user-supplied slabs, NOT real tax advice; "
        "always tell the user to confirm with a tax professional."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        mode = data.get("mode", "budget")

        if mode == "budget":
            return self._budget(data)
        if mode == "savings_projection":
            return self._savings_projection(data)
        if mode == "tax_estimate":
            return self._tax_estimate(data)
        return json.dumps({"error": f"Unknown mode '{mode}'. Use budget, savings_projection, or tax_estimate."})

    @staticmethod
    def _budget(data: dict) -> str:
        try:
            income = float(data.get("income", 0))
        except (TypeError, ValueError):
            return json.dumps({"error": "'income' must be a number."})
        expenses = data.get("expenses", {})
        if not isinstance(expenses, dict):
            return json.dumps({"error": "'expenses' must be an object of {category: amount}."})

        total_expenses = 0.0
        clean_expenses = {}
        for k, v in expenses.items():
            try:
                amt = float(v)
            except (TypeError, ValueError):
                continue
            clean_expenses[k] = round(amt, 2)
            total_expenses += amt

        remaining = income - total_expenses
        savings_rate_pct = round((remaining / income) * 100, 1) if income else 0

        return json.dumps({
            "income": round(income, 2),
            "expenses": clean_expenses,
            "total_expenses": round(total_expenses, 2),
            "remaining": round(remaining, 2),
            "savings_rate_pct": savings_rate_pct,
            "over_budget": remaining < 0,
        }, ensure_ascii=False)

    @staticmethod
    def _savings_projection(data: dict) -> str:
        try:
            monthly = float(data.get("monthly_saving", 0))
            months = int(data.get("months", 12))
            annual_rate_pct = float(data.get("annual_interest_rate_pct", 0))
        except (TypeError, ValueError):
            return json.dumps({"error": "monthly_saving, months, and annual_interest_rate_pct must be numbers."})

        monthly_rate = (annual_rate_pct / 100) / 12
        balance = 0.0
        timeline = []
        for m in range(1, months + 1):
            balance = balance * (1 + monthly_rate) + monthly
            timeline.append({"month": m, "balance": round(balance, 2)})

        total_contributed = monthly * months
        interest_earned = balance - total_contributed

        return json.dumps({
            "months": months,
            "monthly_saving": round(monthly, 2),
            "annual_interest_rate_pct": annual_rate_pct,
            "final_balance": round(balance, 2),
            "total_contributed": round(total_contributed, 2),
            "interest_earned": round(interest_earned, 2),
            "timeline": timeline,
        }, ensure_ascii=False)

    @staticmethod
    def _tax_estimate(data: dict) -> str:
        try:
            income = float(data.get("annual_income", 0))
        except (TypeError, ValueError):
            return json.dumps({"error": "'annual_income' must be a number."})

        slabs = data.get("slabs", [])
        if not isinstance(slabs, list) or not slabs:
            return json.dumps({
                "error": (
                    "'slabs' is required — a list of {\"up_to\": <amount or null>, \"rate_pct\": <number>}. "
                    "This tool does not know real tax law; the user must supply the applicable slabs."
                )
            })

        tax = 0.0
        prev_cap = 0.0
        breakdown = []
        for slab in slabs:
            up_to = slab.get("up_to")
            rate_pct = float(slab.get("rate_pct", 0))
            cap = float(up_to) if up_to is not None else income
            taxable_in_slab = max(0.0, min(income, cap) - prev_cap)
            slab_tax = taxable_in_slab * (rate_pct / 100)
            tax += slab_tax
            breakdown.append({
                "range": f"{prev_cap:.0f}–{cap:.0f}" if up_to is not None else f"{prev_cap:.0f}+",
                "rate_pct": rate_pct,
                "taxable_amount": round(taxable_in_slab, 2),
                "tax": round(slab_tax, 2),
            })
            prev_cap = cap
            if income <= cap:
                break

        return json.dumps({
            "annual_income": round(income, 2),
            "estimated_tax": round(tax, 2),
            "effective_rate_pct": round((tax / income) * 100, 2) if income else 0,
            "net_income": round(income - tax, 2),
            "breakdown": breakdown,
            "disclaimer": (
                "This is a simple arithmetic estimate based on the slabs provided — "
                "not tax advice. Confirm with a tax professional before filing."
            ),
        }, ensure_ascii=False)

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["budget", "savings_projection", "tax_estimate"]},
                    "income": {"type": "number", "description": "For mode=budget"},
                    "expenses": {"type": "object", "description": "For mode=budget: {category: amount}"},
                    "monthly_saving": {"type": "number", "description": "For mode=savings_projection"},
                    "months": {"type": "integer", "description": "For mode=savings_projection"},
                    "annual_interest_rate_pct": {"type": "number", "description": "For mode=savings_projection"},
                    "annual_income": {"type": "number", "description": "For mode=tax_estimate"},
                    "slabs": {"type": "array", "items": {"type": "object"}, "description": "For mode=tax_estimate"},
                },
                "required": ["mode"],
            },
        }}