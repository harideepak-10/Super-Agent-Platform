"""
Tests for the Django↔AI bridge handlers.
Pure Python — no Django ORM, no Celery, no real LLM calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from api.task_handler import TaskHandler, TaskRequest, TaskResult
from api.approval_handler import ApprovalHandler, ApprovalRequest, ApprovalResult
from api.result_handler import ResultHandler
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_invoice_store():
    class S:
        def all(self): return []
        def filter(self, **kw): return []
        def get(self, id): return None
        def flag(self, *a, **kw): pass
    return S()


def _qa_agent(responses):
    from agents.qa_agent import QAAgent
    return QAAgent(llm_provider=MockLLMProvider(responses))


def _make_task_result(**kwargs) -> TaskResult:
    defaults = dict(task_id="t1", status="completed", result="done",
                    error="", steps_taken=2, cost_usd=0.001, audit_log=[])
    defaults.update(kwargs)
    return TaskResult(**defaults)


def _make_approval_result(**kwargs) -> ApprovalResult:
    defaults = dict(task_id="t1", status="completed", result="done",
                    error="", steps_taken=1, cost_usd=0.0, audit_log=[])
    defaults.update(kwargs)
    return ApprovalResult(**defaults)


# ---------------------------------------------------------------------------
# TaskRequest dataclass
# ---------------------------------------------------------------------------

class TestTaskRequest:
    def test_defaults(self):
        req = TaskRequest(task_id="t1", prompt="hello")
        assert req.agent_type == "auto"
        assert req.max_steps == 20
        assert req.max_cost == 1.0

    def test_custom_values(self):
        req = TaskRequest(task_id="t1", prompt="hi", agent_type="qa", max_steps=5)
        assert req.agent_type == "qa"
        assert req.max_steps == 5


# ---------------------------------------------------------------------------
# TaskHandler — single agent
# ---------------------------------------------------------------------------

class TestTaskHandlerSingleAgent:
    def _handler_with_qa(self, responses):
        """Patch _build_agent so it returns a MockLLMProvider-backed QAAgent."""
        from agents.qa_agent import QAAgent
        agent = QAAgent(llm_provider=MockLLMProvider(responses))

        handler = TaskHandler()
        with patch("api.task_handler._build_agent", return_value=agent):
            req = TaskRequest(task_id="t1", prompt="review output", agent_type="qa")
            return handler.execute(req)

    def test_completed_status(self):
        result = self._handler_with_qa([{"content": "QA PASSED.", "tool_call": None}])
        assert result.status == "completed"

    def test_result_populated(self):
        result = self._handler_with_qa([{"content": "All good.", "tool_call": None}])
        assert result.result == "All good."

    def test_task_id_preserved(self):
        result = self._handler_with_qa([{"content": "Done.", "tool_call": None}])
        assert result.task_id == "t1"

    def test_steps_taken_populated(self):
        result = self._handler_with_qa([{"content": "Done.", "tool_call": None}])
        assert result.steps_taken >= 1

    def test_audit_log_populated(self):
        result = self._handler_with_qa([{"content": "Done.", "tool_call": None}])
        assert isinstance(result.audit_log, list)

    def test_waiting_approval_on_yellow_tool(self):
        from agents.finance_agent import FinanceAgent
        store = _mock_invoice_store()
        agent = FinanceAgent(
            llm_provider=MockLLMProvider([
                {"content": "", "tool_call": {"name": "flag_invoice", "input": json.dumps({
                    "invoice_id": "INV-001", "reason": "duplicate",
                })}},
            ]),
            invoice_store=store,
        )
        handler = TaskHandler()
        with patch("api.task_handler._build_agent", return_value=agent):
            req = TaskRequest(task_id="t1", prompt="flag invoice", agent_type="finance")
            result = handler.execute(req)
        assert result.status == "waiting_approval"
        assert result.approval_payload is not None
        assert result.approval_payload["tool_name"] == "flag_invoice"

    def test_failed_status_on_step_limit(self):
        from core.base_agent import StepLimitReached
        from agents.qa_agent import QAAgent
        agent = QAAgent(
            llm_provider=MockLLMProvider([
                {"content": "", "tool_call": {"name": "review_draft", "input": '{"draft": "x"}'}}
            ] * 20),
            max_steps=2,
        )
        handler = TaskHandler()
        with patch("api.task_handler._build_agent", return_value=agent):
            req = TaskRequest(task_id="t1", prompt="loop", agent_type="qa")
            result = handler.execute(req)
        assert result.status == "failed"
        assert result.error

    def test_unknown_agent_type_returns_failed(self):
        with patch("api.task_handler._build_agent", return_value=None):
            req = TaskRequest(task_id="t1", prompt="hi", agent_type="unknown_xyz")
            result = TaskHandler().execute(req)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# TaskHandler — orchestrated (auto)
# ---------------------------------------------------------------------------

class TestTaskHandlerOrchestrated:
    def test_auto_routes_and_completes(self):
        from agents.qa_agent import QAAgent
        qa = QAAgent(llm_provider=MockLLMProvider([{"content": "QA done.", "tool_call": None}]))

        from agents.orchestrator import Orchestrator, OrchestrationStatus, OrchestrationState, OrchestrationStep, StepStatus
        mock_state = OrchestrationState(
            run_id="r1",
            original_task="review output",
            steps=[],
            status=OrchestrationStatus.COMPLETED,
            final_result="QA done.",
        )

        handler = TaskHandler()
        with patch("api.task_handler._build_agent", return_value=qa), \
             patch("agents.orchestrator.Orchestrator.run", return_value=mock_state):
            req = TaskRequest(task_id="t2", prompt="review output", agent_type="auto")
            result = handler.execute(req)

        assert result.status == "completed"
        assert result.result == "QA done."


# ---------------------------------------------------------------------------
# ApprovalRequest dataclass
# ---------------------------------------------------------------------------

class TestApprovalRequest:
    def test_defaults(self):
        req = ApprovalRequest(
            task_id="t1", approved=True, tool_name="send_email",
            tool_input={}, resume_snapshot={}, original_prompt="hi",
        )
        assert req.agent_type == "email"
        assert req.max_steps == 20

    def test_rejection_marked(self):
        req = ApprovalRequest(
            task_id="t1", approved=False, tool_name="send_email",
            tool_input={}, resume_snapshot={}, original_prompt="hi",
        )
        assert req.approved is False


# ---------------------------------------------------------------------------
# ApprovalHandler
# ---------------------------------------------------------------------------

class TestApprovalHandler:
    def _make_snapshot(self):
        return {
            "tool_name": "flag_invoice",
            "tool_input": '{"invoice_id": "INV-001", "reason": "duplicate"}',
            "messages_snapshot": [
                {"role": "system", "content": "You are FinanceAgent."},
                {"role": "user", "content": "flag invoice"},
            ],
            "last_assistant_content": "",
            "last_tool_call": {"name": "flag_invoice", "input": "{}"},
            "task": "flag invoice INV-001",
        }

    def test_rejection_returns_rejected_status(self):
        req = ApprovalRequest(
            task_id="t1", approved=False, tool_name="flag_invoice",
            tool_input={}, resume_snapshot={}, original_prompt="flag invoice",
            reviewer_note="Not authorised",
        )
        result = ApprovalHandler().resume(req)
        assert result.status == "rejected"
        assert "Not authorised" in result.error

    def test_rejection_without_note(self):
        req = ApprovalRequest(
            task_id="t1", approved=False, tool_name="flag_invoice",
            tool_input={}, resume_snapshot={}, original_prompt="flag invoice",
        )
        result = ApprovalHandler().resume(req)
        assert result.status == "rejected"

    def test_resume_completes_after_approval(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent(
            llm_provider=MockLLMProvider([{"content": "Resumed and done.", "tool_call": None}])
        )
        with patch("api.approval_handler._build_agent", return_value=agent):
            req = ApprovalRequest(
                task_id="t1", approved=True, tool_name="flag_invoice",
                tool_input={}, resume_snapshot=self._make_snapshot(),
                original_prompt="flag invoice",
                agent_type="finance",
            )
            result = ApprovalHandler().resume(req)
        assert result.status == "completed"
        assert result.result == "Resumed and done."

    def test_resume_handles_second_yellow_tool(self):
        from agents.finance_agent import FinanceAgent
        store = _mock_invoice_store()
        agent = FinanceAgent(
            llm_provider=MockLLMProvider([
                {"content": "", "tool_call": {"name": "flag_invoice", "input": json.dumps({
                    "invoice_id": "INV-002", "reason": "suspicious_vendor",
                })}},
            ]),
            invoice_store=store,
        )
        with patch("api.approval_handler._build_agent", return_value=agent):
            req = ApprovalRequest(
                task_id="t1", approved=True, tool_name="flag_invoice",
                tool_input={}, resume_snapshot=self._make_snapshot(),
                original_prompt="flag all suspicious invoices",
                agent_type="finance",
            )
            result = ApprovalHandler().resume(req)
        assert result.status == "waiting_approval"
        assert result.approval_payload is not None

    def test_resume_task_id_preserved(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent(llm_provider=MockLLMProvider([{"content": "Done.", "tool_call": None}]))
        with patch("api.approval_handler._build_agent", return_value=agent):
            req = ApprovalRequest(
                task_id="my-task-id", approved=True, tool_name="x",
                tool_input={}, resume_snapshot=self._make_snapshot(),
                original_prompt="test",
            )
            result = ApprovalHandler().resume(req)
        assert result.task_id == "my-task-id"

    def test_unknown_agent_type_returns_failed(self):
        with patch("api.approval_handler._build_agent", return_value=None):
            req = ApprovalRequest(
                task_id="t1", approved=True, tool_name="x",
                tool_input={}, resume_snapshot={}, original_prompt="hi",
                agent_type="ghost",
            )
            result = ApprovalHandler().resume(req)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# ResultHandler
# ---------------------------------------------------------------------------

class TestResultHandler:
    def test_format_task_result_completed(self):
        result = _make_task_result()
        payload = ResultHandler.format_task_result(result)
        assert payload["status"] == "completed"
        assert payload["result"] == "done"
        assert payload["task_id"] == "t1"
        assert "formatted_at" in payload

    def test_format_task_result_has_approval_summary(self):
        result = _make_task_result(
            status="waiting_approval",
            approval_payload={"tool_name": "flag_invoice", "tool_input": "{}"},
        )
        payload = ResultHandler.format_task_result(result)
        assert payload["has_approval"] is True
        assert payload["approval_summary"]["tool_name"] == "flag_invoice"

    def test_format_task_result_no_approval(self):
        result = _make_task_result()
        payload = ResultHandler.format_task_result(result)
        assert payload["has_approval"] is False
        assert payload["approval_summary"] is None

    def test_format_approval_result(self):
        result = _make_approval_result()
        payload = ResultHandler.format_approval_result(result)
        assert payload["status"] == "completed"
        assert "formatted_at" in payload

    def test_format_audit_log(self):
        log = [
            {"event_type": "task_started", "details": {"task": "test"}, "step_number": 1},
            {"event_type": "tool_called", "details": {"tool_name": "flag_invoice"}, "step_number": 2},
        ]
        formatted = ResultHandler.format_audit_log(log)
        assert len(formatted) == 2
        assert formatted[0]["event_type"] == "task_started"
        assert formatted[1]["step"] == 2

    def test_ws_task_started(self):
        payload = ResultHandler.ws_task_started("t1")
        assert payload["event"] == "task_started"
        assert payload["task_id"] == "t1"

    def test_ws_task_completed(self):
        payload = ResultHandler.ws_task_completed("t1", "done", 3, 0.002)
        assert payload["event"] == "task_completed"
        assert payload["steps_taken"] == 3

    def test_ws_approval_required(self):
        payload = ResultHandler.ws_approval_required("t1", "a1", "flag_invoice", "{}")
        assert payload["event"] == "approval_required"
        assert payload["tool_name"] == "flag_invoice"

    def test_ws_task_failed(self):
        payload = ResultHandler.ws_task_failed("t1", "Out of steps")
        assert payload["event"] == "task_failed"
        assert "Out of steps" in payload["error"]

    def test_ws_task_resumed(self):
        payload = ResultHandler.ws_task_resumed("t1")
        assert payload["event"] == "task_resumed"

    def test_ws_task_cancelled(self):
        payload = ResultHandler.ws_task_cancelled("t1", "Rejected")
        assert payload["event"] == "task_cancelled"
        assert payload["reason"] == "Rejected"

    def test_cost_rounded_to_6dp(self):
        result = _make_task_result(cost_usd=0.00012345678)
        payload = ResultHandler.format_task_result(result)
        assert len(str(payload["cost_usd"]).split(".")[-1]) <= 6

    def test_task_id_override(self):
        result = _make_task_result(task_id="old-id")
        payload = ResultHandler.format_task_result(result, task_id="new-id")
        assert payload["task_id"] == "new-id"
