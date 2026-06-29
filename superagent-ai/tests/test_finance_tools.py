"""
Tests for Finance tools: GetInvoicesTool, CalculateTotalTool,
DetectDuplicateTool, FlagInvoiceTool, ExportCSVTool.

All data comes from MockInvoiceStore — no real database, no real files
beyond actual /tmp/ CSV creation (which is tested and cleaned up).
"""

from __future__ import annotations

import csv
import json
import os

import pytest

from core.tools.finance.get_invoices import GetInvoicesTool
from core.tools.finance.calculate_total import CalculateTotalTool
from core.tools.finance.detect_duplicate import DetectDuplicateTool
from core.tools.finance.flag_invoice import FlagInvoiceTool
from core.tools.finance.export_csv import ExportCSVTool
from core.tools.base_tool import ToolZone
from core.base_agent import BaseAgent, ApprovalRequired
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Mock invoice store
# ---------------------------------------------------------------------------

class MockInvoiceStore:
    """In-memory invoice data store for testing."""

    def __init__(self, invoices: list[dict] | None = None) -> None:
        self._invoices = list(invoices or [])
        self.flagged: list[dict] = []

    def all(self) -> list[dict]:
        return list(self._invoices)

    def filter(self, **kwargs) -> list[dict]:
        results = self._invoices
        for key, val in kwargs.items():
            results = [i for i in results if i.get(key) == val]
        return results

    def get(self, invoice_id: str) -> dict | None:
        for inv in self._invoices:
            if inv.get("id") == invoice_id:
                return inv
        return None

    def flag(self, invoice_id: str, reason: str = "", notes: str = "") -> None:
        self.flagged.append({"invoice_id": invoice_id, "reason": reason, "notes": notes})
        for inv in self._invoices:
            if inv.get("id") == invoice_id:
                inv["status"] = "flagged"


def _make_invoice(
    id="INV-001",
    invoice_number="2024-001",
    vendor="Acme Corp",
    amount=1000.00,
    currency="USD",
    status="pending",
    invoice_date="2024-03-01",
    due_date="2024-03-31",
):
    return {
        "id": id,
        "invoice_number": invoice_number,
        "vendor_name": vendor,
        "vendor_email": f"billing@{vendor.lower().replace(' ', '')}.com",
        "amount": amount,
        "currency": currency,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "status": status,
        "line_items": [
            {"description": "Services", "quantity": 1, "unit_price": amount}
        ],
    }


# ---------------------------------------------------------------------------
# GetInvoicesTool
# ---------------------------------------------------------------------------

class TestGetInvoicesTool:
    def test_returns_all_invoices(self):
        store = MockInvoiceStore([_make_invoice(), _make_invoice("INV-002", "2024-002")])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run("{}"))
        assert isinstance(result, list)
        assert len(result) == 2

    def test_filter_by_status(self):
        store = MockInvoiceStore([
            _make_invoice("INV-001", status="pending"),
            _make_invoice("INV-002", "2024-002", status="paid"),
        ])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run('{"status": "pending"}'))
        assert len(result) == 1
        assert result[0]["status"] == "pending"

    def test_filter_by_vendor(self):
        store = MockInvoiceStore([
            _make_invoice("INV-001", vendor="Acme Corp"),
            _make_invoice("INV-002", "2024-002", vendor="Beta Ltd"),
        ])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run('{"vendor": "Acme"}'))
        assert len(result) == 1
        assert "Acme" in result[0]["vendor_name"]

    def test_filter_overdue(self):
        store = MockInvoiceStore([
            _make_invoice("INV-001", due_date="2020-01-01", status="pending"),
            _make_invoice("INV-002", "2024-002", due_date="2099-12-31", status="pending"),
        ])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run('{"overdue": true}'))
        assert len(result) == 1
        assert result[0]["id"] == "INV-001"

    def test_get_by_invoice_id(self):
        store = MockInvoiceStore([_make_invoice("INV-001")])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run('{"invoice_id": "INV-001"}'))
        assert len(result) == 1
        assert result[0]["id"] == "INV-001"

    def test_get_by_nonexistent_id_returns_empty(self):
        store = MockInvoiceStore([_make_invoice()])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run('{"invoice_id": "DOES-NOT-EXIST"}'))
        assert result == []

    def test_no_store_returns_error(self):
        tool = GetInvoicesTool(invoice_store=None)
        result = json.loads(tool.run("{}"))
        assert "error" in result

    def test_empty_input_returns_all(self):
        store = MockInvoiceStore([_make_invoice()])
        tool = GetInvoicesTool(invoice_store=store)
        result = json.loads(tool.run(""))
        assert len(result) == 1

    def test_zone_is_green(self):
        assert GetInvoicesTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# CalculateTotalTool
# ---------------------------------------------------------------------------

