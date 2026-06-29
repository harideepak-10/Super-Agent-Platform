"""
ComplianceAgent — runs on schedule, not on user trigger.
Checks deadlines, finds missing documents, and sends Telegram alerts.
"""

from __future__ import annotations

from core.base_agent import BaseAgent
from core.tools.compliance.check_deadlines import CheckDeadlinesTool
from core.tools.compliance.find_missing_docs import FindMissingDocsTool
from core.tools.compliance.send_alert import SendAlertTool, TelegramService


class ComplianceAgent(BaseAgent):
    """
    Runs automated compliance checks.

    Designed to be triggered on a schedule (e.g., daily via Celery beat),
    not by a user message. Checks for:
    - Overdue or upcoming compliance deadlines
    - Missing required documents for vendors/entities
    - Sends Telegram alerts for any critical issues (YELLOW — requires approval)
    """

    def __init__(
        self,
        llm_provider,
        telegram_service: TelegramService | None = None,
        max_steps: int = 20,
        max_cost: float = 1.0,
    ):
        send_alert = SendAlertTool(telegram_service=telegram_service or TelegramService())
        tools = [
            CheckDeadlinesTool(),
            FindMissingDocsTool(),
            send_alert,
        ]
        super().__init__(
            name="ComplianceAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=max_steps,
            max_cost=max_cost,
        )

    def _system_prompt(self) -> str:
        return (
            "You are the ComplianceAgent for the KRYPSOS platform.\n\n"
            "ROLE:\n"
            "You run on a schedule to monitor compliance for the business. "
            "You do not wait for user instructions — you check everything proactively.\n\n"
            "WORKFLOW:\n"
            "1. Use check_deadlines to identify any overdue or upcoming compliance items.\n"
            "2. Use find_missing_docs to check each vendor/entity for required documents.\n"
            "3. If there are overdue items or missing critical documents, use send_alert to notify "
            "the owner via Telegram. send_alert requires human approval before sending.\n"
            "4. Summarise your findings clearly: what is overdue, what is missing, "
            "what alerts were sent.\n\n"
            "ALERT LEVELS:\n"
            "- info: Upcoming deadlines (within 7 days), minor missing docs.\n"
            "- warning: Deadlines due today, multiple missing documents.\n"
            "- critical: Overdue items, regulatory deadlines missed.\n\n"
            "RULES:\n"
            "- Never fabricate compliance data — only report what the tools return.\n"
            "- Always send_alert for critical issues even if it requires approval.\n"
            "- Group alerts: one message per severity level, not one per item.\n"
            "- If no issues found, output a brief 'All clear' summary — do not send an alert.\n"
            "- You run automatically; your output goes into the task audit log.\n"
        )
