"""
Tests for the Orchestrator — routing, approval handling, retries, escalation.
All agent LLM calls use MockLLMProvider. No real API calls.
"""

from __future__ import annotations

import json

import pytest

from agents.orchestrator import (
    Orchestrator,
    OrchestrationStatus,
    OrchestrationStep,
    OrchestrationState,
    StepStatus,
    _ROUTING_HINTS,
)
from agents.finance_agent import FinanceAgent
from agents.qa_agent import QAAgent
from agents.reporting_agent import ReportingAgent
from agents.document_agent import DocumentAgent
from core.base_agent import (
    ApprovalRequired,
    BaseAgent,
    StepLimitReached,
)
from core.tools.base_tool import BaseTool, ToolZone
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_invoice_store():
    """Minimal mock invoice store for FinanceAgent."""
    class MockStore:
        def all(self): return []
        def filter(self, **kw): return []
        def get(self, id): return None
        def flag(self, *a, **kw): pass
    return MockStore()


def _make_finance_agent(responses):
    store = _mock_invoice_store()
    return FinanceAgent(llm_provider=MockLLMProvider(responses), invoice_store=store)


def _make_qa_agent(responses, issue_log=None):
    return QAAgent(llm_provider=MockLLMProvider(responses), issue_log=issue_log)


def _make_reporting_agent(responses):
    return ReportingAgent(llm_provider=MockLLMProvider(responses))


class AlwaysFailAgent(BaseAgent):
    """Agent whose LLM always raises an exception (simulates tool error)."""
    def __init__(self, fail_times: int = 1):
        self._fail_times = fail_times
        self._calls = 0
        # Minimal init without calling super (we override run)
        from core.llm.mock_provider import MockLLMProvider
        super().__init__(
            name="AlwaysFailAgent",
            llm_provider=MockLLMProvider([]),
            tools=[],
        )

    def run(self, task, initial_messages=None):
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError(f"Simulated failure #{self._calls}")
        return "Recovered successfully"


# ---------------------------------------------------------------------------
# Orchestrator — init and routing
# ---------------------------------------------------------------------------

class TestOrchestratorInit:
    def test_registers_agents(self):
        llm = MockLLMProvider([])
        qa = _make_qa_agent([])
        orch = Orchestrator(agents={"QAAgent": qa})
        assert "QAAgent" in orch._agents

    def test_default_max_retries(self):
        orch = Orchestrator(agents={})
        assert orch.max_retries == 3

    def test_custom_max_retries(self):
        orch = Orchestrator(agents={}, max_retries=5)
        assert orch.max_retries == 5


class TestOrchestratorRouting:
    def _orch(self):
        return Orchestrator(agents={
            "FinanceAgent": _make_finance_agent([]),
            "QAAgent": _make_qa_agent([]),
            "ReportingAgent": _make_reporting_agent([]),
        })

    def test_routes_invoice_to_finance_agent(self):
        orch = self._orch()
        agent_name = orch._route("check invoice payments for this month")
        assert agent_name == "FinanceAgent"

    def test_routes_report_to_reporting_agent(self):
        orch = self._orch()
        agent_name = orch._route("generate monthly report summary")
        assert agent_name == "ReportingAgent"

    def test_routes_qa_keywords(self):
        orch = self._orch()
        agent_name = orch._route("review and verify the output quality")
        assert agent_name == "QAAgent"

    def test_falls_back_to_first_agent_when_no_match(self):
        orch = Orchestrator(agents={"QAAgent": _make_qa_agent([])})
        agent_name = orch._route("completely unrelated task xyz")
        assert agent_name == "QAAgent"

    def test_only_routes_to_registered_agents(self):
        # EmailAgent not registered — routing should skip it
        orch = Orchestrator(agents={"QAAgent": _make_qa_agent([])})
        agent_name = orch._route("send email to vendor")
        assert agent_name == "QAAgent"  # fallback


# ---------------------------------------------------------------------------
# Orchestrator — planning (auto QA step)
# ---------------------------------------------------------------------------

