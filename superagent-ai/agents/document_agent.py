"""
Document Agent — invoice and file management via Google Drive.

DocumentAgent orchestrates:
  1. Search Drive for relevant documents (GREEN)
  2. Download and read PDF content (GREEN)
  3. Extract structured fields (vendor, amounts, dates) via LLM (GREEN)
  4. Present findings to the human operator
  5. Move approved files to organised folders (YELLOW — needs approval)

Nothing is ever moved automatically.  move_file is YELLOW zone, so
BaseAgent raises ApprovalRequired before it executes.
"""

from __future__ import annotations

from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.drive.search_drive import SearchDriveTool
from core.tools.drive.read_pdf import ReadPDFTool
from core.tools.drive.extract_data import ExtractDataTool
from core.tools.drive.move_file import MoveFileTool
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool


_SYSTEM_PROMPT = """You are DocumentAgent, an AI assistant for invoice processing and file organisation.

Workflow:
STEP 1 - Search: Use search_drive to find invoices, contracts, or receipts.
STEP 2 - Read:   Use read_pdf to extract the full text from a PDF.
STEP 3 - Extract: Use extract_data to pull structured fields (vendor, amount, dates, invoice number).
STEP 4 - Verify: Check totals with calculator. Cross-check dates with current_time.
STEP 5 - Report: Present all extracted data clearly to the human operator.
STEP 6 - Organise: Use move_file ONLY after explicit human approval to file documents.

Hard rules:
1. NEVER move or delete files without explicit human approval.
2. ALWAYS extract and verify invoice numbers and totals before reporting.
3. Flag any invoice where the total does not match line items.
4. Flag any invoice that is overdue (due_date < today).
5. When in doubt about a document, ask the human operator.

Tools (GREEN = auto, YELLOW = needs approval):
- search_drive:  Search Drive for files by name or type (GREEN).
- read_pdf:      Download and extract text from a PDF file (GREEN).
- extract_data:  Extract structured invoice/contract fields via LLM (GREEN).
- move_file:     Move a file to a different Drive folder (YELLOW — requires approval).
- calculator:    Verify invoice totals and compute date differences (GREEN).
- current_time:  Get current date to check due dates (GREEN).
"""


class DocumentAgent(BaseAgent):
    """Specialised agent for Drive-powered document and invoice management.

    Extends BaseAgent with a Drive-focused tool set:
    search, read PDF, extract structured data, and gated file moves.

    Default limits:
        max_steps : 20
        max_cost  : 1.0 USD
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        drive_service: Any = None,
        max_steps: int = 20,
        max_cost: float = 1.0,
        task_id: str | None = None,
    ) -> None:
        """Initialise DocumentAgent.

        Args:
            llm_provider: LLM provider for all inference calls.
            drive_service: Optional mock/real Drive service injected into
                           Drive tools. When None, tools build the real
                           service from DriveAuth on first use.
            max_steps:    Maximum agent loop steps.
            max_cost:     Maximum cumulative LLM cost in USD.
            task_id:      Optional external task identifier.
        """
        # Build extract_data with the same LLM provider so it can call
        # the LLM without needing a separate credential setup.
        extract_tool = ExtractDataTool(llm_provider=llm_provider)

        tools = [
            SearchDriveTool(drive_service=drive_service),
            ReadPDFTool(drive_service=drive_service),
            extract_tool,
            MoveFileTool(drive_service=drive_service),
            CalculatorTool(),
            CurrentTimeTool(),
        ]

        super().__init__(
            name="DocumentAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=max_steps,
            max_cost=max_cost,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
