"""
Tests for QAAgent, ReviewDraftTool, VerifyNumbersTool, FlagIssueTool.
All LLM calls use MockLLMProvider. No real API calls are made.
"""

from __future__ import annotations

import json

import pytest

from agents.qa_agent import QAAgent
from core.tools.qa.review_draft import ReviewDraftTool
from core.tools.qa.verify_numbers import VerifyNumbersTool
from core.tools.qa.flag_issue import FlagIssueTool
from core.tools.base_tool import ToolZone
from core.base_agent import StepLimitReached
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# ReviewDraftTool tests
# ---------------------------------------------------------------------------

class TestReviewDraftTool:
    def _tool(self):
        return ReviewDraftTool()

    def test_passes_good_draft(self):
        draft = "Invoice summary: 12 invoices processed. Total: USD 45,200.00. All verified."
        result = json.loads(self._tool().run(json.dumps({"draft": draft})))
        assert result["passed"] is True
        assert result["issues"] == []

    def test_fails_short_draft(self):
        result = json.loads(self._tool().run(json.dumps({
            "draft": "Too short",
            "min_length": 50,
        })))
        assert result["passed"] is False
        assert any("short" in issue.lower() for issue in result["issues"])

    def test_detects_todo_placeholder(self):
        draft = "Invoice summary: [TODO] fill in total amount here."
        result = json.loads(self._tool().run(json.dumps({"draft": draft})))
        assert result["passed"] is False
        assert len(result["placeholders_found"]) > 0

    def test_detects_insert_placeholder(self):
        draft = "Summary: [INSERT] recommendation here. Otherwise complete."
        result = json.loads(self._tool().run(json.dumps({"draft": draft})))
        assert result["passed"] is False

    def test_detects_lorem_ipsum(self):
        draft = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod."
        result = json.loads(self._tool().run(json.dumps({"draft": draft})))
        assert result["passed"] is False

    def test_detects_missing_required_section(self):
        draft = "Invoice summary complete. Costs noted."
        result = json.loads(self._tool().run(json.dumps({
            "draft": draft,
            "required_sections": ["Recommendations"],
        })))
        assert result["passed"] is False
        assert any("Recommendations" in issue for issue in result["issues"])

    def test_passes_when_required_section_present(self):
        draft = "Recommendations: Pay outstanding invoices by end of month. Summary: all good."
        result = json.loads(self._tool().run(json.dumps({
            "draft": draft,
            "required_sections": ["Recommendations"],
        })))
        assert result["passed"] is True

    def test_returns_word_count(self):
        draft = "This is a five word draft that is longer than the minimum requirement for passing."
        result = json.loads(self._tool().run(json.dumps({"draft": draft})))
        assert "word_count" in result
        assert result["word_count"] > 0

    def test_plain_string_input(self):
        draft = "A sufficiently long draft text that should pass the basic review check easily."
        result = json.loads(self._tool().run(draft))
        assert "passed" in result

    def test_zone_is_green(self):
        assert ReviewDraftTool().zone == ToolZone.GREEN


# ---------------------------------------------------------------------------
# VerifyNumbersTool tests
# ---------------------------------------------------------------------------

class TestVerifyNumbersTool:
    def _tool(self):
        return VerifyNumbersTool()

    def test_finds_exact_match(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Total invoices: 12. Grand total: USD 45200.00.",
            "expected": {"grand_total": 45200.00},
        })))
        assert result["passed"] is True
        assert result["checks"][0]["passed"] is True

    def test_detects_mismatch(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Total: 100.00",
            "expected": {"total": 999.00},
        })))
        assert result["passed"] is False
        assert len(result["issues"]) > 0

    def test_extracts_numbers_from_text(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Invoices: 5. Amount: $1,250.00. Tax: $125.00.",
            "expected": {},
        })))
        assert "numbers_found" in result
        assert len(result["numbers_found"]) > 0

    def test_tolerance_applied(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Total: 100.00",
            "expected": {"total": 100.005},
            "tolerance": 0.01,
        })))
        assert result["passed"] is True

    def test_multiple_expected_values(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Count: 12. Total: 500.00. Tax: 50.00.",
            "expected": {"count": 12, "total": 500.00, "tax": 50.00},
        })))
        assert len(result["checks"]) == 3

    def test_empty_expected_no_error(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Some text with 100.00 in it.",
            "expected": {},
        })))
        assert result["passed"] is True
        assert result["issues"] == []

    def test_zone_is_green(self):
        assert VerifyNumbersTool().zone == ToolZone.GREEN

    def test_returns_checks_list(self):
        result = json.loads(self._tool().run(json.dumps({
            "text": "Total: 250.00",
            "expected": {"total": 250.00},
        })))
        assert isinstance(result["checks"], list)


# ---------------------------------------------------------------------------
# FlagIssueTool tests
# ---------------------------------------------------------------------------