class TestOrchestratorPlanning:
    def test_finance_task_gets_qa_step(self):
        orch = Orchestrator(agents={
            "FinanceAgent": _make_finance_agent([]),
            "QAAgent": _make_qa_agent([]),
        })
        steps = orch._plan("process invoice payments")
        assert len(steps) == 2
        assert steps[0].agent_name == "FinanceAgent"
        assert steps[1].agent_name == "QAAgent"

    def test_reporting_task_gets_qa_step(self):
        orch = Orchestrator(agents={
            "ReportingAgent": _make_reporting_agent([]),
            "QAAgent": _make_qa_agent([]),
        })
        steps = orch._plan("generate monthly report")
        assert len(steps) == 2
        assert steps[1].agent_name == "QAAgent"

    def test_no_qa_if_qa_agent_not_registered(self):
        orch = Orchestrator(agents={
            "FinanceAgent": _make_finance_agent([]),
        })
        steps = orch._plan("check invoices")
        assert len(steps) == 1

    def test_qa_task_does_not_get_extra_qa_step(self):
        orch = Orchestrator(agents={"QAAgent": _make_qa_agent([])})
        steps = orch._plan("review output quality")
        assert len(steps) == 1

    def test_qa_step_has_previous_result_template(self):
        orch = Orchestrator(agents={
            "FinanceAgent": _make_finance_agent([]),
            "QAAgent": _make_qa_agent([]),
        })
        steps = orch._plan("check invoices")
        assert "{previous_result}" in steps[1].task


# ---------------------------------------------------------------------------
# Orchestrator — run (happy path)
# ---------------------------------------------------------------------------

class TestOrchestratorRun:
    def test_completes_single_step(self):
        qa = _make_qa_agent([{"content": "QA PASSED.", "tool_call": None}])
        orch = Orchestrator(agents={"QAAgent": qa})
        state = orch.run("review output quality")
        assert state.status == OrchestrationStatus.COMPLETED
        assert state.final_result == "QA PASSED."

    def test_step_marked_completed(self):
        qa = _make_qa_agent([{"content": "Done.", "tool_call": None}])
        orch = Orchestrator(agents={"QAAgent": qa})
        state = orch.run("verify quality")
        assert state.steps[0].status == StepStatus.COMPLETED

    def test_run_id_generated(self):
        qa = _make_qa_agent([{"content": "Done.", "tool_call": None}])
        orch = Orchestrator(agents={"QAAgent": qa})
        state = orch.run("verify quality")
        assert state.run_id

    def test_two_step_finance_qa_pipeline(self):
        store = _mock_invoice_store()
        finance = FinanceAgent(
            llm_provider=MockLLMProvider([{"content": "Invoice summary done.", "tool_call": None}]),
            invoice_store=store,
        )
        qa = _make_qa_agent([{"content": "QA PASSED.", "tool_call": None}])
        orch = Orchestrator(agents={"FinanceAgent": finance, "QAAgent": qa})
        state = orch.run("check invoice totals")
        assert state.status == OrchestrationStatus.COMPLETED
        assert len(state.steps) == 2
        assert state.steps[0].status == StepStatus.COMPLETED
        assert state.steps[1].status == StepStatus.COMPLETED

    def test_qa_step_receives_previous_result(self):
        store = _mock_invoice_store()
        finance = FinanceAgent(
            llm_provider=MockLLMProvider([{"content": "Total: USD 5000.00", "tool_call": None}]),
            invoice_store=store,
        )
        received_tasks: list[str] = []

        class SpyQA(QAAgent):
            def run(self, task, initial_messages=None):
                received_tasks.append(task)
                return "QA PASSED."

        qa = SpyQA(llm_provider=MockLLMProvider([]))
        orch = Orchestrator(agents={"FinanceAgent": finance, "QAAgent": qa})
        orch.run("check invoice totals")
        assert "Total: USD 5000.00" in received_tasks[0]

    def test_state_to_dict_has_expected_keys(self):
        qa = _make_qa_agent([{"content": "Done.", "tool_call": None}])
        orch = Orchestrator(agents={"QAAgent": qa})
        state = orch.run("verify quality")
        d = state.to_dict()
        assert "run_id" in d
        assert "status" in d
        assert "steps" in d
        assert "final_result" in d

    def test_unregistered_agent_fails_gracefully(self):
        orch = Orchestrator(agents={})
        # _plan falls back to first agent — but none registered
        # Manually inject a step with unknown agent
        state = OrchestrationState(
            run_id="test",
            original_task="test",
            steps=[OrchestrationStep(step_id="s1", agent_name="GhostAgent", task="test")],
        )
        result = orch._execute(state)
        assert result.status == OrchestrationStatus.FAILED


# ---------------------------------------------------------------------------
# Orchestrator — ApprovalRequired handling
# ---------------------------------------------------------------------------

