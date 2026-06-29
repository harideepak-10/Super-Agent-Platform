"""
Tests for ComplianceAgent, CheckDeadlinesTool, FindMissingDocsTool, SendAlertTool.
Telegram is fully mocked — no real HTTP calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agents.compliance_agent import ComplianceAgent
from core.tools.compliance.check_deadlines import CheckDeadlinesTool
from core.tools.compliance.find_missing_docs import FindMissingDocsTool
from core.tools.compliance.send_alert import SendAlertTool, TelegramService, _ALERT_LEVELS
from core.tools.base_tool import ToolZone
from core.base_agent import StepLimitReached, ApprovalRequired
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# MockTelegramService
# ---------------------------------------------------------------------------

class MockTelegramService(TelegramService):
    """Records sent messages without hitting the network."""

    def __init__(self):
        self.sent: list[str] = []
        self._token = "mock_token"
        self._chat_id = "mock_chat_id"

    def send_message(self, text: str) -> dict:
        self.sent.append(text)
        return {"ok": True, "result": {"message_id": len(self.sent)}}


# ---------------------------------------------------------------------------
# CheckDeadlinesTool tests
# ---------------------------------------------------------------------------

class TestCheckDeadlinesTool:
    def _tool(self):
        return CheckDeadlinesTool()

    def _run(self, items, *, ref="2024-01-15", warning_days=7):
        payload = {"items": items, "reference_date": ref, "warning_days": warning_days}
        return json.loads(self._tool().run(json.dumps(payload)))

    def test_detects_overdue_item(self):
        items = [{"id": "1", "name": "VAT Return", "due_date": "2024-01-10"}]
        result = self._run(items)
        assert len(result["overdue"]) == 1
        assert result["overdue"][0]["days_overdue"] == 5

    def test_detects_item_due_today(self):
        items = [{"id": "1", "name": "Report", "due_date": "2024-01-15"}]
        result = self._run(items)
        assert len(result["due_today"]) == 1

    def test_detects_upcoming_item(self):
        items = [{"id": "1", "name": "Filing", "due_date": "2024-01-20"}]
        result = self._run(items)
        assert len(result["upcoming"]) == 1
        assert result["upcoming"][0]["days_until_due"] == 5

    def test_item_beyond_window_not_flagged(self):
        items = [{"id": "1", "name": "Far Away", "due_date": "2024-03-01"}]
        result = self._run(items)
        assert len(result["overdue"]) == 0
        assert len(result["upcoming"]) == 0
        assert result["ok_count"] == 1

    def test_completed_item_ignored(self):
        items = [{"id": "1", "name": "Done", "due_date": "2024-01-05", "status": "completed"}]
        result = self._run(items)
        assert len(result["overdue"]) == 0
        assert result["ok_count"] == 1

    def test_action_required_when_overdue(self):
        items = [{"id": "1", "name": "Late", "due_date": "2024-01-01"}]
        result = self._run(items)
        assert result["action_required"] is True

    def test_action_not_required_when_all_ok(self):
        items = [{"id": "1", "name": "Future", "due_date": "2024-02-01"}]
        result = self._run(items)
        assert result["action_required"] is False

    def test_multiple_items_sorted(self):
        items = [
            {"id": "1", "name": "Less Late", "due_date": "2024-01-13"},
            {"id": "2", "name": "More Late", "due_date": "2024-01-01"},
        ]
        result = self._run(items)
        assert result["overdue"][0]["id"] == "2"  # most overdue first

    def test_empty_items_returns_error(self):
        result = json.loads(self._tool().run(json.dumps({"items": []})))
        assert "error" in result

    def test_invalid_due_date_in_overdue(self):
        items = [{"id": "1", "name": "Bad Date", "due_date": "not-a-date"}]
        result = self._run(items)
        assert len(result["overdue"]) == 1
        assert "Invalid" in result["overdue"][0]["note"]

    def test_custom_warning_days(self):
        items = [{"id": "1", "name": "Item", "due_date": "2024-01-25"}]
        result = self._run(items, warning_days=14)  # 10 days away, inside 14-day window
        assert len(result["upcoming"]) == 1

    def test_zone_is_green(self):
        assert CheckDeadlinesTool().zone == ToolZone.GREEN

    def test_total_checked_count(self):
        items = [
            {"id": "1", "name": "A", "due_date": "2024-01-10"},
            {"id": "2", "name": "B", "due_date": "2024-02-01"},
        ]
        result = self._run(items)
        assert result["total_checked"] == 2


# ---------------------------------------------------------------------------
# FindMissingDocsTool tests
# ---------------------------------------------------------------------------

class TestFindMissingDocsTool:
    def _tool(self):
        return FindMissingDocsTool()

    def test_finds_missing_doc(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["Invoice", "Contract", "ID Copy"],
            "available": ["Invoice", "Contract"],
        })))
        assert result["missing"] == ["ID Copy"]
        assert result["compliant"] is False

    def test_all_docs_present(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["Invoice", "Contract"],
            "available": ["Invoice", "Contract", "Extra Doc"],
        })))
        assert result["compliant"] is True
        assert result["missing"] == []
        assert result["action_required"] is False

    def test_compliance_percentage_calculated(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["A", "B", "C", "D"],
            "available": ["A", "B"],
        })))
        assert result["compliance_percentage"] == 50.0

    def test_case_insensitive_match(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["Invoice"],
            "available": ["INVOICE"],
        })))
        assert result["compliant"] is True

    def test_entity_name_in_summary(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["Contract"],
            "available": [],
            "entity_name": "Acme Ltd",
        })))
        assert "Acme Ltd" in result["summary"]

    def test_no_required_returns_error(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": [],
            "available": ["Invoice"],
        })))
        assert "error" in result

    def test_zone_is_green(self):
        assert FindMissingDocsTool().zone == ToolZone.GREEN

    def test_present_count_correct(self):
        result = json.loads(self._tool().run(json.dumps({
            "required": ["A", "B", "C"],
            "available": ["A", "B"],
        })))
        assert result["present_count"] == 2
        assert result["missing_count"] == 1


# ---------------------------------------------------------------------------
# SendAlertTool tests
# ---------------------------------------------------------------------------

class TestSendAlertTool:
    def _tool(self):
        svc = MockTelegramService()
        tool = SendAlertTool(telegram_service=svc)
        tool._mock_svc = svc
        return tool

    def test_sends_warning_alert(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({
            "message": "VAT return overdue by 5 days.",
            "level": "warning",
        })))
        assert result["status"] == "sent"
        assert len(tool._mock_svc.sent) == 1

    def test_sends_critical_alert(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({
            "message": "Regulatory deadline missed.",
            "level": "critical",
            "subject": "CRITICAL: Compliance Failure",
        })))
        assert result["status"] == "sent"
        assert result["level"] == "critical"

    def test_sends_info_alert(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({
            "message": "Deadline coming up in 3 days.",
            "level": "info",
        })))
        assert result["status"] == "sent"

    def test_message_contains_level(self):
        tool = self._tool()
        tool.run(json.dumps({"message": "Test message.", "level": "critical"}))
        assert "CRITICAL" in tool._mock_svc.sent[0]

    def test_message_contains_subject(self):
        tool = self._tool()
        tool.run(json.dumps({
            "message": "Test.",
            "level": "warning",
            "subject": "Custom Subject",
        }))
        assert "Custom Subject" in tool._mock_svc.sent[0]

    def test_telegram_message_id_in_result(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({
            "message": "Test.",
            "level": "info",
        })))
        assert result["telegram_message_id"] == 1

    def test_invalid_level_returns_error(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({
            "message": "Test.",
            "level": "extreme",
        })))
        assert "error" in result

    def test_missing_message_returns_error(self):
        tool = self._tool()
        result = json.loads(tool.run(json.dumps({"level": "warning"})))
        assert "error" in result

    def test_zone_is_yellow(self):
        assert SendAlertTool().zone == ToolZone.YELLOW

    def test_all_levels_accepted(self):
        for level in _ALERT_LEVELS:
            tool = self._tool()
            result = json.loads(tool.run(json.dumps({
                "message": f"Test {level}.",
                "level": level,
            })))
            assert result["status"] == "sent"

    def test_missing_token_raises_in_send(self):
        svc = TelegramService(bot_token="", chat_id="some_chat")
        tool = SendAlertTool(telegram_service=svc)
        result = json.loads(tool.run(json.dumps({"message": "Test.", "level": "info"})))
        assert "error" in result

    def test_missing_chat_id_raises_in_send(self):
        svc = TelegramService(bot_token="some_token", chat_id="")
        tool = SendAlertTool(telegram_service=svc)
        result = json.loads(tool.run(json.dumps({"message": "Test.", "level": "info"})))
        assert "error" in result


# ---------------------------------------------------------------------------
# ComplianceAgent tests
# ---------------------------------------------------------------------------

class TestComplianceAgentInit:
    def test_agent_name(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert agent.name == "ComplianceAgent"

    def test_has_check_deadlines_tool(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert "check_deadlines" in agent._tools

    def test_has_find_missing_docs_tool(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert "find_missing_docs" in agent._tools

    def test_has_send_alert_tool(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert "send_alert" in agent._tools

    def test_check_deadlines_is_green(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert agent._tools["check_deadlines"].zone == ToolZone.GREEN

    def test_find_missing_docs_is_green(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert agent._tools["find_missing_docs"].zone == ToolZone.GREEN

    def test_send_alert_is_yellow(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert agent._tools["send_alert"].zone == ToolZone.YELLOW

    def test_system_prompt_mentions_schedule(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert "schedule" in agent._system_prompt().lower()

    def test_system_prompt_mentions_telegram(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert "Telegram" in agent._system_prompt()

    def test_default_max_steps(self):
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]))
        assert agent.max_steps == 20


class TestComplianceAgentWorkflow:
    def _mock_svc(self):
        return MockTelegramService()

    def test_checks_deadlines(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "check_deadlines", "input": json.dumps({
                "items": [{"id": "1", "name": "VAT Return", "due_date": "2024-01-10"}],
                "reference_date": "2024-01-15",
            })}},
            {"content": "Compliance check complete. VAT Return is 5 days overdue.", "tool_call": None},
        ])
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc)
        result = agent.run("Run compliance check")
        assert result

    def test_finds_missing_docs(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "find_missing_docs", "input": json.dumps({
                "required": ["Contract", "ID Copy"],
                "available": ["Contract"],
                "entity_name": "Vendor A",
            })}},
            {"content": "Vendor A is missing ID Copy.", "tool_call": None},
        ])
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc)
        result = agent.run("Check vendor documents")
        assert "Vendor A" in result or result

    def test_send_alert_requires_approval(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "send_alert", "input": json.dumps({
                "message": "VAT Return is 5 days overdue.",
                "level": "critical",
            })}},
        ])
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc)
        with pytest.raises(ApprovalRequired):
            agent.run("Send compliance alert")
        assert agent.pending_approval is not None
        assert agent.pending_approval["tool_name"] == "send_alert"

    def test_no_alert_sent_on_all_clear(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "check_deadlines", "input": json.dumps({
                "items": [{"id": "1", "name": "Future", "due_date": "2024-03-01"}],
                "reference_date": "2024-01-15",
            })}},
            {"content": "All clear — no overdue or upcoming items within warning window.", "tool_call": None},
        ])
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc)
        result = agent.run("Run compliance check")
        assert len(svc.sent) == 0

    def test_audit_log_populated(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([{"content": "All clear.", "tool_call": None}])
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc)
        agent.run("Compliance check")
        log = agent.get_audit_log()
        assert any(e["event_type"] == "task_completed" for e in log)

    def test_custom_telegram_service_injected(self):
        svc = self._mock_svc()
        agent = ComplianceAgent(llm_provider=MockLLMProvider([]), telegram_service=svc)
        assert agent._tools["send_alert"]._telegram is svc

    def test_respects_max_steps(self):
        svc = self._mock_svc()
        llm = MockLLMProvider([
            {"content": "", "tool_call": {"name": "check_deadlines", "input": json.dumps({
                "items": [{"id": "1", "name": "X", "due_date": "2024-01-10"}],
                "reference_date": "2024-01-15",
            })}}
        ] * 25)
        agent = ComplianceAgent(llm_provider=llm, telegram_service=svc, max_steps=3)
        with pytest.raises(StepLimitReached):
            agent.run("Loop forever")
