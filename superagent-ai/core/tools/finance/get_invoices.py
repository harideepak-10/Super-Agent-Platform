"""
Get invoices tool — retrieves invoices from the data store.

Zone: GREEN — runs automatically, no human approval required.

In production the data store is a database (injected via ``invoice_store``).
In tests a MockInvoiceStore is injected — no real DB connection is made.

Each invoice dict has:
    id, invoice_number, vendor_name, vendor_email,
    amount, currency, invoice_date, due_date,
    status (pending | paid | overdue | flagged),
    line_items []
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Protocol

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class InvoiceStore(Protocol):
    """Minimal interface that any invoice data source must satisfy."""
    def all(self) -> list[dict[str, Any]]: ...
    def filter(self, **kwargs: Any) -> list[dict[str, Any]]: ...
    def get(self, invoice_id: str) -> dict[str, Any] | None: ...


class GetInvoicesTool(BaseTool):
    """Retrieve invoices from the data store with optional filters.

    Input format (JSON string or empty)::

        {}                              → returns all invoices
        {"status": "pending"}           → filter by status
        {"vendor": "Acme"}              → filter by vendor name (partial)
        {"overdue": true}               → only overdue invoices
        {"invoice_id": "INV-001"}       → single invoice by ID

    Returns:
        JSON list of invoice dicts, or ``{"error": "...", "invoices": []}``.
    """

    name: str = "get_invoices"
    description: str = (
        "Retrieves invoices from the data store. "
        "Input JSON: {\"status\": \"pending\"} or {\"overdue\": true} or "
        "{\"vendor\": \"Acme\"} or {} for all. "
        "Returns a JSON list of invoice dicts."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, invoice_store: Any = None) -> None:
        self._store = invoice_store

    def run(self, input_str: str) -> str:
        filters = self._parse_input(input_str)
        if self._store is None:
            return json.dumps({"error": "No invoice store configured.", "invoices": []})
        try:
            invoices = self._fetch(filters)
            return json.dumps(invoices, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.error(f"GetInvoicesTool error: {exc}")
            return json.dumps({"error": str(exc), "invoices": []})

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
        return {}

    def _fetch(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        # Single invoice by ID
        if "invoice_id" in filters:
            item = self._store.get(filters["invoice_id"])
            return [item] if item else []

        invoices = self._store.all()

        if "status" in filters:
            invoices = [i for i in invoices if i.get("status") == filters["status"]]

        if "vendor" in filters:
            q = filters["vendor"].lower()
            invoices = [i for i in invoices if q in i.get("vendor_name", "").lower()]

        if filters.get("overdue"):
            today = date.today().isoformat()
            invoices = [
                i for i in invoices
                if i.get("due_date", "9999-12-31") < today
                and i.get("status") != "paid"
            ]

        return invoices
