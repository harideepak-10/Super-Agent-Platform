"""
ExtractInvoiceDataTool — extract structured invoice data from an email.

Zone: GREEN — runs automatically, no human approval required.

Uses regex + keyword matching to pull: vendor, amount, due date,
invoice number, and payment status from email body text.
"""
from __future__ import annotations
import json
import logging
import re
from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

# Currency patterns
_AMOUNT_PATTERN = re.compile(
    r'(?:₹|Rs\.?|INR|USD|\$|€|£|GBP|EUR)\s*([\d,]+(?:\.\d{1,2})?)'
    r'|'
    r'([\d,]+(?:\.\d{1,2})?)\s*(?:₹|Rs\.?|INR|USD|\$|€|£|GBP|EUR)',
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-]\d{2}[\/\-]\d{2}|'
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4})\b',
    re.IGNORECASE,
)
_INVOICE_NUM_PATTERN = re.compile(
    r'(?:invoice|inv|bill|receipt|order)\s*[#\-:]?\s*([A-Z0-9\-]+)',
    re.IGNORECASE,
)
_DUE_KEYWORDS = ["due date", "due by", "pay by", "payment due", "due on"]
_OVERDUE_KEYWORDS = ["overdue", "past due", "outstanding", "unpaid"]
_PAID_KEYWORDS = ["paid", "payment received", "payment confirmed", "receipt"]


class ExtractInvoiceDataTool(BaseTool):
    """Extract structured invoice data from email body text.

    Input::

        {
            "email_body":    "Dear Customer, Invoice #1042 for ₹45,000 is due by July 15...",
            "email_subject": "Invoice #1042 - Payment Due"    (optional, helps extraction)
        }

    Returns::

        {
            "invoice_number": "1042",
            "amount":         "₹45,000",
            "amount_numeric": 45000.0,
            "currency":       "INR",
            "due_date":       "July 15, 2026",
            "vendor":         "",
            "payment_status": "unpaid",    # unpaid | paid | overdue
            "raw_dates":      ["July 15, 2026"],
            "confidence":     "high"       # high | medium | low
        }
    """

    name: str = "extract_invoice_data"
    description: str = (
        "Extract structured invoice data from an email body. "
        "Input JSON: {\"email_body\": \"...\", \"email_subject\": \"...(optional)\"}. "
        "Returns invoice_number, amount, due_date, payment_status (unpaid/paid/overdue). "
        "Use this on emails with has_attachments or invoice-related subjects."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input."})

        body    = data.get("email_body", data.get("body", ""))
        subject = data.get("email_subject", data.get("subject", ""))
        text    = f"{subject}\n{body}"

        if not text.strip():
            return json.dumps({"error": "'email_body' is required."})

        invoice_number = self._extract_invoice_number(text)
        amount, amount_numeric, currency = self._extract_amount(text)
        dates     = self._extract_dates(text)
        due_date  = self._find_due_date(text, dates)
        status    = self._detect_status(text)
        vendor    = self._extract_vendor(body)
        confidence = "high" if (invoice_number and amount) else ("medium" if amount else "low")

        return json.dumps({
            "invoice_number": invoice_number,
            "amount":         amount,
            "amount_numeric": amount_numeric,
            "currency":       currency,
            "due_date":       due_date,
            "vendor":         vendor,
            "payment_status": status,
            "raw_dates":      dates,
            "confidence":     confidence,
        }, ensure_ascii=False)

    @staticmethod
    def _extract_invoice_number(text: str) -> str:
        m = _INVOICE_NUM_PATTERN.search(text)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_amount(text: str):
        _CURRENCY_MAP = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD",
                         "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}
        m = _AMOUNT_PATTERN.search(text)
        if not m:
            return "", None, ""
        raw = m.group(0).strip()
        num_str = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            amount_numeric = float(num_str)
        except ValueError:
            amount_numeric = None
        # Detect currency symbol
        for sym, code in _CURRENCY_MAP.items():
            if sym in raw.lower() or sym in text[:50].lower():
                return raw, amount_numeric, code
        return raw, amount_numeric, ""

    @staticmethod
    def _extract_dates(text: str) -> list:
        return list(dict.fromkeys(_DATE_PATTERN.findall(text)))  # unique, order preserved

    @staticmethod
    def _find_due_date(text: str, dates: list) -> str:
        text_lower = text.lower()
        for kw in _DUE_KEYWORDS:
            idx = text_lower.find(kw)
            if idx >= 0:
                nearby = text[idx:idx+60]
                m = _DATE_PATTERN.search(nearby)
                if m:
                    return m.group(0)
        return dates[0] if dates else ""

    @staticmethod
    def _detect_status(text: str) -> str:
        t = text.lower()
        if any(kw in t for kw in _PAID_KEYWORDS):
            return "paid"
        if any(kw in t for kw in _OVERDUE_KEYWORDS):
            return "overdue"
        return "unpaid"

    @staticmethod
    def _extract_vendor(body: str) -> str:
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        for line in lines[:5]:
            if re.search(r'\b(?:from|regards|sincerely|best,|thanks,|—)\b', line, re.IGNORECASE):
                return re.sub(r'\b(?:from|regards|sincerely|best|thanks|—):?\s*', '', line, flags=re.IGNORECASE).strip()
        return ""

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "email_body":    {"type": "string"},
                    "email_subject": {"type": "string"},
                },
                "required": ["email_body"],
            },
        }}
