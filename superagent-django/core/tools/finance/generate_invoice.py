"""
GenerateInvoiceTool — build a real PDF or Word invoice file.

Zone: GREEN — runs automatically, no human approval required.
(Actually sending the invoice by email is a separate step via send_email — YELLOW.)

Reuses the existing create_pdf / create_docx tools so invoices look consistent
with every other document the platform produces.
"""

from __future__ import annotations

import json


from core.tools.base_tool import BaseTool, ToolZone


class GenerateInvoiceTool(BaseTool):
    """Generate a PDF or Word invoice from structured line items.

    Input format (JSON string)::

        {
            "invoice_number": "INV-2026-014",
            "from_name": "KRYPSOS Technologies",
            "to_name": "Client Name",
            "to_email": "client@example.com",
            "date": "2026-07-28",
            "due_date": "2026-08-11",
            "currency": "INR",
            "items": [
                {"description": "Consulting — July", "quantity": 1, "unit_price": 50000}
            ],
            "tax_pct": 18,
            "notes": "Payment due within 14 days.",
            "output_format": "pdf"   # or "docx", default pdf
        }

    Returns file_path/filename, same shape as create_pdf/create_docx.
    """

    name: str = "generate_invoice"
    description: str = (
        "Generate a real PDF or Word invoice file from line items and automatically "
        "save it. Input JSON: {\"invoice_number\":\"...\", \"from_name\":\"...\", "
        "\"to_name\":\"...\", \"to_email\":\"...(optional)\", \"date\":\"...\", "
        "\"due_date\":\"...(optional)\", \"currency\":\"INR\", "
        "\"items\":[{\"description\":\"...\",\"quantity\":1,\"unit_price\":1000}], "
        "\"tax_pct\":18(optional), \"notes\":\"...(optional)\", "
        "\"output_format\":\"pdf|docx\"}. "
        "Returns file_path — pass it to upload_to_drive or attach when sending the email."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        invoice_number = data.get("invoice_number", "INV-0001")
        from_name = data.get("from_name", "")
        to_name = data.get("to_name", "")
        to_email = data.get("to_email", "")
        date = data.get("date", "")
        due_date = data.get("due_date", "")
        currency = data.get("currency", "INR")
        items = data.get("items", [])
        tax_pct = float(data.get("tax_pct", 0) or 0)
        notes = data.get("notes", "")
        output_format = (data.get("output_format") or "pdf").lower()
        if output_format not in ("pdf", "docx"):
            output_format = "pdf"

        if not items or not isinstance(items, list):
            return json.dumps({"error": "'items' is required — a list of {description, quantity, unit_price}."})

        subtotal = 0.0
        line_lines = []
        for item in items:
            try:
                qty = float(item.get("quantity", 1))
                unit_price = float(item.get("unit_price", 0))
            except (TypeError, ValueError):
                continue
            line_total = qty * unit_price
            subtotal += line_total
            line_lines.append(
                f"{item.get('description', 'Item')} — Qty: {qty:g} × {currency} {unit_price:,.2f} "
                f"= {currency} {line_total:,.2f}"
            )

        tax_amount = subtotal * (tax_pct / 100)
        total = subtotal + tax_amount

        sections = [
            {
                "heading": "Invoice Details",
                "content": (
                    f"Invoice Number: {invoice_number}\n"
                    f"Date: {date}\n"
                    f"Due Date: {due_date or 'On receipt'}\n"
                    f"From: {from_name}\n"
                    f"Bill To: {to_name}" + (f" ({to_email})" if to_email else "")
                ),
            },
            {
                "heading": "Line Items",
                "content": "\n".join(line_lines) if line_lines else "No valid items.",
            },
            {
                "heading": "Summary",
                "content": (
                    f"Subtotal: {currency} {subtotal:,.2f}\n"
                    + (f"Tax ({tax_pct:g}%): {currency} {tax_amount:,.2f}\n" if tax_pct else "")
                    + f"Total Due: {currency} {total:,.2f}"
                ),
            },
        ]
        if notes:
            sections.append({"heading": "Notes", "content": notes})

        title = f"Invoice {invoice_number}"
        payload = json.dumps({"title": title, "sections": sections, "author": from_name or "KRYPSOS"})

        try:
            if output_format == "docx":
                from core.tools.document.create_docx import CreateDocxTool
                raw = CreateDocxTool().run(payload)
            else:
                from core.tools.document.create_pdf import CreatePdfTool
                raw = CreatePdfTool().run(payload)
            result = json.loads(raw)
        except Exception as exc:
            return json.dumps({"error": f"Failed to create invoice file: {exc}"})

        if result.get("status") != "created":
            return json.dumps({"error": "Invoice file could not be created.", "details": result})

        return json.dumps({
            "status": "created",
            "invoice_number": invoice_number,
            "subtotal": round(subtotal, 2),
            "tax_amount": round(tax_amount, 2),
            "total": round(total, 2),
            "currency": currency,
            "file_path": result["file_path"],
            "filename": result["filename"],
            "format": output_format,
            "note": (
                f"Invoice created at {result['file_path']}. "
                "Call upload_to_drive to save it, or pass the file_path when sending it by email."
            ),
        }, ensure_ascii=False)

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "invoice_number": {"type": "string"},
                    "from_name": {"type": "string"},
                    "to_name": {"type": "string"},
                    "to_email": {"type": "string"},
                    "date": {"type": "string"},
                    "due_date": {"type": "string"},
                    "currency": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit_price": {"type": "number"},
                            },
                            "required": ["description", "unit_price"],
                        },
                    },
                    "tax_pct": {"type": "number"},
                    "notes": {"type": "string"},
                    "output_format": {"type": "string", "enum": ["pdf", "docx"]},
                },
                "required": ["invoice_number", "to_name", "items"],
            },
        }}