"""
Tests for ReportingAgent, GeneratePDFTool, and ExportReportTool.
All LLM calls use MockLLMProvider. Real PDF/JSON files written to /tmp/ and cleaned up.
"""

from __future__ import annotations

import json
import os

import pytest

from agents.reporting_agent import ReportingAgent
from core.tools.reporting.generate_pdf import GeneratePDFTool
from core.tools.reporting.export_report import ExportReportTool
from core.tools.base_tool import ToolZone
from core.base_agent import StepLimitReached
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# GeneratePDFTool tests
# ---------------------------------------------------------------------------

class TestGeneratePDFTool:
    _SAMPLE = {
        "title": "Monthly Finance Report — March 2024",
        "period": "monthly",
        "sections": [
            {"heading": "Invoice Summary", "content": "12 invoices. Total: USD 45,200.00."},
            {"heading": "Issues", "content": "No issues found."},
        ],
        "summary": "All invoices verified. No duplicates detected.",
        "filename": "test_report_march.pdf",
    }

    def test_creates_pdf_file(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps(self._SAMPLE)))
        assert result["status"] == "generated"
        assert os.path.exists(result["file_path"])
        os.remove(result["file_path"])

    def test_file_path_in_tmp(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps({**self._SAMPLE, "filename": "test_path.pdf"})))
        assert result["file_path"].startswith("/tmp/")
        os.remove(result["file_path"])

    def test_pdf_has_content(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps(self._SAMPLE)))
        file_path = result["file_path"]
        assert os.path.getsize(file_path) > 1000  # non-trivial PDF
        os.remove(file_path)

    def test_returns_title(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps(self._SAMPLE)))
        assert result["title"] == self._SAMPLE["title"]
        os.remove(result["file_path"])

    def test_page_count_is_positive_int(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps(self._SAMPLE)))
        assert isinstance(result["page_count"], int)
        assert result["page_count"] >= 1
        os.remove(result["file_path"])

    def test_default_filename_generated(self):
        tool = GeneratePDFTool()
        params = {k: v for k, v in self._SAMPLE.items() if k != "filename"}
        result = json.loads(tool.run(json.dumps(params)))
        assert result["file_path"].endswith(".pdf")
        os.remove(result["file_path"])

    def test_empty_sections_still_generates(self):
        tool = GeneratePDFTool()
        result = json.loads(tool.run(json.dumps({
            "title": "Empty Report",
            "period": "weekly",
            "sections": [],
            "filename": "test_empty.pdf",
        })))
        assert result["status"] == "generated"
        os.remove(result["file_path"])

    def test_zone_is_green(self):
        assert GeneratePDFTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# ExportReportTool tests
# ---------------------------------------------------------------------------

class TestExportReportTool:
    _SAMPLE = {
        "title": "Weekly Summary",
        "period": "weekly",
        "sections": [{"heading": "Overview", "content": "3 invoices processed."}],
        "summary": "All good.",
    }

    def test_exports_json(self):
        tool = ExportReportTool()
        result = json.loads(tool.run(json.dumps({
            **self._SAMPLE, "format": "json", "filename": "test_export.json"
        })))
        assert result["status"] == "exported"
        assert os.path.exists(result["file_path"])
        with open(result["file_path"]) as f:
            data = json.load(f)
        assert data["title"] == "Weekly Summary"
        os.remove(result["file_path"])

    def test_exports_text(self):
        tool = ExportReportTool()
        result = json.loads(tool.run(json.dumps({
            **self._SAMPLE, "format": "text", "filename": "test_export.txt"
        })))
        assert result["status"] == "exported"
        with open(result["file_path"]) as f:
            content = f.read()
        assert "Weekly Summary" in content
        os.remove(result["file_path"])

    def test_json_contains_sections(self):
        tool = ExportReportTool()
        result = json.loads(tool.run(json.dumps({
            **self._SAMPLE, "format": "json", "filename": "test_sections.json"
        })))
        with open(result["file_path"]) as f:
            data = json.load(f)
        assert "sections" in data
        assert len(data["sections"]) == 1
        os.remove(result["file_path"])

    def test_default_filename_json(self):
        tool = ExportReportTool()
        result = json.loads(tool.run(json.dumps({**self._SAMPLE, "format": "json"})))
        assert result["file_path"].endswith(".json")
        os.remove(result["file_path"])

    def test_format_returned_in_result(self):
        tool = ExportReportTool()
        result = json.loads(tool.run(json.dumps({
            **self._SAMPLE, "format": "text", "filename": "test_fmt.txt"
        })))
        assert result["format"] == "text"
        os.remove(result["file_path"])

    def test_zone_is_green(self):
        assert ExportReportTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# ReportingAgent tests
# ---------------------------------------------------------------------------

class TestReportingAgentInit:
    def test_agent_name(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert agent.name == "ReportingAgent"

    def test_has_generate_pdf_tool(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "generate_pdf" in agent._tools

    def test_has_export_report_tool(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "export_report" in agent._tools

    def test_has_calculator_tool(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "calculator" in agent._tools

    def test_all_tools_green(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        for name, tool in agent._tools.items():
            assert tool.zone == ToolZone.GREEN, f"{name} should be GREEN"

    def test_system_prompt_mentions_weekly(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "weekly" in agent._system_prompt().lower()

    def test_system_prompt_mentions_monthly(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "monthly" in agent._system_prompt().lower()

    def test_system_prompt_mentions_pdf(self):
        agent = ReportingAgent(llm_provider=MockLLMProvider([]))
        assert "pdf" in agent._system_prompt().lower()


class TestReportingAgentWorkflow:
    def test_generates_pdf_report(self):
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "generate_pdf", "input": json.dumps({
                "title": "Weekly Report",
                "period": "weekly",
                "sections": [{"heading": "Summary", "content": "3 invoices."}],
                "summary": "All good.",
                "filename": "test_agent_weekly.pdf",
            })}},
            {"content": "Weekly PDF report generated at /tmp/test_agent_weekly.pdf.", "tool_call": None},
        ])
        agent = ReportingAgent(llm_provider=llm)
        result = agent.run("Generate weekly report")
        assert result
        if os.path.exists("/tmp/test_agent_weekly.pdf"):
            os.remove("/tmp/test_agent_weekly.pdf")

    def test_exports_json_report(self):
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "export_report", "input": json.dumps({
                "title": "Monthly Summary",
                "period": "monthly",
                "sections": [{"heading": "Invoices", "content": "12 processed."}],
                "format": "json",
                "filename": "test_agent_monthly.json",
            })}},
            {"content": "Monthly report exported to /tmp/test_agent_monthly.json.", "tool_call": None},
        ])
        agent = ReportingAgent(llm_provider=llm)
        result = agent.run("Export monthly report as JSON")
        assert result
        if os.path.exists("/tmp/test_agent_monthly.json"):
            os.remove("/tmp/test_agent_monthly.json")

    def test_audit_log_populated(self):
        llm = MockLLMProvider([{"content": "Done.", "tool_call": None}])
        agent = ReportingAgent(llm_provider=llm)
        agent.run("Quick task")
        log = agent.get_audit_log()
        assert any(e["event_type"] == "task_completed" for e in log)

    def test_respects_max_steps(self):
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "generate_pdf", "input": "{}"}}
        ] * 10)
        agent = ReportingAgent(llm_provider=llm, max_steps=3)
        with pytest.raises(StepLimitReached):
            agent.run("Loop")
