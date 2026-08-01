"""
FindInvoiceEmailsTool — search Gmail for invoice/receipt/billing emails and
extract the key data (amount, vendor, due date) from each match.

Zone: GREEN — read-only, runs automatically.

Reads BOTH the email body text AND any PDF/DOCX/image attachments, since in
practice most real invoice emails have the actual numbers inside the
attached file, not the email body itself.

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
    """Find invoice/receipt/billing emails and extract key financial data from each,
    reading both the email body and any attached PDF/DOCX/image files.

    Input format (JSON string)::

        {"query": "invoice OR receipt (optional, has a sensible default)", "max_results": 10}

    Returns::

        {
            "count": 2,
            "invoices": [
                {
                    "subject": "...", "from": "...", "date": "...",
                    "attachments": [{"filename": "...", "file_path": "..."}],
                    "extracted": {"amount": "...", "vendor": "...", "due_date": "...", ...}
                },
                ...
            ]
        }
    """

    name: str = "find_invoice_emails"
    description: str = (
        "Search Gmail for invoice, receipt, or billing emails and extract the amount, "
        "vendor, and due date from each match — reads BOTH the email body AND any "
        "attached PDF/DOCX file, since most real invoices have the numbers inside the "
        "attachment, not the email text. Input JSON: {\"query\": \"...(optional)\", "
        "\"max_results\": 10}. If 'query' is omitted, searches for common invoice/receipt/bill "
        "keywords automatically. Returns file_path for each attachment found — pass that to "
        "summarize_financial_document or generate_invoice if needed."
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
            attachments = self._extract_attachments(payload, ref["id"])

            # Combine body text + every attachment's extracted text so the
            # extractor has the best chance of finding real numbers, whichever
            # place they actually live in.
            combined_text_parts = [body_text] if body_text else []
            for att in attachments:
                if att.get("content"):
                    combined_text_parts.append(att["content"])
            combined_text = "\n\n".join(combined_text_parts)

            try:
                extracted_raw = ExtractInvoiceDataTool().run(
                    json.dumps({"email_body": combined_text, "subject": subject})
                )
                extracted = json.loads(extracted_raw)
            except Exception:
                extracted = {}

            invoices.append({
                "subject": subject,
                "from": sender,
                "date": date,
                "attachments": [
                    {"filename": a["filename"], "file_path": a["file_path"]}
                    for a in attachments if a.get("file_path")
                ],
                "extracted": extracted,
            })

        return json.dumps({"count": len(invoices), "invoices": invoices}, ensure_ascii=False)

    def _extract_attachments(self, payload: dict, msg_id: str) -> list:
        """Download every attachment in this message and extract its text content."""
        import tempfile
        import os as _os
        import base64

        def _walk_parts(parts):
            for part in parts:
                fname = part.get("filename", "")
                body = part.get("body", {})
                att_id = body.get("attachmentId", "")
                inline = body.get("data", "")
                sub_parts = part.get("parts", [])
                if fname:
                    yield fname, att_id, inline
                if sub_parts:
                    yield from _walk_parts(sub_parts)

        att_parts = list(_walk_parts(payload.get("parts", []) or []))
        if not att_parts:
            return []

        output_dir = _os.path.join(tempfile.gettempdir(), "krypsos_docs")
        _os.makedirs(output_dir, exist_ok=True)

        from core.tools.gmail.read_attachment_content import ReadAttachmentContentTool as RAC

        results = []
        for fname, att_id, inline_data in att_parts:
            try:
                if att_id:
                    raw_att = (
                        self._service.users().messages().attachments()
                        .get(userId="me", messageId=msg_id, id=att_id)
                        .execute()
                    )
                    file_bytes = base64.urlsafe_b64decode(raw_att.get("data", "") + "==")
                elif inline_data:
                    file_bytes = base64.urlsafe_b64decode(inline_data + "==")
                else:
                    continue

                file_path = _os.path.join(output_dir, fname)
                with open(file_path, "wb") as f:
                    f.write(file_bytes)

                content_raw = RAC().run(json.dumps({"file_path": file_path, "max_chars": 20000}))
                content_data = json.loads(content_raw)
                results.append({
                    "filename": fname,
                    "file_path": file_path,
                    "content": content_data.get("content", ""),
                })
            except Exception:
                continue

        return results

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