class TestOrchestratorApproval:
    def _build_approval_state(self) -> tuple[Orchestrator, OrchestrationState]:
        """Helper that constructs a state paused on YELLOW tool."""
        store = _mock_invoice_store()
        finance = FinanceAgent(
            llm_provider=MockLLMProvider([
                {"content": "", "tool_call": {"name": "flag_invoice", "input": json.dumps({
                    "invoice_id": "INV-001",
                    "reason": "duplicate",
                })}}
            ]),
            invoice_store=store,
        )
        orch = Orchestrator(agents={"FinanceAgent": finance})
        state = orch.run("flag duplicate invoice INV-001")
        return orch, state

    def test_run_pauses_on_approval_required(self):
        _, state = self._build_approval_state()
        assert state.status == OrchestrationStatus.WAITING_APPROVAL

    def test_step_status_is_waiting_approval(self):
        _, state = self._build_approval_state()
        assert state.steps[state.current_step_index].status == StepStatus.WAITING_APPROVAL

    def test_approval_state_saved_on_step(self):
        _, state = self._build_approval_state()
        step = state.steps[state.current_step_index]
        assert step.approval_state is not None
        assert step.approval_state["tool_name"] == "flag_invoice"

    def test_resume_invalid_state_raises(self):
        qa = _make_qa_agent([{"content": "Done.", "tool_call": None}])
        orch = Orchestrator(agents={"QAAgent": qa})
        completed_state = orch.run("verify quality")
        with pytest.raises(ValueError, match="not 'waiting_approval'"):
            orch.resume(completed_state, "approved result")

    def test_resume_completes_after_approval(self):
        store = _mock_invoice_store()
        # First run: agent calls flag_invoice (YELLOW) → pauses
        # After approval: agent gets the result injected and finishes
        finance = FinanceAgent(
            llm_provider=MockLLMProvider([
                {"content": "", "tool_call": {"name": "flag_invoice", "input": json.dumps({
                    "invoice_id": "INV-001",
                    "reason": "duplicate",
                })}},
            ]),
            invoice_store=store,
        )
        orch = Orchestrator(agents={"FinanceAgent": finance})
        state = orch.run("flag invoice INV-001")
        assert state.status == OrchestrationStatus.WAITING_APPROVAL

        # Now resume with a new LLM that provides the completion response
        finance2 = FinanceAgent(
            llm_provider=MockLLMProvider([{"content": "Invoice flagged.", "tool_call": None}]),
            invoice_store=store,
        )
        orch._agents["FinanceAgent"] = finance2
        resumed = orch.resume(state, '{"status": "flagged", "invoice_id": "INV-001"}')
        assert resumed.status == OrchestrationStatus.COMPLETED
        assert resumed.final_result == "Invoice flagged."


# ---------------------------------------------------------------------------
# Orchestrator — retry and escalation
# ---------------------------------------------------------------------------

class TestOrchestratorRetryEscalation:
    def test_retries_on_failure(self):
        agent = AlwaysFailAgent(fail_times=1)
        orch = Orchestrator(agents={"AlwaysFailAgent": agent}, max_retries=3)
        state = orch.run("do something")
        # Fails once, succeeds on retry
        assert state.status == OrchestrationStatus.COMPLETED
        assert agent._calls == 2

    def test_escalates_after_max_retries(self):
        agent = AlwaysFailAgent(fail_times=10)  # always fails
        orch = Orchestrator(agents={"AlwaysFailAgent": agent}, max_retries=2)
        state = orch.run("do something")
        assert state.status == OrchestrationStatus.ESCALATED

    def test_escalation_reason_populated(self):
        agent = AlwaysFailAgent(fail_times=10)
        orch = Orchestrator(agents={"AlwaysFailAgent": agent}, max_retries=1)
        state = orch.run("do something")
        assert state.escalation_reason
        assert "failed after" in state.escalation_reason

    def test_failed_step_has_error_message(self):
        agent = AlwaysFailAgent(fail_times=10)
        orch = Orchestrator(agents={"AlwaysFailAgent": agent}, max_retries=1)
        state = orch.run("do something")
        failed_step = state.steps[0]
        assert failed_step.status == StepStatus.FAILED
        assert failed_step.error

    def test_retry_count_increments(self):
        agent = AlwaysFailAgent(fail_times=10)
        orch = Orchestrator(agents={"AlwaysFailAgent": agent}, max_retries=2)
        state = orch.run("do something")
        assert state.steps[0].retry_count == 3  # 1 initial + 2 retries + 1 that triggers escalation
