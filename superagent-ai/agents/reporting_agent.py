"""
Reporting Agent — generates weekly and monthly PDF/JSON summaries.

ReportingAgent orchestrates:
  1. Collect data from other agents or provided context (GREEN)
  2. Generate a formatted PDF report using reportlab (GREEN)
  3. Export a machine-readable JSON/text report (GREEN)
  4. Present the report path to the operator

All tools are GREEN — report generation is read-only and non-destructive.
"""

from __future__ import annotations

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.reporting.generate_pdf import GeneratePDFTool
from core.tools.reporting.export_report import ExportReportTool
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool


_SYSTEM_PROMPT = """You are ReportingAgent, an AI assistant for generating weekly and monthly business summaries.

Workflow:
STEP 1 - Collect: Gather all data provided in the task (invoices, costs, audit events).
STEP 2 - Structure: Organise data into report sections: Summary, Invoice Details, Costs, Issues.
STEP 3 - Calculate: Use calculator to verify all totals and percentages.
STEP 4 - PDF: Use generate_pdf to create the formatted PDF report.
STEP 5 - Export: Use export_report to save a JSON version for downstream systems.
STEP 6 - Confirm: Return the file paths to the operator.

Report periods:
- weekly:  covers Mon–Sun of the current week
- monthly: covers the full calendar month

Report sections (always include all):
1. Executive Summary    — 2–3 sentence overview
2. Invoice Summary      — count, total amount, pending/paid/overdue breakdown
3. Cost Summary         — LLM and tool costs for the period
4. Issues & Flags       — any QA issues or flagged invoices
5. Recommendations      — 2–3 action items for the owner

Accuracy rules:
1. All numbers must match the source data exactly — never estimate.
2. Always use calculator to verify totals before writing them.
3. Use current_time to confirm the report period dates.
"""


class ReportingAgent(BaseAgent):
    """Specialised agent for generating PDF and JSON business reports.

    Extends BaseAgent with reporting tools: PDF generation (reportlab)
    and structured JSON/text export.

    Default limits:
        max_steps : 15
        max_cost  : 1.0 USD
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        max_steps: int = 15,
        max_cost: float = 1.0,
        task_id: str | None = None,
    ) -> None:
        tools = [
            GeneratePDFTool(),
            ExportReportTool(),
            CalculatorTool(),
            CurrentTimeTool(),
        ]
        super().__init__(
            name="ReportingAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=max_steps,
            max_cost=max_cost,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