class TestFlagIssueTool:
    def _tool(self, log=None):
        return FlagIssueTool(issue_log=log)

    def test_flags_number_mismatch(self):
        result = json.loads(self._tool().run(json.dumps({
            "source_agent": "FinanceAgent",
            "issue_type": "number_mismatch",
            "severity": "critical",
            "description": "Invoice total $500 does not match line items $480.",
        })))
        assert result["status"] == "flagged"
        assert result["issue_type"] == "number_mismatch"
        assert result["severity"] == "critical"

    def test_result_contains_issue_id(self):
        result = json.loads(self._tool().run(json.dumps({
            "issue_type": "format_error",
            "severity": "low",
            "description": "Missing currency code.",
        })))
        assert "issue_id" in result
        assert result["issue_id"].startswith("QA-")

    def test_result_contains_timestamp(self):
        result = json.loads(self._tool().run(json.dumps({
            "issue_type": "logic_error",
            "severity": "high",
            "description": "Total does not include tax.",
        })))
        assert "timestamp" in result

    def test_appends_to_issue_log(self):
        log: list = []
        tool = self._tool(log=log)
        tool.run(json.dumps({
            "issue_type": "missing_section",
            "severity": "medium",
            "description": "Section 'Recommendations' is missing.",
        }))
        assert len(log) == 1
        assert log[0]["issue_type"] == "missing_section"

    def test_multiple_flags_accumulated(self):
        log: list = []
        tool = self._tool(log=log)
        for i in range(3):
            tool.run(json.dumps({
                "issue_type": "format_error",
                "severity": "low",
                "description": f"Issue {i}",
            }))
        assert len(log) == 3

    def test_invalid_issue_type_returns_error(self):
        result = json.loads(self._tool().run(json.dumps({
            "issue_type": "bad_type",
            "severity": "low",
            "description": "Test.",
        })))
        assert "error" in result

    def test_invalid_severity_returns_error(self):
        result = json.loads(self._tool().run(json.dumps({
            "issue_type": "format_error",
            "severity": "extreme",
            "description": "Test.",
        })))
        assert "error" in result

    def test_missing_description_returns_error(self):
        result = json.loads(self._tool().run(json.dumps({
            "issue_type": "format_error",
            "severity": "low",
        })))
        assert "error" in result

    def test_zone_is_green(self):
        assert FlagIssueTool().zone == ToolZone.GREEN

    def test_all_issue_types_accepted(self):
        from core.tools.qa.flag_issue import _ISSUE_TYPES
        for itype in _ISSUE_TYPES:
            result = json.loads(self._tool().run(json.dumps({
                "issue_type": itype,
                "severity": "low",
                "description": f"Test for {itype}",
            })))
            assert result["status"] == "flagged"


# ---------------------------------------------------------------------------
# QAAgent tests
# ---------------------------------------------------------------------------

class TestQAAgentInit:
    def test_agent_name(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert agent.name == "QAAgent"

    def test_has_review_draft_tool(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert "review_draft" in agent._tools

    def test_has_verify_numbers_tool(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert "verify_numbers" in agent._tools

    def test_has_flag_issue_tool(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert "flag_issue" in agent._tools

    def test_all_tools_are_green(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        for name, tool in agent._tools.items():
            assert tool.zone == ToolZone.GREEN, f"{name} should be GREEN"

    def test_default_max_steps_is_10(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert agent.max_steps == 10

    def test_system_prompt_mentions_finance_agent(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert "FinanceAgent" in agent._system_prompt()

    def test_system_prompt_mentions_qa_passed(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert "QA PASSED" in agent._system_prompt()

    def test_issue_log_initialised_empty(self):
        agent = QAAgent(llm_provider=MockLLMProvider([]))
        assert agent.issue_log == []

    def test_shared_issue_log_injected(self):
        shared_log: list = []
        agent = QAAgent(llm_provider=MockLLMProvider([]), issue_log=shared_log)
        assert agent.issue_log is shared_log


class TestQAAgentWorkflow:
    def test_reviews_clean_draft(self):
        draft = "Invoice summary complete. Total: USD 500.00. All verified. No issues."
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "review_draft", "input": json.dumps({
                "draft": draft,
                "min_length": 20,
            })}},
            {"content": "QA PASSED — no issues found.", "tool_call": None},
        ])
        agent = QAAgent(llm_provider=llm)
        result = agent.run(f"Review this output: {draft}")
        assert result

    def test_flags_issue_when_found(self):
        issue_log: list = []
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "flag_issue", "input": json.dumps({
                "source_agent": "FinanceAgent",
                "issue_type": "number_mismatch",
                "severity": "critical",
                "description": "Total $500 does not match line items $480.",
            })}},
            {"content": "QA FAILED — 1 critical issue flagged.", "tool_call": None},
        ])
        agent = QAAgent(llm_provider=llm, issue_log=issue_log)
        result = agent.run("Review finance output with errors")
        assert result
        assert len(issue_log) == 1
        assert issue_log[0]["severity"] == "critical"

    def test_verifies_numbers(self):
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "verify_numbers", "input": json.dumps({
                "text": "Total invoices: 5. Grand total: USD 2500.00.",
                "expected": {"grand_total": 2500.00},
            })}},
            {"content": "QA PASSED — numbers verified.", "tool_call": None},
        ])
        agent = QAAgent(llm_provider=llm)
        result = agent.run("Verify these numbers")
        assert result

    def test_audit_log_recorded(self):
        llm = MockLLMProvider([{"content": "QA PASSED.", "tool_call": None}])
        agent = QAAgent(llm_provider=llm)
        agent.run("Quick QA check")
        log = agent.get_audit_log()
        assert any(e["event_type"] == "task_completed" for e in log)

    def test_respects_max_steps(self):
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "review_draft", "input": '{"draft": "test draft text here"}'}}
        ] * 15)
        agent = QAAgent(llm_provider=llm, max_steps=3)
        with pytest.raises(StepLimitReached):
            agent.run("Loop forever")
