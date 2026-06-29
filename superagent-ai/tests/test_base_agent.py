"""
Tests for BaseAgent — the core agent loop, zone enforcement,
audit logging, cost/step limits, and exception behaviour.

All tests use MockLLMProvider.  No real LLM or network calls are made.
"""

from __future__ import annotations

import pytest

from core.base_agent import (
    BaseAgent,
    ApprovalRequired,
    RedZoneBlocked,
    CostLimitReached,
    StepLimitReached,
)
from core.llm.mock_provider import MockLLMProvider
from core.tools.base_tool import BaseTool, ToolZone
from core.tools.echo import EchoTool
from core.tools.calculator import CalculatorTool


# ---------------------------------------------------------------------------
# Helpers — custom tools for zone testing
# ---------------------------------------------------------------------------


class YellowTool(BaseTool):
    """A YELLOW zone tool — always requires human approval."""

    name = "yellow_action"
    description = "A tool that requires human approval."
    zone = ToolZone.YELLOW

    def run(self, input_str: str) -> str:
        return f"yellow ran: {input_str}"


class RedTool(BaseTool):
    """A RED zone tool — agent must never execute this."""

    name = "red_action"
    description = "A tool that only humans may execute."
    zone = ToolZone.RED

    def run(self, input_str: str) -> str:
        return f"red ran: {input_str}"


def _make_agent(
    responses: list[dict],
    tools: list[BaseTool] | None = None,
    max_steps: int = 20,
    max_cost: float = 1.0,
) -> BaseAgent:
    """Factory: build a BaseAgent with a MockLLMProvider."""
    llm = MockLLMProvider(responses)
    return BaseAgent(
        name="TestAgent",
        llm_provider=llm,
        tools=tools or [EchoTool()],
        max_steps=max_steps,
        max_cost=max_cost,
        task_id="test-task-001",
    )


# ---------------------------------------------------------------------------
# Happy-path: simple task completion
# ---------------------------------------------------------------------------


