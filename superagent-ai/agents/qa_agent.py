"""
QA Agent — reviews output from other agents before it reaches the owner.

QAAgent orchestrates:
  1. Review the draft text for completeness and placeholders (GREEN)
  2. Verify all numeric values against expected values (GREEN)
  3. Flag any issues found for human review (GREEN)

All QA tools are GREEN — QA reviews are read-only and non-destructive.
QA Agent is always called BEFORE presenting FinanceAgent or
ReportingAgent output to the owner.
"""

from __future__ import annotations

from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.qa.review_draft import ReviewDraftTool
from core.tools.qa.verify_numbers import VerifyNumbersTool
from core.tools.qa.flag_issue import FlagIssueTool


_SYSTEM_PROMPT = """You are QAAgent, an AI quality assurance reviewer for the Super Agent platform.

Your job is to review output from FinanceAgent and ReportingAgent BEFORE it reaches the owner.

Workflow:
STEP 1 - Review: Use review_draft to check the output for completeness, placeholders, and formatting.
STEP 2 - Numbers: Use verify_numbers to confirm all monetary amounts and counts are correct.
STEP 3 - Flag: Use flag_issue for EVERY problem found — never skip issues, no matter how small.
STEP 4 - Report: Return a clear QA summary: PASSED or FAILED, with a list of all issues.

QA rules (non-negotiable):
1. NEVER approve output that contains a number mismatch.
2. NEVER approve output with placeholder text ([TODO], [INSERT], etc.).
3. NEVER approve output with missing required sections.
4. Flag every issue — even "low" severity ones.
5. If QA passes with zero issues, explicitly state "QA PASSED — no issues found."
6. If QA fails, list every issue with severity and description.

Severity guide:
- critical: Wrong numbers, missing totals, data corruption
- high:     Missing required sections, logic errors
- medium:   Formatting issues, incomplete sentences
- low:      Minor wording, style suggestions
"""


class QAAgent(BaseAgent):
    """QA Agent that reviews other agents' output before delivery to owner.

    Extends BaseAgent with QA-focused tools: draft review, number
    verification, and issue flagging.  All tools are GREEN — QA is
    read-only and never modifies data.

    Default limits:
        max_steps : 10
        max_cost  : 0.5 USD
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        issue_log: list[dict[str, Any]] | None = None,
        max_steps: int = 10,
        max_cost: float = 0.5,
        task_id: str | None = None,
    ) -> None:
        """Initialise QAAgent.

        Args:
            llm_provider: LLM provider for all inference calls.
            issue_log:    Optional shared list to collect flagged issues.
                          Useful for the Orchestrator to inspect QA results.
            max_steps:    Maximum agent loop steps (default 10 — QA is short).
            max_cost:     Maximum cumulative LLM cost (default $0.50).
            task_id:      Optional external task identifier.
        """
        tools = [
            ReviewDraftTool(),
            VerifyNumbersTool(),
            FlagIssueTool(issue_log=issue_log),
        ]
        super().__init__(
            name="QAAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=max_steps,
            max_cost=max_cost,
            task_id=task_id,
        )
        self.issue_log: list[dict[str, Any]] = issue_log if issue_log is not None else []

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
