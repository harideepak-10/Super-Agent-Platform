"""
Calculate total tool — pure Python arithmetic for invoice verification.

Zone: GREEN — runs automatically, no external API, deterministic.

Supports:
  - Summing line items to verify invoice totals
  - Adding tax/discount to a subtotal
  - Summing multiple invoice amounts
  - Detecting discrepancies between stated total and computed total

All arithmetic uses Python's Decimal for financial precision.
"""

from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")


def _d(value: Any) -> Decimal:
    """Convert a value to Decimal, raising ValueError if not numeric."""
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Cannot convert {value!r} to a number.") from exc


class CalculateTotalTool(BaseTool):
    """Verify and compute invoice totals with Decimal precision.

    Input format (JSON string) — three modes:

    1. Sum line items::

        {
            "mode": "sum_lines",
            "line_items": [
                {"description": "...", "quantity": 2, "unit_price": 50.00},
                ...
            ],
            "tax_rate": 0.10,        // optional, e.g. 10%
            "discount": 5.00,        // optional flat discount
            "stated_total": 105.00   // optional — checked against computed
        }

    2. Sum invoice amounts::

        {
            "mode": "sum_invoices",
            "amounts": [100.00, 250.50, 75.00]
        }

    3. Verify a single total::

        {
            "mode": "verify",
            "subtotal": 100.00,
            "tax_rate": 0.10,
            "discount": 0.00,
            "stated_total": 110.00
        }

    Returns:
        JSON dict with ``computed_total``, ``stated_total`` (if given),
        ``match`` (bool), ``discrepancy`` (if any), and breakdown fields.
    """

    name: str = "calculate_total"
    description: str = (
        "Computes and verifies invoice totals using Decimal arithmetic. "
        "Modes: sum_lines (from line items), sum_invoices (list of amounts), "
        "verify (check stated vs computed). "
        "Input JSON: {\"mode\": \"sum_lines\", \"line_items\": [...], "
        "\"tax_rate\": 0.10, \"stated_total\": 110.00}. "
        "Returns computed_total, match, discrepancy."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            params = self._parse_input(input_str)
            mode = params.get("mode", "sum_lines")

            if mode == "sum_lines":
                return self._sum_lines(params)
            elif mode == "sum_invoices":
                return self._sum_invoices(params)
            elif mode == "verify":
                return self._verify(params)
            else:
                return json.dumps({"error": f"Unknown mode: {mode!r}. Use sum_lines, sum_invoices, or verify."})
        except (ValueError, KeyError) as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            logger.error(f"CalculateTotalTool error: {exc}")
            return json.dumps({"error": str(exc)})

    @staticmethod
    def _parse_input(input_str: str) -> dict[str, Any]:
        if not input_str or not input_str.strip():
            return {}
        s = input_str.strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON input: {exc}") from exc
        raise ValueError("CalculateTotalTool expects a JSON string.")

    @staticmethod
    def _sum_lines(params: dict[str, Any]) -> str:
        line_items = params.get("line_items", [])
        tax_rate = _d(params.get("tax_rate", 0))
        discount = _d(params.get("discount", 0))

        subtotal = Decimal("0")
        lines_out = []
        for item in line_items:
            qty = _d(item.get("quantity", 1))
            price = _d(item.get("unit_price", 0))
            line_total = (qty * price).quantize(_TWO_PLACES, ROUND_HALF_UP)
            subtotal += line_total
            lines_out.append({
                "description": item.get("description", ""),
                "quantity": str(qty),
                "unit_price": str(price),
                "line_total": str(line_total),
            })

        tax_amount = (subtotal * tax_rate).quantize(_TWO_PLACES, ROUND_HALF_UP)
        computed = (subtotal - discount + tax_amount).quantize(_TWO_PLACES, ROUND_HALF_UP)

        result: dict[str, Any] = {
            "mode": "sum_lines",
            "subtotal": str(subtotal),
            "tax_rate": str(tax_rate),
            "tax_amount": str(tax_amount),
            "discount": str(discount),
            "computed_total": str(computed),
            "line_items": lines_out,
        }

        if "stated_total" in params:
            stated = _d(params["stated_total"]).quantize(_TWO_PLACES, ROUND_HALF_UP)
            discrepancy = (computed - stated).quantize(_TWO_PLACES, ROUND_HALF_UP)
            result["stated_total"] = str(stated)
            result["match"] = discrepancy == Decimal("0")
            result["discrepancy"] = str(discrepancy)

        return json.dumps(result)

    @staticmethod
    def _sum_invoices(params: dict[str, Any]) -> str:
        amounts = params.get("amounts", [])
        if not amounts:
            return json.dumps({"error": "amounts list is required for sum_invoices mode."})
        total = sum(_d(a) for a in amounts).quantize(_TWO_PLACES, ROUND_HALF_UP)
        return json.dumps({
            "mode": "sum_invoices",
            "computed_total": str(total),
            "count": len(amounts),
            "amounts": [str(_d(a)) for a in amounts],
        })

    @staticmethod
    def _verify(params: dict[str, Any]) -> str:
        subtotal = _d(params.get("subtotal", 0))
        tax_rate = _d(params.get("tax_rate", 0))
        discount = _d(params.get("discount", 0))
        stated = _d(params.get("stated_total", 0)).quantize(_TWO_PLACES, ROUND_HALF_UP)

        tax_amount = (subtotal * tax_rate).quantize(_TWO_PLACES, ROUND_HALF_UP)
        computed = (subtotal - discount + tax_amount).quantize(_TWO_PLACES, ROUND_HALF_UP)
        discrepancy = (computed - stated).quantize(_TWO_PLACES, ROUND_HALF_UP)

        return json.dumps({
            "mode": "verify",
            "subtotal": str(subtotal),
            "tax_amount": str(tax_amount),
            "discount": str(discount),
            "computed_total": str(computed),
            "stated_total": str(stated),
            "match": discrepancy == Decimal("0"),
            "discrepancy": str(discrepancy),
        })
