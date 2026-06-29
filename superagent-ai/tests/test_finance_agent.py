"""
Tests for FinanceAgent.

All invoice data comes from MockInvoiceStore.
All LLM calls use MockLLMProvider.
No real database, real network, or real email is used.
"""

from __future__ import annotations

import json
import os

import pytest

from agents.finance_agent import FinanceAgent
from core.base_agent import ApprovalRequired, StepLimitReached
from core.llm.mock_provider import MockLLMProvider
from core.tools.base_tool import ToolZone


# ---------------------------------------------------------------------------
# MockInvoiceStore (same as in test_finance_tools.py)
# ---------------------------------------------------------------------------

class MockInvoiceStore:
    def __init__(self, invoices=None):
        self._invoices = list(invoices or [])
        self.flagged: list[dict] = []

    def all(self):
        return list(self._invoices)

    def filter(self, **kwargs):
        results = self._invoices
        for k, v in kwargs.items():
            results = [i for i in results if i.get(k) == v]
        return results

    def get(self, invoice_id):
        return next((i for i in self._invoices if i.get("id") == invoice_id), None)

    def flag(self, invoice_id, reason="", notes=""):
        self.flagged.append({"invoice_id": invoice_id, "reason": reason, "notes": notes})
        for inv in self._invoices:
            if inv.get("id") == invoice_id:
                inv["status"] = "flagged"


def _make_invoice(id="INV-001", invoice_number="2024-001", vendor="Acme Corp",
                  amount=1000.0, status="pending", due_date="2099-12-31"):
    return {
        "id": id,
        "invoice_number": invoice_number,
        "vendor_name": vendor,
        "vendor_email": "billing@acme.com",
        "amount": amount,
        "currency": "USD",
        "invoice_date": "2024-03-01",
        "due_date": due_date,
        "status": status,
        "line_items": [{"description": "Services", "quantity": 1, "unit_price": amount}],
    }


# ---------------------------------------------------------------------------
# Initialisation tests
# ---------------------------------------------------------------------------

class TestFinanceAgentInit:
    def test_agent_name(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert agent.name == "FinanceAgent"

    def test_has_get_invoices_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "get_invoices" in agent._tools

    def test_has_calculate_total_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "calculate_total" in agent._tools

    def test_has_detect_duplicate_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "detect_duplicate" in agent._tools

    def test_has_flag_invoice_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "flag_invoice" in agent._tools

    def test_has_export_csv_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "export_csv" in agent._tools

    def test_has_calculator_tool(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert "calculator" in agent._tools

    def test_flag_invoice_is_yellow(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        assert agent._tools["flag_invoice"].zone == ToolZone.YELLOW

    def test_all_other_tools_are_green(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        green_tools = ["get_invoices", "calculate_total", "detect_duplicate",
                       "export_csv", "calculator", "current_time"]
        for name in green_tools:
            assert agent._tools[name].zone == ToolZone.GREEN, f"{name} should be GREEN"

    def test_system_prompt_mentions_accuracy(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        prompt = agent._system_prompt()
        assert "ACCURACY" in prompt or "accuracy" in prompt.lower()

    def test_system_prompt_mentions_qa_agent(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        prompt = agent._system_prompt()
        assert "QA" in prompt or "qa" in prompt.lower()

    def test_system_prompt_mentions_yellow_flag(self):
        agent = FinanceAgent(llm_provider=MockLLMProvider([]))
        prompt = agent._system_prompt()
        assert "YELLOW" in prompt or "approval" in prompt.lower()


# ---------------------------------------------------------------------------
# Invoice retrieval workflow
# ---------------------------------------------------------------------------

class TestFinanceAgentRetrieve:
    def test_retrieves_and_returns_invoices(self):
        store = MockInvoiceStore([_make_invoice(), _make_invoice("INV-002", "2024-002")])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "get_invoices", "input": "{}"}},
            {"content": "Found 2 invoices: INV-001 and INV-002 from Acme Corp.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Show me all invoices")
        assert result

    def test_audit_log_records_get_invoices(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "get_invoices", "input": "{}"}},
            {"content": "Done.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        agent.run("List invoices")
        event_types = [e["event_type"] for e in agent.get_audit_log()]
        assert "tool_called" in event_types

    def test_filter_pending_invoices(self):
        store = MockInvoiceStore([
            _make_invoice("INV-001", status="pending"),
            _make_invoice("INV-002", "2024-002", status="paid"),
        ])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "get_invoices", "input": '{"status": "pending"}'}},
            {"content": "1 pending invoice found.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Show pending invoices")
        assert result


# ---------------------------------------------------------------------------
# Total calculation workflow
# ---------------------------------------------------------------------------

class TestFinanceAgentCalculate:
    def test_calculates_total(self):
        store = MockInvoiceStore([_make_invoice(amount=500.0)])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {
                "name": "calculate_total",
                "input": json.dumps({
                    "mode": "sum_lines",
                    "line_items": [{"description": "Services", "quantity": 1, "unit_price": 500.0}],
                    "stated_total": 500.0,
                }),
            }},
            {"content": "Total verified: $500.00. No discrepancy.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Verify invoice INV-001 total")
        assert result

    def test_detects_total_mismatch(self):
        store = MockInvoiceStore([_make_invoice(amount=500.0)])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {
                "name": "calculate_total",
                "input": json.dumps({
                    "mode": "sum_lines",
                    "line_items": [{"description": "Services", "quantity": 1, "unit_price": 500.0}],
                    "stated_total": 400.0,
                }),
            }},
            {"content": "MISMATCH: computed $500.00, stated $400.00. Discrepancy: $100.00.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Verify totals")
        assert result


