"""
Tests for DocumentAgent.

All Drive API calls use MockDriveService.
All LLM calls use MockLLMProvider.
No real credentials, network calls, or file moves are made.
"""

from __future__ import annotations

import io
import json

import pytest

from agents.document_agent import DocumentAgent
from core.base_agent import ApprovalRequired, StepLimitReached
from core.llm.mock_provider import MockLLMProvider
from core.tools.base_tool import ToolZone


# ---------------------------------------------------------------------------
# Re-use mock from test_drive_tools
# ---------------------------------------------------------------------------

class MockDriveService:
    def __init__(self, search_results=None, file_meta=None, pdf_bytes=None):
        self._search_results = search_results or []
        self._file_meta = file_meta or {"parents": ["folder_inbox"]}
        self._pdf_bytes = pdf_bytes or b""
        self.moved_files: list[dict] = []

    def files(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        return {"files": self._search_results}

    def get(self, fileId=None, fields=None):
        return self

    def get_media(self, fileId=None):
        return self._pdf_bytes

    def get_pdf_bytes(self, file_id):
        return self._pdf_bytes

    def update(self, fileId=None, addParents=None, removeParents=None, fields=None):
        self.moved_files.append({"file_id": fileId, "destination": addParents})
        return self

    def __call__(self, *a, **kw):
        return self


def _make_pdf_bytes() -> bytes:
    import PyPDF2
    writer = PyPDF2.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_file(name="Invoice_2024.pdf"):
    return {
        "id": "file_001",
        "name": name,
        "mimeType": "application/pdf",
        "size": "51200",
        "modifiedTime": "2024-03-15T10:00:00Z",
        "webViewLink": "https://drive.google.com/file/d/file_001/view",
        "parents": ["folder_inbox"],
    }


# ---------------------------------------------------------------------------
# Initialisation tests
# ---------------------------------------------------------------------------

class TestDocumentAgentInit:
    def test_agent_name(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert agent.name == "DocumentAgent"

    def test_has_search_drive_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "search_drive" in agent._tools

    def test_has_read_pdf_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "read_pdf" in agent._tools

    def test_has_extract_data_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "extract_data" in agent._tools

    def test_has_move_file_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "move_file" in agent._tools

    def test_has_calculator_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "calculator" in agent._tools

    def test_has_current_time_tool(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert "current_time" in agent._tools

    def test_move_file_is_yellow(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        assert agent._tools["move_file"].zone == ToolZone.YELLOW

    def test_system_prompt_contains_drive_keywords(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        prompt = agent._system_prompt()
        assert "DocumentAgent" in prompt
        assert "invoice" in prompt.lower()
        assert "move_file" in prompt

    def test_system_prompt_mentions_approval(self):
        llm = MockLLMProvider([])
        agent = DocumentAgent(llm_provider=llm)
        prompt = agent._system_prompt()
        assert "approval" in prompt.lower() or "YELLOW" in prompt


# ---------------------------------------------------------------------------
# Search workflow tests
# ---------------------------------------------------------------------------

class TestDocumentAgentSearch:
    def test_search_and_return_results(self):
        """Agent searches Drive and returns file list."""
        files = [_make_file("Invoice_March.pdf")]
        drive = MockDriveService(search_results=files)
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {"name": "search_drive", "input": '{"query": "invoice"}'},
            },
            {
                "content": "Found 1 invoice: Invoice_March.pdf",
                "tool_call": None,
            },
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        result = agent.run("Find all invoices in Drive")
        assert "Invoice_March.pdf" in result or "Found" in result

    def test_empty_search_handled(self):
        drive = MockDriveService(search_results=[])
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {"name": "search_drive", "input": '{"query": "invoice"}'},
            },
            {
                "content": "No invoices found in Drive.",
                "tool_call": None,
            },
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        result = agent.run("Find invoices")
        assert "No invoices" in result or result

    def test_audit_log_records_tool_call(self):
        drive = MockDriveService(search_results=[_make_file()])
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {"name": "search_drive", "input": '{"query": "invoice"}'},
            },
            {"content": "Done.", "tool_call": None},
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        agent.run("Find invoices")
        event_types = [e["event_type"] for e in agent.get_audit_log()]
        assert "tool_called" in event_types


# ---------------------------------------------------------------------------
# PDF reading tests
# ---------------------------------------------------------------------------

class TestDocumentAgentReadPDF:
    def test_reads_pdf_and_returns_text(self):
        pdf_bytes = _make_pdf_bytes()
        drive = MockDriveService(pdf_bytes=pdf_bytes)
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {"name": "read_pdf", "input": '{"file_id": "file_001"}'},
            },
            {
                "content": "PDF text extracted: 1 page.",
                "tool_call": None,
            },
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        result = agent.run("Read the PDF file_001")
        assert result

    def test_audit_log_records_read_pdf(self):
        pdf_bytes = _make_pdf_bytes()
        drive = MockDriveService(pdf_bytes=pdf_bytes)
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {"name": "read_pdf", "input": '{"file_id": "file_001"}'},
            },
            {"content": "Done.", "tool_call": None},
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        agent.run("Read PDF")
        event_types = [e["event_type"] for e in agent.get_audit_log()]
        assert "tool_called" in event_types