class TestCalculateTotalTool:
    def _tool(self):
        return CalculateTotalTool()

    def test_sum_lines_basic(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [
                {"description": "A", "quantity": 2, "unit_price": 50.00},
                {"description": "B", "quantity": 1, "unit_price": 25.00},
            ],
        })))
        assert result["computed_total"] == "125.00"

    def test_sum_lines_with_tax(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.00}],
            "tax_rate": 0.10,
        })))
        assert result["computed_total"] == "110.00"
        assert result["tax_amount"] == "10.00"

    def test_sum_lines_with_discount(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.00}],
            "discount": 5.00,
        })))
        assert result["computed_total"] == "95.00"

    def test_stated_total_match(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.00}],
            "stated_total": 100.00,
        })))
        assert result["match"] is True
        assert result["discrepancy"] == "0.00"

    def test_stated_total_mismatch(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [{"description": "X", "quantity": 1, "unit_price": 100.00}],
            "stated_total": 90.00,
        })))
        assert result["match"] is False
        assert result["discrepancy"] == "10.00"

    def test_sum_invoices_mode(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_invoices",
            "amounts": [100.00, 250.50, 75.00],
        })))
        assert result["computed_total"] == "425.50"
        assert result["count"] == 3

    def test_verify_mode_match(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "verify",
            "subtotal": 100.00,
            "tax_rate": 0.10,
            "discount": 0.00,
            "stated_total": 110.00,
        })))
        assert result["match"] is True

    def test_verify_mode_mismatch(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "verify",
            "subtotal": 100.00,
            "tax_rate": 0.10,
            "discount": 0.00,
            "stated_total": 100.00,
        })))
        assert result["match"] is False

    def test_decimal_precision(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [{"description": "X", "quantity": 3, "unit_price": 0.10}],
        })))
        assert result["computed_total"] == "0.30"

    def test_unknown_mode_returns_error(self):
        result = json.loads(self._tool().run('{"mode": "magic"}'))
        assert "error" in result

    def test_empty_line_items(self):
        result = json.loads(self._tool().run(json.dumps({
            "mode": "sum_lines",
            "line_items": [],
        })))
        assert result["computed_total"] == "0.00"

    def test_zone_is_green(self):
        assert CalculateTotalTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# DetectDuplicateTool
# ---------------------------------------------------------------------------

class TestDetectDuplicateTool:
    def _tool(self):
        return DetectDuplicateTool()

    def test_no_duplicates_clean(self):
        invoices = [
            _make_invoice("INV-001", "2024-001", amount=100.00),
            _make_invoice("INV-002", "2024-002", amount=200.00),
        ]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        assert result["duplicates"] == []
        assert result["clean_count"] == 2

    def test_detects_exact_number_duplicate(self):
        invoices = [
            _make_invoice("INV-001", "SAME-NUM", amount=100.00),
            _make_invoice("INV-002", "SAME-NUM", amount=100.00),
        ]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        dups = result["duplicates"]
        assert len(dups) == 1
        assert dups[0]["type"] == "exact_number"
        assert dups[0]["confidence"] == "high"
        assert "INV-001" in dups[0]["invoice_ids"]
        assert "INV-002" in dups[0]["invoice_ids"]

    def test_detects_same_vendor_amount_date(self):
        invoices = [
            _make_invoice("INV-001", "2024-001", vendor="Acme", amount=500.0, invoice_date="2024-03-01"),
            _make_invoice("INV-002", "2024-002", vendor="Acme", amount=500.0, invoice_date="2024-03-02"),
        ]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        dups = result["duplicates"]
        assert len(dups) >= 1
        assert dups[0]["confidence"] in ("high", "medium")

    def test_different_vendors_not_duplicate(self):
        invoices = [
            _make_invoice("INV-001", "2024-001", vendor="Acme", amount=500.0),
            _make_invoice("INV-002", "2024-002", vendor="Beta", amount=500.0),
        ]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        assert result["duplicates"] == []

    def test_empty_list_handled(self):
        result = json.loads(self._tool().run('{"invoices": []}'))
        assert result["total_checked"] == 0

    def test_total_checked_is_correct(self):
        invoices = [_make_invoice(f"INV-{i:03d}", f"2024-{i:03d}") for i in range(5)]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        assert result["total_checked"] == 5

    def test_zone_is_green(self):
        assert DetectDuplicateTool().zone == ToolZone.GREEN

    def test_multiple_duplicates_detected(self):
        invoices = [
            _make_invoice("INV-001", "SAME", amount=100.0),
            _make_invoice("INV-002", "SAME", amount=100.0),
            _make_invoice("INV-003", "SAME", amount=100.0),
        ]
        result = json.loads(self._tool().run(json.dumps({"invoices": invoices})))
        assert result["duplicates"][0]["type"] == "exact_number"


# ---------------------------------------------------------------------------
# FlagInvoiceTool
# ---------------------------------------------------------------------------

