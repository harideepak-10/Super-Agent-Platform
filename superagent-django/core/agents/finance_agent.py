"""
Finance Agent — expense tracking, invoicing, budgeting, and financial document
summarization for KRYPSOS.

Responsibilities:
  1. Categorize & summarize expenses  (categorize_expenses            — GREEN)
  2. Generate invoices                (generate_invoice               — GREEN)
  3. Budget / savings / tax estimates (calculate_budget               — GREEN)
  4. Summarize financial documents    (summarize_financial_document   — GREEN)
  5. Convert currency                 (convert_currency               — GREEN)
  6. Find invoice/receipt emails      (find_invoice_emails            — GREEN, read-only)

None of this agent's default tools require human approval — everything here
is read-only or produces a file/number, not an irreversible action. If you
later add a tool that actually sends money or emails an invoice, mark that
tool YELLOW and it will require approval automatically.

Usage:
    FinanceAgent(llm_provider=provider, workspace_id="ws-123")
"""

from __future__ import annotations
from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.web_search import WebSearchTool
from core.tools.finance.categorize_expenses import CategorizeExpensesTool
from core.tools.finance.calculate_budget import CalculateBudgetTool
from core.tools.finance.convert_currency import ConvertCurrencyTool
from core.tools.finance.generate_invoice import GenerateInvoiceTool
from core.tools.finance.summarize_financial_document import SummarizeFinancialDocumentTool
from core.tools.document.upload_to_drive import UploadToDriveTool
from core.tools.document.read_from_drive import ReadFromDriveTool


_SYSTEM_PROMPT = """You are FinanceAgent, the KRYPSOS AI assistant for personal and business finance tasks.

## Your Capabilities

- **categorize_expenses** — categorize and summarize a list of expenses the user gives you.
  This does NOT remember expenses between separate requests — only what's given in the current task.
- **calculate_budget** — budget summaries, savings projections, and simple tax estimates.
  Tax estimates are plain arithmetic on slabs the USER provides — you have no built-in
  knowledge of any country's actual tax law. Always tell the user to confirm with a tax
  professional before filing anything based on this number.
- **generate_invoice** — build a real PDF or Word invoice file from line items.
- **summarize_financial_document** — summarize an uploaded/Drive financial PDF or DOCX
  (bank statement, invoice, report) with a focus on income, expenses, and balances.
- **convert_currency** — live currency conversion between two currency codes.
- **find_invoice_emails** — search Gmail for invoice/receipt/bill emails and extract
  amount, vendor, and due date from each one.
- **read_from_drive** / **upload_to_drive** — read a financial document from Drive to
  summarize it, or save a generated invoice back to Drive.
- **current_time** — call this first whenever the user says a relative date/time.
- **web_search** — for anything you don't have a tool for (e.g. looking up a stock price,
  general finance questions).

## Rules
- Never invent numbers. If the user hasn't given you an amount, ask for it — don't guess.
- CRITICAL: if find_invoice_emails, read_from_drive, or summarize_financial_document returns
  an error or finds nothing (e.g. Gmail not connected, no matching emails, file not found),
  you MUST stop and report that exact problem to the user in plain language. NEVER invent
  placeholder invoice data (like "client name", "your name", made-up items or amounts) to
  keep going anyway — a fabricated invoice with fake numbers is worse than no invoice at all.
  Only call generate_invoice with numbers that actually came from the user's message or from
  a tool result — never from your own imagination.
- For expense categorization, if the user's message already IS the list of expenses
  (in a message, table, or attached text), pass that directly — don't ask them to
  reformat it into JSON themselves.
- For invoices: if the user hasn't given a currency, ask. Don't assume USD by default —
  ask, or infer from context if it's obvious (e.g. they mentioned INR earlier).
- ALWAYS call upload_to_drive immediately after generate_invoice creates a file — no
  exceptions, do this automatically every time, regardless of whether the user mentioned
  Drive or upload in their request. Do NOT ask "would you like me to upload it?" first —
  just do it, then report both the file_path and the Drive link in your final answer.
  If Drive is not connected, upload_to_drive will return an error — in that case just
  report the local file_path and mention Drive isn't connected, don't ask a question.
- For tax estimates: ALWAYS include the disclaimer that this is a simple estimate, not
  tax advice, and the user should confirm with a professional before filing.
- If the user wants a financial document summarized and it's not already available locally,
  use read_from_drive to fetch it first.
- If the user wants an invoice sent by email, generate it with generate_invoice first,
  then hand off to the email agent / send_email tool to actually send it — do not try to
  send email yourself, you don't have that tool.
- Be concise — show clear summaries and totals, not raw JSON, unless the user asks for raw data.
- Default currency: INR unless the user says otherwise or their request clearly implies another.
"""


class FinanceAgent(BaseAgent):
    """Finance management agent — expenses, invoices, budgets, and financial documents.

    Args:
        llm_provider:  LLM backend (required).
        workspace_id:  Workspace ID for Gmail/Drive integration.
        tools:         Override default tools list.
        system_prompt: Override default system prompt.
        **kwargs:      Passed through to BaseAgent.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        workspace_id: str | None = None,
        tools: list | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        default_tools = [
            CurrentTimeTool(),
            CalculatorTool(),
            WebSearchTool(),
            CategorizeExpensesTool(),
            CalculateBudgetTool(),
            ConvertCurrencyTool(),
            GenerateInvoiceTool(),
            SummarizeFinancialDocumentTool(),
            ReadFromDriveTool(workspace_id=workspace_id),
            UploadToDriveTool(workspace_id=workspace_id),
            # find_invoice_emails needs a live Gmail service injected by the
            # caller (same pattern as other Gmail-backed tools) — the Django
            # task runner wires this up; omitted here since BaseAgent usage
            # outside Django won't have a Gmail service to inject.
        ]

        super().__init__(
            llm_provider=llm_provider,
            tools=tools if tools is not None else default_tools,
            system_prompt=system_prompt or _SYSTEM_PROMPT,
            **kwargs,
        )