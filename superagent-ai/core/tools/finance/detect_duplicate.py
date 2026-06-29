"""
Detect duplicate invoices tool — checks for duplicate invoice numbers and amounts.

Zone: GREEN — runs automatically, no human approval required.

Duplicates are detected by:
  1. Exact invoice_number match (definite duplicate)
  2. Same vendor + same amount within 7 days (probable duplicate)
  3. Same amount from same vendor (possible duplicate — lower confidence)
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_NEAR_DATE_DAYS = 7  # invoices within 7 days treated as "near-date" duplicates


class DetectDuplicateTool(BaseTool):
    """Scan a list of invoices for potential duplicates.

    Input format (JSON string)::

        {
            "invoices": [
                {
                    "id": "INV-001",
                    "invoice_number": "2024-001",
                    "vendor_name": "Acme Corp",
                    "amount": 1250.00,
                    "invoice_date": "2024-03-01"
                },
                ...
            ]
        }

    Returns:
        JSON dict with:
            ``duplicates``  : list of duplicate groups, each with:
                                ``type`` (exact_number | same_vendor_amount_date |
                                          same_vendor_amount),
                                ``confidence`` (high | medium | low),
                                ``invoice_ids`` list,
                                ``reason`` string
            ``clean_count`` : number of invoices with no duplicate found
            ``total_checked``: total invoices scanned
    """

    name: str = "detect_duplicate"
    description: str = (
        "Scans a list of invoices for duplicates by invoice number, "
        "vendor+amount+date, or vendor+amount. "
        "Input JSON: {\"invoices\": [...]}. "
        "Returns JSON with duplicates list, each having type, confidence, "
        "invoice_ids, reason."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            invoices: list[dict[str, Any]] = params.get("invoices", [])
            if not invoices:
                return json.dumps({
                    "duplicates": [],
                    "clean_count": 0,
                    "total_checked": 0,
                    "message": "No invoices provided.",
                })
            return self._detect(invoices)
        except Exception as exc:
            logger.error(f"DetectDuplicateTool error: {exc}")
            return json.dumps({"error": str(exc), "duplicates": []})

    @staticmethod
    def _parse_input(input_str: str) -> dict[str, Any]:
        if not input_str or not input_str.strip():
            return {}
        s = input_str.strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON: {exc}") from exc
        raise ValueError("DetectDuplicateTool expects a JSON string.")

    @staticmethod
    def _detect(invoices: list[dict[str, Any]]) -> str:
        duplicates: list[dict[str, Any]] = []
        flagged_ids: set[str] = set()

        # ---- Pass 1: Exact invoice_number duplicates (HIGH confidence) ----
        number_map: dict[str, list[str]] = {}
        for inv in invoices:
            num = str(inv.get("invoice_number", "")).strip()
            if not num:
                continue
            inv_id = str(inv.get("id", inv.get("invoice_number", "")))
            number_map.setdefault(num, []).append(inv_id)

        for num, ids in number_map.items():
            if len(ids) > 1:
                duplicates.append({
                    "type": "exact_number",
                    "confidence": "high",
                    "invoice_ids": ids,
                    "invoice_number": num,
                    "reason": f"Invoice number '{num}' appears {len(ids)} times.",
                })
                flagged_ids.update(ids)

        # ---- Pass 2: Same vendor + amount + near date (MEDIUM confidence) ----
        for i, inv_a in enumerate(invoices):
            id_a = str(inv_a.get("id", inv_a.get("invoice_number", f"idx_{i}")))
            if id_a in flagged_ids:
                continue
            vendor_a = str(inv_a.get("vendor_name", "")).lower().strip()
            amount_a = str(inv_a.get("amount", ""))
            date_a_str = str(inv_a.get("invoice_date", ""))

            for j, inv_b in enumerate(invoices[i + 1:], start=i + 1):
                id_b = str(inv_b.get("id", inv_b.get("invoice_number", f"idx_{j}")))
                if id_b in flagged_ids:
                    continue
                vendor_b = str(inv_b.get("vendor_name", "")).lower().strip()
                amount_b = str(inv_b.get("amount", ""))
                date_b_str = str(inv_b.get("invoice_date", ""))

                if vendor_a != vendor_b or amount_a != amount_b:
                    continue

                # Check date proximity
                try:
                    d_a = date.fromisoformat(date_a_str)
                    d_b = date.fromisoformat(date_b_str)
                    days_apart = abs((d_a - d_b).days)
                    if days_apart <= _NEAR_DATE_DAYS:
                        duplicates.append({
                            "type": "same_vendor_amount_date",
                            "confidence": "medium",
                            "invoice_ids": [id_a, id_b],
                            "reason": (
                                f"Same vendor '{inv_a.get('vendor_name')}' and amount "
                                f"{amount_a} within {days_apart} day(s)."
                            ),
                        })
                        flagged_ids.update([id_a, id_b])
                        break
                except (ValueError, TypeError):
                    # Dates unparseable — fall through to amount-only check
                    pass

                # Same vendor + same amount but dates unclear (LOW confidence)
                if id_a not in flagged_ids and id_b not in flagged_ids:
                    duplicates.append({
                        "type": "same_vendor_amount",
                        "confidence": "low",
                        "invoice_ids": [id_a, id_b],
                        "reason": (
                            f"Same vendor '{inv_a.get('vendor_name')}' and amount "
                            f"{amount_a} (dates missing or unparseable)."
                        ),
                    })
                    flagged_ids.update([id_a, id_b])
                    break

        clean_count = len(invoices) - len(flagged_ids)
        return json.dumps({
            "duplicates": duplicates,
            "clean_count": max(clean_count, 0),
            "total_checked": len(invoices),
        })