class TestFlagInvoiceTool:
    def test_zone_is_yellow(self):
        assert FlagInvoiceTool().zone == ToolZone.YELLOW

    def test_zone_is_always_yellow(self):
        tool1 = FlagInvoiceTool()
        tool2 = FlagInvoiceTool(invoice_store=MockInvoiceStore())
        assert tool1.zone == ToolZone.YELLOW
        assert tool2.zone == ToolZone.YELLOW

    def test_raises_approval_required_via_agent(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([{
            "content": "",
            "tool_call": {
                "name": "flag_invoice",
                "input": json.dumps({
                    "invoice_id": "INV-001",
                    "reason": "duplicate",
                    "notes": "Same number as INV-002",
                }),
            },
        }])
        agent = BaseAgent(
            name="TestAgent",
            llm_provider=llm,
            tools=[FlagInvoiceTool(invoice_store=store)],
        )
        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Flag invoice INV-001")
        assert exc_info.value.tool_name == "flag_invoice"

    def test_run_flags_invoice_in_store(self):
        store = MockInvoiceStore([_make_invoice()])
        tool = FlagInvoiceTool(invoice_store=store)
        result = json.loads(tool.run(json.dumps({
            "invoice_id": "INV-001",
            "reason": "duplicate",
            "notes": "Test flag",
        })))
        assert result["status"] == "flagged"
        assert result["invoice_id"] == "INV-001"
        assert len(store.flagged) == 1

    def test_result_contains_timestamp(self):
        tool = FlagInvoiceTool()
        result = json.loads(tool.run(json.dumps({
            "invoice_id": "INV-001",
            "reason": "amount_mismatch",
        })))
        assert "timestamp" in result

    def test_missing_invoice_id_raises_error(self):
        tool = FlagInvoiceTool()
        with pytest.raises(ValueError, match="invoice_id"):
            tool.run(json.dumps({"reason": "duplicate"}))

    def test_missing_reason_raises_error(self):
        tool = FlagInvoiceTool()
        with pytest.raises(ValueError, match="reason"):
            tool.run(json.dumps({"invoice_id": "INV-001"}))

    def test_invalid_reason_raises_error(self):
        tool = FlagInvoiceTool()
        with pytest.raises(ValueError, match="invalid reason"):
            tool.run(json.dumps({"invoice_id": "INV-001", "reason": "bad_reason"}))

    def test_all_valid_reasons_accepted(self):
        from core.tools.finance.flag_invoice import _VALID_REASONS
        tool = FlagInvoiceTool()
        for reason in _VALID_REASONS:
            result = json.loads(tool.run(json.dumps({
                "invoice_id": "INV-001",
                "reason": reason,
            })))
            assert result["status"] == "flagged"

    def test_store_not_flagged_without_approval(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([{
            "content": "",
            "tool_call": {
                "name": "flag_invoice",
                "input": json.dumps({"invoice_id": "INV-001", "reason": "duplicate"}),
            },
        }])
        agent = BaseAgent(
            name="Test",
            llm_provider=llm,
            tools=[FlagInvoiceTool(invoice_store=store)],
        )
        with pytest.raises(ApprovalRequired):
            agent.run("Flag invoice")
        assert len(store.flagged) == 0


# ---------------------------------------------------------------------------
# ExportCSVTool
# ---------------------------------------------------------------------------

class TestExportCSVTool:
    def _invoices(self, n=3):
        return [_make_invoice(f"INV-{i:03d}", f"2024-{i:03d}", amount=float(100 * i)) for i in range(1, n + 1)]

    def test_creates_file_in_tmp(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": self._invoices()})))
        assert result["status"] == "exported"
        assert result["file_path"].startswith("/tmp/")
        assert os.path.exists(result["file_path"])
        os.remove(result["file_path"])

    def test_row_count_correct(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": self._invoices(5)})))
        assert result["row_count"] == 5
        os.remove(result["file_path"])

    def test_csv_has_correct_columns(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": self._invoices(1)})))
        with open(result["file_path"], newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
        assert "invoice_number" in headers
        assert "vendor_name" in headers
        assert "amount" in headers
        os.remove(result["file_path"])

    def test_csv_data_is_correct(self):
        invoices = [_make_invoice("INV-001", "2024-001", vendor="Acme", amount=999.99)]
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": invoices})))
        with open(result["file_path"], newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["vendor_name"] == "Acme"
        assert rows[0]["invoice_number"] == "2024-001"
        os.remove(result["file_path"])

    def test_custom_filename(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({
            "invoices": self._invoices(1),
            "filename": "test_export.csv",
        })))
        assert result["file_path"] == "/tmp/test_export.csv"
        os.remove(result["file_path"])

    def test_empty_invoices_returns_error(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": []})))
        assert "error" in result

    def test_fields_listed_in_result(self):
        tool = ExportCSVTool()
        result = json.loads(tool.run(json.dumps({"invoices": self._invoices(1)})))
        assert isinstance(result["fields"], list)
        assert len(result["fields"]) > 0
        os.remove(result["file_path"])

    def test_zone_is_green(self):
        assert ExportCSVTool().zone == ToolZone.GREEN
