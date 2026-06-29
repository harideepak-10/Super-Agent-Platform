"""
Tests for EmailAgent.

Verifies initialisation, task execution with MockLLMProvider,
and audit log population.  No real LLM or network calls are made.
"""

from __future__ import annotations

import pytest

from agents.email_agent import EmailAgent
from core.llm.mock_provider import MockLLMProvider
from core.base_agent import ApprovalRequired


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_email_agent(responses: list[dict]) -> EmailAgent:
    """Build an EmailAgent backed by a MockLLMProvider."""
    llm = MockLLMProvider(responses)
    return EmailAgent(llm_provider=llm)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestEmailAgentInit:
    """Tests for EmailAgent initialisation."""

    def test_name_is_email_agent(self) -> None:
        agent = _make_email_agent([])
        assert agent.name == "EmailAgent"

    def test_max_steps_is_20(self) -> None:
        # Raised from 15 to 20 to support the extended 7-step workflow
        agent = _make_email_agent([])
        assert agent.max_steps == 20

    def test_max_cost_is_50_cents(self) -> None:
        agent = _make_email_agent([])
        assert agent.max_cost == 0.50

    def test_has_calculator_tool(self) -> None:
        agent = _make_email_agent([])
        assert "calculator" in agent._tools

    def test_has_current_time_tool(self) -> None:
        agent = _make_email_agent([])
        assert "current_time" in agent._tools

    def test_has_echo_tool(self) -> None:
        agent = _make_email_agent([])
        assert "echo" in agent._tools

    def test_system_prompt_mentions_email(self) -> None:
        agent = _make_email_agent([])
        prompt = agent._system_prompt()
        assert "email" in prompt.lower()

    def test_system_prompt_mentions_no_send_without_approval(self) -> None:
        agent = _make_email_agent([])
        prompt = agent._system_prompt()
        # The prompt must mention that sending requires approval
        lower = prompt.lower()
        assert "approval" in lower or "human" in lower

    def test_task_id_auto_generated(self) -> None:
        agent = _make_email_agent([])
        assert agent.task_id is not None
        assert len(agent.task_id) > 0

    def test_task_id_can_be_supplied(self) -> None:
        llm = MockLLMProvider([])
        agent = EmailAgent(llm_provider=llm, task_id="my-custom-id")
        assert agent.task_id == "my-custom-id"


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------


class TestEmailAgentRun:
    """Tests for EmailAgent.run() with MockLLMProvider."""

    def test_simple_task_returns_string(self) -> None:
        responses = [
            {"content": "This is an invoice email from ACME Corp.", "tool_call": None}
        ]
        agent = _make_email_agent(responses)
        result = agent.run("Classify this email: 'Please find attached invoice #1042.'")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_task_with_echo_tool(self) -> None:
        """Agent calls echo tool then returns final answer."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "echo", "input": "test classification"},
                "tokens_used": 80,
                "cost_usd": 0.0,
            },
            {
                "content": "Email classified as: test.",
                "tool_call": None,
            },
        ]
        agent = _make_email_agent(responses)
        result = agent.run("Classify this email.")
        assert result == "Email classified as: test."

    def test_task_with_calculator_tool(self) -> None:
        """Agent calculates invoice total using calculator tool."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "calculator", "input": "500 + 250 + 100"},
                "tokens_used": 100,
                "cost_usd": 0.0,
            },
            {
                "content": "The invoice total is $850.",
                "tool_call": None,
            },
        ]
        agent = _make_email_agent(responses)
        result = agent.run("Calculate the total for invoice items: $500, $250, $100.")
        assert "850" in result

    def test_task_with_current_time_tool(self) -> None:
        """Agent retrieves current time, then responds."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "current_time", "input": ""},
                "tokens_used": 60,
                "cost_usd": 0.0,
            },
            {
                "content": "The reply has been scheduled.",
                "tool_call": None,
            },
        ]
        agent = _make_email_agent(responses)
        result = agent.run("Schedule a reply for this email.")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestEmailAgentAuditLog:
    """Tests for EmailAgent audit log population."""

    def test_audit_log_populated_after_run(self) -> None:
        responses = [{"content": "Classified.", "tool_call": None}]
        agent = _make_email_agent(responses)
        agent.run("Classify email.")
        assert len(agent.audit_log) > 0

    def test_audit_log_contains_task_started(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_email_agent(responses)
        agent.run("Task.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "task_started" in types

    def test_audit_log_contains_task_completed(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_email_agent(responses)
        agent.run("Task.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "task_completed" in types

    def test_audit_log_contains_tool_events_when_tool_used(self) -> None:
        responses = [
            {
                "content": "",
                "tool_call": {"name": "echo", "input": "check"},
                "tokens_used": 50,
                "cost_usd": 0.0,
            },
            {"content": "Done.", "tool_call": None},
        ]
        agent = _make_email_agent(responses)
        agent.run("Use echo.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "tool_called" in types
        assert "tool_result" in types

    def test_audit_log_entries_have_correct_structure(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_email_agent(responses)
        agent.run("Structural check.")
        for entry in agent.audit_log:
            assert "timestamp" in entry
            assert "event_type" in entry
            assert "details" in entry
            assert "step_number" in entry
            assert "cost_so_far" in entry

    def test_cost_summary_after_run(self) -> None:
        responses = [
            {
                "content": "",
                "tool_call": {"name": "echo", "input": "x"},
                "tokens_used": 100,
                "cost_usd": 0.001,
            },
            {"content": "Done.", "tool_call": None, "cost_usd": 0.001},
        ]
        agent = _make_email_agent(responses)
        agent.run("Cost check.")
        summary = agent.get_cost_summary()
        assert summary["total_cost_usd"] >= 0.001
        assert summary["total_steps"] >= 1
