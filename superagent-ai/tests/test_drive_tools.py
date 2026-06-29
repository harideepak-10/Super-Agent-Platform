"""
Tests for Drive tools: SearchDriveTool, ReadPDFTool, ExtractDataTool, MoveFileTool.

All Drive API calls are intercepted by MockDriveService.
No real credentials, network calls, or file moves are made.
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from core.tools.drive.search_drive import SearchDriveTool
from core.tools.drive.read_pdf import ReadPDFTool
from core.tools.drive.extract_data import ExtractDataTool
from core.tools.drive.move_file import MoveFileTool
from core.tools.base_tool import ToolZone
from core.base_agent import BaseAgent, ApprovalRequired
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Mock Drive service
# ---------------------------------------------------------------------------

class _MockFilesList:
    def __init__(self, files):
        self._files = files

    def execute(self):
        return {"files": self._files}


class _MockFilesGet:
    def __init__(self, meta=None, pdf_bytes=None):
        self._meta = meta or {}
        self._pdf_bytes = pdf_bytes or b""

    def execute(self):
        return self._meta

    def get_media(self, *args, **kwargs):
        return self


class _MockFilesUpdate:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class MockDriveService:
    """Minimal Google Drive API mock for testing."""

    def __init__(self, search_results=None, file_meta=None, pdf_bytes=None):
        self._search_results = search_results or []
        self._file_meta = file_meta or {}
        self._pdf_bytes = pdf_bytes or b""
        self.moved_files: list[dict] = []   # records move calls
        self._update_result = {}

    def files(self):
        return self

    def list(self, **kwargs):
        return _MockFilesList(self._search_results)

    def get(self, fileId=None, fields=None):
        return _MockFilesGet(meta=self._file_meta)

    def get_media(self, fileId=None):
        # Called by ReadPDFTool._download when no MediaIoBaseDownload
        return self._pdf_bytes

    # Support the shortcut path used by ReadPDFTool._download
    def get_pdf_bytes(self, file_id: str) -> bytes:
        return self._pdf_bytes

    def update(self, fileId=None, addParents=None, removeParents=None, fields=None):
        self.moved_files.append({
            "file_id": fileId,
            "add_parents": addParents,
            "remove_parents": removeParents,
        })
        self._update_result = {"id": fileId, "parents": [addParents]}
        return _MockFilesUpdate(self._update_result)


def _make_minimal_pdf(text: str = "Invoice #1042\nTotal: $500.00") -> bytes:
    """Create a real minimal PDF in memory using PyPDF2 writer."""
    import PyPDF2
    writer = PyPDF2.PdfWriter()
    # PyPDF2 3.x doesn't support add_blank_page text directly,
    # so we create a blank page and patch the stream
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# SearchDriveTool tests
# ---------------------------------------------------------------------------

class TestSearchDriveTool:
    def _make_file(self, name="Invoice_2024.pdf", mime="application/pdf"):
        return {
            "id": "file_001",
            "name": name,
            "mimeType": mime,
            "size": "102400",
            "modifiedTime": "2024-03-15T10:00:00Z",
            "webViewLink": "https://drive.google.com/file/d/file_001/view",
            "parents": ["folder_001"],
        }

    def test_returns_file_list(self):
        mock = MockDriveService(search_results=[self._make_file()])
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run('{"query": "Invoice 2024"}'))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "Invoice_2024.pdf"

    def test_returns_empty_list_when_no_results(self):
        mock = MockDriveService(search_results=[])
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run('{"query": "nonexistent"}'))
        assert result == []

    def test_result_has_required_fields(self):
        mock = MockDriveService(search_results=[self._make_file()])
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run('{"query": "Invoice"}'))
        file = result[0]
        for field in ("id", "name", "mimeType", "webViewLink"):
            assert field in file

    def test_plain_string_input_used_as_query(self):
        mock = MockDriveService(search_results=[self._make_file()])
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run("Invoice 2024"))
        assert isinstance(result, list)

    def test_handles_api_error_gracefully(self):
        class ErrorService:
            def files(self):
                raise RuntimeError("Drive API unavailable")
        tool = SearchDriveTool(drive_service=ErrorService())
        result = json.loads(tool.run('{"query": "test"}'))
        assert "error" in result
        assert "files" in result

    def test_zone_is_green(self):
        assert SearchDriveTool().zone == ToolZone.GREEN

    def test_multiple_results(self):
        files = [self._make_file(f"Invoice_{i}.pdf") for i in range(5)]
        mock = MockDriveService(search_results=files)
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run('{"query": "Invoice", "limit": 5}'))
        assert len(result) == 5

    def test_file_type_filter_accepted(self):
        mock = MockDriveService(search_results=[self._make_file()])
        tool = SearchDriveTool(drive_service=mock)
        result = json.loads(tool.run('{"query": "Invoice", "file_type": "pdf"}'))
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ReadPDFTool tests
# ---------------------------------------------------------------------------

class TestReadPDFTool:
    def test_returns_correct_structure(self):
        pdf_bytes = _make_minimal_pdf()
        mock = MockDriveService(pdf_bytes=pdf_bytes)
        tool = ReadPDFTool(drive_service=mock)
        result = json.loads(tool.run('{"file_id": "file_001"}'))
        assert "file_id" in result
        assert "page_count" in result
        assert "text" in result
        assert "pages" in result

    def test_file_id_in_result(self):
        pdf_bytes = _make_minimal_pdf()
        mock = MockDriveService(pdf_bytes=pdf_bytes)
        tool = ReadPDFTool(drive_service=mock)
        result = json.loads(tool.run('{"file_id": "abc123"}'))
        assert result["file_id"] == "abc123"

    def test_page_count_is_integer(self):
        pdf_bytes = _make_minimal_pdf()
        mock = MockDriveService(pdf_bytes=pdf_bytes)
        tool = ReadPDFTool(drive_service=mock)
        result = json.loads(tool.run('{"file_id": "x"}'))
        assert isinstance(result["page_count"], int)
        assert result["page_count"] >= 1

    def test_pages_is_list(self):
        pdf_bytes = _make_minimal_pdf()
        mock = MockDriveService(pdf_bytes=pdf_bytes)
        tool = ReadPDFTool(drive_service=mock)
        result = json.loads(tool.run('{"file_id": "x"}'))
        assert isinstance(result["pages"], list)

    def test_missing_file_id_returns_error(self):
        tool = ReadPDFTool(drive_service=MockDriveService())
        result = json.loads(tool.run(""))
        assert "error" in result

    def test_handles_download_error_gracefully(self):
        class BadService:
            def files(self):
                raise RuntimeError("Network error")
            def get_pdf_bytes(self, _):
                raise RuntimeError("Network error")
        tool = ReadPDFTool(drive_service=BadService())
        result = json.loads(tool.run('{"file_id": "bad_id"}'))
        assert "error" in result

    def test_plain_string_file_id(self):
        pdf_bytes = _make_minimal_pdf()
        mock = MockDriveService(pdf_bytes=pdf_bytes)
        tool = ReadPDFTool(drive_service=mock)
        result = json.loads(tool.run("file_abc"))
        assert result["file_id"] == "file_abc"

    def test_zone_is_green(self):
        assert ReadPDFTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# ExtractDataTool tests
# ---------------------------------------------------------------------------

class TestExtractDataTool:
    _INVOICE_JSON = json.dumps({
        "document_type": "invoice",
        "vendor_name": "Acme Corp",
        "vendor_email": "billing@acme.com",
        "invoice_number": "INV-2024-001",
        "invoice_date": "2024-03-01",
        "due_date": "2024-03-31",
        "total_amount": "1250.00",
        "currency": "USD",
        "line_items": [
            {"description": "Consulting", "quantity": 10, "unit_price": 125.0, "total": 1250.0}
        ],
        "payment_terms": "Net 30",
        "notes": None,
    })

    def _make_tool(self, llm_response: str):
        llm = MockLLMProvider([{"content": llm_response, "tool_call": None}])
        return ExtractDataTool(llm_provider=llm)

    def test_returns_structured_data(self):
        tool = self._make_tool(self._INVOICE_JSON)
        result = json.loads(tool.run('{"text": "Invoice #INV-2024-001 from Acme Corp..."}'))
        assert result["invoice_number"] == "INV-2024-001"
        assert result["vendor_name"] == "Acme Corp"

    def test_returns_total_amount(self):
        tool = self._make_tool(self._INVOICE_JSON)
        result = json.loads(tool.run('{"text": "Total: $1250.00"}'))
        assert result["total_amount"] == "1250.00"

    def test_returns_document_type(self):
        tool = self._make_tool(self._INVOICE_JSON)
        result = json.loads(tool.run('{"text": "Invoice..."}'))
        assert result["document_type"] == "invoice"

    def test_handles_invalid_llm_json(self):
        tool = self._make_tool("I could not parse this document.")
        result = json.loads(tool.run('{"text": "Some text"}'))
        assert "error" in result

    def test_handles_empty_text(self):
        llm = MockLLMProvider([])
        tool = ExtractDataTool(llm_provider=llm)
        result = json.loads(tool.run('{"text": ""}'))
        assert "error" in result

    def test_no_llm_provider_returns_error(self):
        tool = ExtractDataTool(llm_provider=None)
        result = json.loads(tool.run('{"text": "Invoice text"}'))
        assert "error" in result

    def test_llm_markdown_fences_stripped(self):
        fenced = "```json\n" + self._INVOICE_JSON + "\n```"
        tool = self._make_tool(fenced)
        result = json.loads(tool.run('{"text": "Invoice text"}'))
        assert "invoice_number" in result

    def test_zone_is_green(self):
        assert ExtractDataTool().zone == ToolZone.GREEN

    def test_line_items_is_list(self):
        tool = self._make_tool(self._INVOICE_JSON)
        result = json.loads(tool.run('{"text": "Invoice with items"}'))
        assert isinstance(result["line_items"], list)


# ---------------------------------------------------------------------------
# MoveFileTool tests
# ---------------------------------------------------------------------------

class TestMoveFileTool:
    def test_zone_is_yellow(self):
        assert MoveFileTool().zone == ToolZone.YELLOW

    def test_zone_is_always_yellow(self):
        """Zone must be YELLOW regardless of how the tool is constructed."""
        tool1 = MoveFileTool()
        tool2 = MoveFileTool(drive_service=MockDriveService())
        assert tool1.zone == ToolZone.YELLOW
        assert tool2.zone == ToolZone.YELLOW

    def test_raises_approval_required_via_agent(self):
        """BaseAgent must raise ApprovalRequired when LLM selects move_file."""
        mock_service = MockDriveService(
            file_meta={"parents": ["old_folder"]},
        )
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {
                    "name": "move_file",
                    "input": json.dumps({
                        "file_id": "file_001",
                        "destination_id": "folder_archived",
                        "file_name": "Invoice_2024.pdf",
                    }),
                },
            }
        ])
        agent = BaseAgent(
            name="TestAgent",
            llm_provider=llm,
            tools=[MoveFileTool(drive_service=mock_service)],
        )
        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Move the invoice to the archive folder")
        assert exc_info.value.tool_name == "move_file"

    def test_run_succeeds_after_approval(self):
        """run() executes the move when called directly (simulates post-approval)."""
        mock_service = MockDriveService(
            file_meta={"parents": ["folder_inbox"]},
        )
        tool = MoveFileTool(drive_service=mock_service)
        result = json.loads(tool.run(json.dumps({
            "file_id": "file_001",
            "destination_id": "folder_archived",
            "file_name": "Invoice_2024.pdf",
        })))
        assert result["status"] == "moved"
        assert result["file_id"] == "file_001"
        assert result["destination_id"] == "folder_archived"

    def test_run_records_previous_parents(self):
        mock_service = MockDriveService(
            file_meta={"parents": ["folder_inbox"]},
        )
        tool = MoveFileTool(drive_service=mock_service)
        result = json.loads(tool.run(json.dumps({
            "file_id": "file_001",
            "destination_id": "folder_archive",
        })))
        assert "previous_parents" in result
        assert result["previous_parents"] == ["folder_inbox"]

    def test_missing_file_id_raises_value_error(self):
        tool = MoveFileTool(drive_service=MockDriveService())
        with pytest.raises(ValueError, match="file_id"):
            tool.run(json.dumps({"destination_id": "folder_001"}))

    def test_missing_destination_raises_value_error(self):
        tool = MoveFileTool(drive_service=MockDriveService())
        with pytest.raises(ValueError, match="destination_id"):
            tool.run(json.dumps({"file_id": "file_001"}))

    def test_empty_input_raises_value_error(self):
        tool = MoveFileTool(drive_service=MockDriveService())
        with pytest.raises(ValueError):
            tool.run("")

    def test_api_error_raises_runtime_error(self):
        class ErrorService:
            def files(self):
                raise RuntimeError("Drive unavailable")
            def get(self, **kwargs):
                raise RuntimeError("Drive unavailable")
        tool = MoveFileTool(drive_service=ErrorService())
        with pytest.raises(RuntimeError):
            tool.run(json.dumps({"file_id": "f1", "destination_id": "d1"}))
