"""
FindInvoiceEmailsTool — search Gmail for invoice/receipt/billing emails and
extract the key data (amount, vendor, due date) from each match.

Zone: GREEN — read-only, runs automatically.

Takes an already-built Gmail API service (constructed by the caller — same
convention as the other core/tools/gmail tools), so this file has no direct
Google auth logic of its own.
"""

from __future__ import annotations

import json


from core.tools.base_tool import BaseTool, ToolZone


_DEFAULT_QUERY = (
    "(invoice OR receipt OR bill OR payment OR statement) -in:spam -in:trash"
)


class FindInvoiceEmailsTool(BaseTool):
    """Find invoice/receipt/billing emails and extract key financial data from each.

    Input format (JSON string)::

        {"query": "invoice OR receipt (optional, has a sensible default)", "max_results": 10}

    Returns::

        {
            "count": 2,
            "invoices": [
                {
                    "subject": "...", "from": "...", "date": "...",
                    "extracted": {"amount": "...", "vendor": "...", "due_date": "...", ...}
                },
                ...
            ]
        }
    """

    name: str = "find_invoice_emails"
    description: str = (
        "Search Gmail for invoice, receipt, or billing emails and extract the amount, "
        "vendor, and due date from each match. Input JSON: {\"query\": \"...(optional)\", "
        "\"max_results\": 10}. If 'query' is omitted, searches for common invoice/receipt/bill "
        "keywords automatically."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service=None, workspace_id=None):
        self._service = gmail_service
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) and input_str.strip() else {}
        except (json.JSONDecodeError, TypeError):
            data = {}

        if not self._service:
            return json.dumps({
                "error": "Gmail is not connected. Please go to Integrations and connect your Gmail account first.",
                "invoices": [], "count": 0,
            })

        query = data.get("query") or _DEFAULT_QUERY
        max_results = int(data.get("max_results", 10))

        try:
            result = (
                self._service.users().messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            msg_refs = result.get("messages", [])
        except Exception as exc:
            return json.dumps({"error": f"Gmail search failed: {exc}", "invoices": [], "count": 0})

        if not msg_refs:
            return json.dumps({"invoices": [], "count": 0, "note": "No invoice/receipt emails found."})

        from core.tools.gmail.extract_invoice_data import ExtractInvoiceDataTool

        invoices = []
        for ref in msg_refs:
            try:
                msg = (
                    self._service.users().messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
            except Exception:
                continue

            payload = msg.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            subject = headers.get("subject", "(no subject)")
            sender = headers.get("from", "(unknown)")
            date = headers.get("date", "")

            body_text = self._extract_body_text(payload)

            try:
                extracted_raw = ExtractInvoiceDataTool().run(
                    json.dumps({"email_body": body_text, "subject": subject})
                )
                extracted = json.loads(extracted_raw)
            except Exception:
                extracted = {}

            invoices.append({
                "subject": subject,
                "from": sender,
                "date": date,
                "extracted": extracted,
            })

        return json.dumps({"count": len(invoices), "invoices": invoices}, ensure_ascii=False)

    @staticmethod
    def _extract_body_text(payload: dict) -> str:
        """Pull plain-text body content out of a Gmail message payload."""
        import base64

        def _decode(data: str) -> str:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            except Exception:
                return ""

        body = payload.get("body", {})
        if body.get("data"):
            return _decode(body["data"])

        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return _decode(part["body"]["data"])
        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                return _decode(part["body"]["data"])
        return ""

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
            },
        }}