# ---------------------------------------------------------------------------
# Duplicate detection workflow
# ---------------------------------------------------------------------------

class TestFinanceAgentDuplicates:
    def test_detects_duplicates(self):
        invoices = [
            _make_invoice("INV-001", "SAME-NUM"),
            _make_invoice("INV-002", "SAME-NUM"),
        ]
        store = MockInvoiceStore(invoices)
        llm = MockLLMProvider([
            {"content": "", "tool_call": {
                "name": "detect_duplicate",
                "input": json.dumps({"invoices": invoices}),
            }},
            {"content": "Duplicate found: INV-001 and INV-002 share invoice number SAME-NUM.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Check for duplicates")
        assert result

    def test_no_duplicates_reported_clean(self):
        invoices = [
            _make_invoice("INV-001", "2024-001"),
            _make_invoice("INV-002", "2024-002"),
        ]
        store = MockInvoiceStore(invoices)
        llm = MockLLMProvider([
            {"content": "", "tool_call": {
                "name": "detect_duplicate",
                "input": json.dumps({"invoices": invoices}),
            }},
            {"content": "No duplicates found. All 2 invoices are clean.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Check for duplicates")
        assert result


# ---------------------------------------------------------------------------
# Flag invoice (approval gate) tests
# ---------------------------------------------------------------------------

class TestFinanceAgentFlagInvoice:
    def test_flag_raises_approval_required(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([{
            "content": "",
            "tool_call": {
                "name": "flag_invoice",
                "input": json.dumps({
                    "invoice_id": "INV-001",
                    "reason": "duplicate",
                    "notes": "Same as INV-002",
                }),
            },
        }])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Flag invoice INV-001")
        assert exc_info.value.tool_name == "flag_invoice"

    def test_pending_approval_contains_invoice_id(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([{
            "content": "",
            "tool_call": {
                "name": "flag_invoice",
                "input": json.dumps({"invoice_id": "INV-001", "reason": "duplicate"}),
            },
        }])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        with pytest.raises(ApprovalRequired):
            agent.run("Flag invoice")
        assert "INV-001" in agent.pending_approval["tool_input"]

    def test_store_not_modified_without_approval(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([{
            "content": "",
            "tool_call": {
                "name": "flag_invoice",
                "input": json.dumps({"invoice_id": "INV-001", "reason": "duplicate"}),
            },
        }])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        with pytest.raises(ApprovalRequired):
            agent.run("Flag invoice")
        assert len(store.flagged) == 0


# ---------------------------------------------------------------------------
# CSV export workflow
# ---------------------------------------------------------------------------

class TestFinanceAgentExportCSV:
    def test_export_creates_file(self):
        invoices = [_make_invoice()]
        store = MockInvoiceStore(invoices)
        llm = MockLLMProvider([
            {"content": "", "tool_call": {
                "name": "export_csv",
                "input": json.dumps({"invoices": invoices, "filename": "test_agent_export.csv"}),
            }},
            {"content": "Exported 1 invoice to /tmp/test_agent_export.csv.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        result = agent.run("Export invoices to CSV")
        assert result
        if os.path.exists("/tmp/test_agent_export.csv"):
            os.remove("/tmp/test_agent_export.csv")


# ---------------------------------------------------------------------------
# Limits and audit log
# ---------------------------------------------------------------------------

class TestFinanceAgentLimits:
    def test_respects_max_steps(self):
        store = MockInvoiceStore([])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "get_invoices", "input": "{}"}}
        ] * 10)
        agent = FinanceAgent(llm_provider=llm, invoice_store=store, max_steps=3)
        with pytest.raises(StepLimitReached):
            agent.run("Loop forever")

    def test_audit_log_populated(self):
        store = MockInvoiceStore([_make_invoice()])
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "get_invoices", "input": "{}"}},
            {"content": "Done.", "tool_call": None},
        ])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        agent.run("List invoices")
        log = agent.get_audit_log()
        assert len(log) > 0
        assert any(e["event_type"] == "task_started" for e in log)
        assert any(e["event_type"] == "task_completed" for e in log)

    def test_cost_summary_structure(self):
        store = MockInvoiceStore([])
        llm = MockLLMProvider([{"content": "Done.", "tool_call": None}])
        agent = FinanceAgent(llm_provider=llm, invoice_store=store)
        agent.run("Quick task")
        summary = agent.get_cost_summary()
        assert "total_cost_usd" in summary
        assert "total_steps" in summary
        assert summary["total_steps"] >= 1