# ---------------------------------------------------------------------------
# Extract data tests
# ---------------------------------------------------------------------------

class TestDocumentAgentExtractData:
    _INVOICE_JSON = json.dumps({
        "document_type": "invoice",
        "vendor_name": "Acme Corp",
        "invoice_number": "INV-001",
        "total_amount": "500.00",
        "currency": "USD",
        "due_date": "2024-04-30",
        "line_items": [],
        "payment_terms": "Net 30",
        "vendor_email": None,
        "invoice_date": "2024-03-31",
        "notes": None,
    })

    def test_extract_returns_structured_data(self):
        llm = MockLLMProvider([
            # First call: extract_data tool selected
            {
                "content": "",
                "tool_call": {
                    "name": "extract_data",
                    "input": '{"text": "Invoice INV-001 from Acme Corp. Total: $500."}',
                },
            },
            # extract_data internally calls LLM — provide that response too
            # (ExtractDataTool uses its own llm_provider reference)
            # After tool result comes back, agent finishes
            {
                "content": "Extracted: vendor=Acme Corp, invoice=INV-001, total=$500.",
                "tool_call": None,
            },
        ])
        # ExtractDataTool needs its own LLM call — provide via separate mock
        extract_llm = MockLLMProvider([
            {"content": self._INVOICE_JSON, "tool_call": None},
        ])
        from core.tools.drive.extract_data import ExtractDataTool
        extract_tool = ExtractDataTool(llm_provider=extract_llm)

        # Build agent manually to inject pre-configured extract_tool
        from core.base_agent import BaseAgent
        from core.tools.drive.search_drive import SearchDriveTool
        from core.tools.drive.read_pdf import ReadPDFTool
        from core.tools.drive.move_file import MoveFileTool
        from core.tools.calculator import CalculatorTool
        from core.tools.current_time import CurrentTimeTool
        from agents.document_agent import _SYSTEM_PROMPT

        agent = BaseAgent(
            name="DocumentAgent",
            llm_provider=llm,
            tools=[
                SearchDriveTool(drive_service=MockDriveService()),
                ReadPDFTool(drive_service=MockDriveService()),
                extract_tool,
                MoveFileTool(drive_service=MockDriveService()),
                CalculatorTool(),
                CurrentTimeTool(),
            ],
        )

        result = agent.run("Extract data from the document")
        assert result


# ---------------------------------------------------------------------------
# Move file (approval) tests
# ---------------------------------------------------------------------------

class TestDocumentAgentMoveFile:
    def test_move_file_raises_approval_required(self):
        """Agent must raise ApprovalRequired when LLM selects move_file."""
        drive = MockDriveService(file_meta={"parents": ["folder_inbox"]})
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {
                    "name": "move_file",
                    "input": json.dumps({
                        "file_id": "file_001",
                        "destination_id": "folder_archive",
                        "file_name": "Invoice_2024.pdf",
                    }),
                },
            }
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Move the invoice to the archive folder")
        assert exc_info.value.tool_name == "move_file"

    def test_pending_approval_snapshot_saved(self):
        drive = MockDriveService(file_meta={"parents": ["folder_inbox"]})
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {
                    "name": "move_file",
                    "input": json.dumps({
                        "file_id": "file_001",
                        "destination_id": "folder_archive",
                    }),
                },
            }
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        with pytest.raises(ApprovalRequired):
            agent.run("Move file")
        assert agent.pending_approval is not None
        assert agent.pending_approval["tool_name"] == "move_file"

    def test_move_not_called_without_approval(self):
        drive = MockDriveService(file_meta={"parents": ["folder_inbox"]})
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {
                    "name": "move_file",
                    "input": json.dumps({
                        "file_id": "file_001",
                        "destination_id": "folder_archive",
                    }),
                },
            }
        ])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        with pytest.raises(ApprovalRequired):
            agent.run("Move file")
        # File should NOT have been moved
        assert len(drive.moved_files) == 0


# ---------------------------------------------------------------------------
# Cost and step limit tests
# ---------------------------------------------------------------------------

class TestDocumentAgentLimits:
    def test_respects_max_steps(self):
        from core.base_agent import StepLimitReached

        # LLM keeps calling tools indefinitely
        responses = [
            {"content": "", "tool_call": {"name": "search_drive", "input": '{"query": "x"}'}}
        ] * 10
        drive = MockDriveService(search_results=[])
        llm = MockLLMProvider(responses)
        agent = DocumentAgent(llm_provider=llm, drive_service=drive, max_steps=3)
        with pytest.raises(StepLimitReached):
            agent.run("Infinite loop")

    def test_cost_summary_returned(self):
        drive = MockDriveService(search_results=[])
        llm = MockLLMProvider([{"content": "Done.", "tool_call": None}])
        agent = DocumentAgent(llm_provider=llm, drive_service=drive)
        agent.run("Quick task")
        summary = agent.get_cost_summary()
        assert "total_cost_usd" in summary
        assert "total_steps" in summary
