"""
Finance Agent — invoice processing, total verification, duplicate detection.

FinanceAgent orchestrates:
  1. Retrieve invoices from the data store (GREEN)
  2. Verify totals using Decimal arithmetic (GREEN)
  3. Detect duplicate invoices by number, vendor, amount (GREEN)
  4. Export clean invoice data to CSV (GREEN)
  5. Flag suspicious invoices for human review (YELLOW — needs approval)

Critical rule: every number produced by FinanceAgent must be verified
by QA Agent before it reaches the owner.  Numbers are NEVER approximate.

Nothing is ever flagged automatically.  flag_invoice is YELLOW zone, so
BaseAgent raises ApprovalRequired before it executes.
"""

from __future__ import annotations

from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.finance.get_invoices import GetInvoicesTool
from core.tools.finance.calculate_total import CalculateTotalTool
from core.tools.finance.detect_duplicate import DetectDuplicateTool
from core.tools.finance.flag_invoice import FlagInvoiceTool
from core.tools.finance.export_csv import ExportCSVTool
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool


_SYSTEM_PROMPT = """You are FinanceAgent, an AI assistant for invoice processing and financial verification.

Workflow:
STEP 1 - Retrieve:  Use get_invoices to fetch invoices (filter by status, vendor, or overdue).
STEP 2 - Verify:    Use calculate_total to verify every invoice total against its line items.
STEP 3 - Duplicate: Use detect_duplicate to scan for duplicate invoice numbers or amounts.
STEP 4 - Report:    Present all findings clearly — amounts, discrepancies, duplicates.
STEP 5 - Export:    Use export_csv to save clean invoice data for records.
STEP 6 - Flag:      Use flag_invoice ONLY after explicit human approval for suspicious invoices.

ACCURACY RULES (non-negotiable):
1. EVERY number you report must be verified with calculate_total — never estimate.
2. NEVER round numbers yourself — let calculate_total handle all arithmetic.
3. If a total does not match line items, report the exact discrepancy amount.
4. All monetary values must include currency code (e.g. USD 1,250.00).
5. Flag any invoice where: computed total ≠ stated total, duplicate detected, or vendor unknown.
6. QA Agent will review all output before it reaches the owner — do not skip steps.

Tools (GREEN = auto, YELLOW = needs approval):
- get_invoices:      Retrieve invoices with optional filters (GREEN).
- calculate_total:   Verify totals and sum line items with Decimal precision (GREEN).
- detect_duplicate:  Scan for duplicate invoice numbers or amounts (GREEN).
- export_csv:        Export invoice data to a CSV file in /tmp/ (GREEN).
- flag_invoice:      Flag an invoice for human review (YELLOW — requires approval).
- calculator:        Additional arithmetic when needed (GREEN).
- current_time:      Check today's date for overdue calculations (GREEN).
"""


class FinanceAgent(BaseAgent):
    """Specialised agent for invoice processing and financial verification.

    Extends BaseAgent with finance-focused tools: invoice retrieval,
    Decimal-precise total calculation, duplicate detection, CSV export,
    and gated invoice flagging.

    QA note: FinanceAgent output should always be reviewed by QA Agent
    before being presented to the owner.

    Default limits:
        max_steps : 25
        max_cost  : 1.0 USD
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        invoice_store: Any = None,
        max_steps: int = 25,
        max_cost: float = 1.0,
        task_id: str | None = None,
    ) -> None:
        """Initialise FinanceAgent.

        Args:
            llm_provider:   LLM provider for all inference calls.
            invoice_store:  Data store injected into invoice tools.
                            Must implement .all(), .filter(), .get(), .flag().
                            When None, tools return an error — always inject
                            a store (real or mock) before using the agent.
            max_steps:      Maximum agent loop steps.
            max_cost:       Maximum cumulative LLM cost in USD.
            task_id:        Optional external task identifier.
        """
        tools = [
            GetInvoicesTool(invoice_store=invoice_store),
            CalculateTotalTool(),
            DetectDuplicateTool(),
            FlagInvoiceTool(invoice_store=invoice_store),
            ExportCSVTool(),
            CalculatorTool(),
            CurrentTimeTool(),
        ]

        super().__init__(
            name="FinanceAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=max_steps,
            max_cost=max_cost,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
