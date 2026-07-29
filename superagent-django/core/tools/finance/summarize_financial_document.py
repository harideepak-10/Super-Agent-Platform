"""
SummarizeFinancialDocumentTool — summarize a financial statement / report file.

Zone: GREEN — runs automatically, no human approval required.

Wraps the existing summarize_document tool with a finance-specific prompt so
the summary focuses on income, expenses, balances, and notable line items
instead of generic document summarization.
"""

from __future__ import annotations

import json


from core.tools.base_tool import BaseTool, ToolZone


class SummarizeFinancialDocumentTool(BaseTool):
    """Summarize a PDF/DOCX financial statement into a finance-focused summary.

    Input format (JSON string)::

        {"file_path": "/tmp/bank_statement.pdf", "max_points": 10}

    Returns the same shape as summarize_document, but the underlying prompt
    is steered toward financial content (totals, balances, unusual charges).
    """

    name: str = "summarize_financial_document"
    description: str = (
        "Summarize a financial document (bank statement, invoice, financial report — "
        "PDF or DOCX) with a focus on income, expenses, balances, and notable line items. "
        "Input JSON: {\"file_path\": \"/tmp/statement.pdf\", \"max_points\": 10}."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        file_path = data.get("file_path", "")
        if not file_path:
            return json.dumps({"error": "'file_path' is required."})

        max_points = data.get("max_points", 10)
        focus = (
            "Focus specifically on: total income, total expenses, closing balance, "
            "any unusually large transactions, recurring charges/subscriptions, "
            "and due dates or overdue amounts if this is an invoice or bill."
        )

        payload = json.dumps({"file_path": file_path, "max_points": max_points, "focus": focus})

        try:
            from core.tools.document.summarize_document import SummarizeDocumentTool as CoreTool
            raw = CoreTool().run(payload)
        except Exception as exc:
            return json.dumps({"error": f"Failed to summarize financial document: {exc}"})

        return raw

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "max_points": {"type": "integer"},
                },
                "required": ["file_path"],
            },
        }}