class TestBaseAgentHappyPath:
    """Tests for normal task completion flow."""

    def test_completes_task_without_tool_call(self) -> None:
        """Agent returns LLM response immediately when no tool call is needed."""
        responses = [
            {"content": "The answer is 42.", "tool_call": None, "tokens_used": 100, "cost_usd": 0.0},
        ]
        agent = _make_agent(responses)
        result = agent.run("What is the answer?")
        assert result == "The answer is 42."

    def test_completes_task_after_tool_call(self) -> None:
        """Agent calls EchoTool, gets result, then returns final answer."""
        responses = [
            # Step 1: LLM requests echo tool
            {
                "content": "",
                "tool_call": {"name": "echo", "input": "hello"},
                "tokens_used": 100,
                "cost_usd": 0.0,
            },
            # Step 2: LLM sees tool result and returns final answer
            {
                "content": "I echoed 'hello' successfully.",
                "tool_call": None,
                "tokens_used": 80,
                "cost_usd": 0.0,
            },
        ]
        agent = _make_agent(responses)
        result = agent.run("Echo 'hello' for me.")
        assert result == "I echoed 'hello' successfully."

    def test_uses_calculator_tool(self) -> None:
        """Agent calls CalculatorTool and uses the result."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "calculator", "input": "1200 + 800"},
                "tokens_used": 100,
                "cost_usd": 0.0,
            },
            {
                "content": "The total is 2000.",
                "tool_call": None,
                "tokens_used": 80,
                "cost_usd": 0.0,
            },
        ]
        agent = _make_agent(responses, tools=[CalculatorTool()])
        result = agent.run("What is 1200 + 800?")
        assert result == "The total is 2000."

    def test_returns_string(self) -> None:
        """run() always returns a string."""
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        result = agent.run("Do something.")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Step limit
# ---------------------------------------------------------------------------


class TestStepLimit:
    """Tests for max_steps enforcement."""

    def test_raises_step_limit_reached(self) -> None:
        """Agent raises StepLimitReached when it exceeds max_steps."""
        # Every LLM response requests the echo tool → infinite loop
        # We supply more responses than max_steps to ensure the limit fires
        tool_response = {
            "content": "",
            "tool_call": {"name": "echo", "input": "ping"},
            "tokens_used": 10,
            "cost_usd": 0.0,
        }
        responses = [tool_response] * 25  # more than max_steps=3
        agent = _make_agent(responses, max_steps=3)

        with pytest.raises(StepLimitReached) as exc_info:
            agent.run("Loop forever.")

        assert exc_info.value.max_steps == 3
        assert exc_info.value.steps_taken > 3

    def test_step_limit_logs_event(self) -> None:
        """Audit log contains step_limit_reached event."""
        tool_response = {
            "content": "",
            "tool_call": {"name": "echo", "input": "ping"},
            "tokens_used": 10,
            "cost_usd": 0.0,
        }
        responses = [tool_response] * 10
        agent = _make_agent(responses, max_steps=2)

        with pytest.raises(StepLimitReached):
            agent.run("Loop.")

        event_types = [e["event_type"] for e in agent.audit_log]
        assert "step_limit_reached" in event_types


# ---------------------------------------------------------------------------
# Cost limit
# ---------------------------------------------------------------------------


class TestCostLimit:
    """Tests for max_cost enforcement."""

    def test_raises_cost_limit_reached(self) -> None:
        """Agent raises CostLimitReached when cumulative cost exceeds max_cost."""
        # Each call costs $0.40, max_cost is $0.50 → second call triggers limit
        expensive_response = {
            "content": "",
            "tool_call": {"name": "echo", "input": "test"},
            "tokens_used": 1000,
            "cost_usd": 0.40,
        }
        responses = [expensive_response] * 5
        agent = _make_agent(responses, max_cost=0.50)

        with pytest.raises(CostLimitReached) as exc_info:
            agent.run("Do expensive work.")

        assert exc_info.value.max_cost == 0.50
        assert exc_info.value.cost_so_far > 0.50

    def test_cost_limit_logs_event(self) -> None:
        """Audit log contains cost_limit_reached event."""
        expensive_response = {
            "content": "",
            "tool_call": {"name": "echo", "input": "test"},
            "tokens_used": 1000,
            "cost_usd": 0.60,
        }
        responses = [expensive_response] * 5
        agent = _make_agent(responses, max_cost=0.50)

        with pytest.raises(CostLimitReached):
            agent.run("Expensive.")

        event_types = [e["event_type"] for e in agent.audit_log]
        assert "cost_limit_reached" in event_types


# ---------------------------------------------------------------------------
# Zone enforcement
# ---------------------------------------------------------------------------


class TestZoneEnforcement:
    """Tests for YELLOW and RED zone tool enforcement."""

    def test_raises_approval_required_for_yellow_tool(self) -> None:
        """Agent raises ApprovalRequired when LLM selects a YELLOW zone tool."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "yellow_action", "input": "do it"},
                "tokens_used": 50,
                "cost_usd": 0.0,
            }
        ]
        agent = _make_agent(responses, tools=[YellowTool()])

        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Perform the yellow action.")

        assert exc_info.value.tool_name == "yellow_action"
        assert exc_info.value.tool_input == "do it"

    def test_approval_required_cannot_be_bypassed(self) -> None:
        """Yellow zone: even with multiple responses, ApprovalRequired is always raised."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "yellow_action", "input": "first call"},
                "tokens_used": 50,
                "cost_usd": 0.0,
            },
            {
                "content": "Done anyway.",
                "tool_call": None,
                "tokens_used": 50,
                "cost_usd": 0.0,
            },
        ]
        agent = _make_agent(responses, tools=[YellowTool()])
        with pytest.raises(ApprovalRequired):
            agent.run("Try to bypass approval.")

    def test_raises_red_zone_blocked_for_red_tool(self) -> None:
        """Agent raises RedZoneBlocked when LLM selects a RED zone tool."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "red_action", "input": "execute"},
                "tokens_used": 50,
                "cost_usd": 0.0,
            }
        ]
        agent = _make_agent(responses, tools=[RedTool()])

        with pytest.raises(RedZoneBlocked) as exc_info:
            agent.run("Execute the red action.")

        assert exc_info.value.tool_name == "red_action"

    def test_approval_needed_logged_for_yellow(self) -> None:
        """Audit log contains approval_needed event for YELLOW tool."""
        responses = [
            {
                "content": "",
                "tool_call": {"name": "yellow_action", "input": "go"},
                "tokens_used": 50,
                "cost_usd": 0.0,
            }
        ]
        agent = _make_agent(responses, tools=[YellowTool()])

        with pytest.raises(ApprovalRequired):
            agent.run("Yellow task.")

        event_types = [e["event_type"] for e in agent.audit_log]
        assert "approval_needed" in event_types


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Tests for audit log correctness."""

    def test_audit_log_contains_task_started(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        agent.run("Start task.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "task_started" in types

    def test_audit_log_contains_task_completed(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        agent.run("Complete task.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "task_completed" in types

    def test_audit_log_contains_tool_called_and_result(self) -> None:
        responses = [
            {"content": "", "tool_call": {"name": "echo", "input": "test"}, "tokens_used": 50, "cost_usd": 0.0},
            {"content": "Done.", "tool_call": None},
        ]
        agent = _make_agent(responses)
        agent.run("Echo test.")
        types = [e["event_type"] for e in agent.audit_log]
        assert "tool_called" in types
        assert "tool_result" in types

    def test_audit_log_entries_have_required_keys(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        agent.run("Any task.")
        for entry in agent.audit_log:
            assert "timestamp" in entry
            assert "event_type" in entry
            assert "details" in entry
            assert "step_number" in entry
            assert "cost_so_far" in entry

    def test_get_audit_log_returns_list(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        agent.run("Task.")
        log = agent.get_audit_log()
        assert isinstance(log, list)
        assert len(log) > 0

    def test_audit_log_is_reset_between_runs(self) -> None:
        """Each call to run() starts with a fresh audit log."""
        responses1 = [{"content": "First.", "tool_call": None}]
        responses2 = [{"content": "Second.", "tool_call": None}]

        agent = BaseAgent(
            name="TestAgent",
            llm_provider=MockLLMProvider(responses1),
            tools=[EchoTool()],
        )
        agent.run("First task.")
        count_after_first = len(agent.audit_log)

        # Replace provider with fresh responses for second run
        agent._llm = MockLLMProvider(responses2)
        agent.run("Second task.")
        count_after_second = len(agent.audit_log)

        # Second run should have its own entries, not accumulated from first
        assert count_after_second == count_after_first


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------


class TestCostSummary:
    """Tests for get_cost_summary()."""

    def test_cost_summary_zero_for_free_responses(self) -> None:
        responses = [{"content": "Done.", "tool_call": None, "cost_usd": 0.0}]
        agent = _make_agent(responses)
        agent.run("Free task.")
        summary = agent.get_cost_summary()
        assert summary["total_cost_usd"] == 0.0
        assert summary["total_steps"] == 1

    def test_cost_summary_accumulates_correctly(self) -> None:
        responses = [
            {"content": "", "tool_call": {"name": "echo", "input": "x"}, "tokens_used": 100, "cost_usd": 0.001},
            {"content": "Done.", "tool_call": None, "cost_usd": 0.002},
        ]
        agent = _make_agent(responses)
        agent.run("Paid task.")
        summary = agent.get_cost_summary()
        assert abs(summary["total_cost_usd"] - 0.003) < 1e-6
        assert summary["total_steps"] == 2

    def test_cost_summary_returns_dict(self) -> None:
        responses = [{"content": "Done.", "tool_call": None}]
        agent = _make_agent(responses)
        agent.run("Any.")
        summary = agent.get_cost_summary()
        assert isinstance(summary, dict)
        assert "total_cost_usd" in summary
        assert "total_steps" in summary
