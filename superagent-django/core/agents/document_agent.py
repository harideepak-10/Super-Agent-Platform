"""
Document Agent — generates professional documents for KRYPSOS.

Responsibilities:
  1. Understand what document the user wants (type, content, format)
  2. Generate structured content using the LLM (generate_content)
  3. Create the file in the requested format (create_pdf / create_docx / export_csv)
  4. Upload to Google Drive after human approval (upload_to_drive — YELLOW)
  5. Return the Drive link in task.deliverables[]

Supported formats: PDF, Word (.docx), CSV
Supported input sources: LLM prompt, email data, task history, raw user text

Nothing is uploaded automatically. upload_to_drive is YELLOW zone —
human approval required before any file lands in Drive.
"""

from __future__ import annotations
from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.web_search import WebSearchTool  # if available
from core.tools.document.generate_content import GenerateContentTool
from core.tools.document.create_pdf import CreatePdfTool
from core.tools.document.create_docx import CreateDocxTool
from core.tools.document.export_csv import ExportCsvTool
from core.tools.document.upload_to_drive import UploadToDriveTool


_SYSTEM_PROMPT = """You are DocumentAgent, the KRYPSOS AI assistant for professional document creation.

You create polished PDF reports, Word documents, and CSV exports — then upload them to Google Drive.

=== WORKFLOW ===

Standard workflow for creating a document:

1. generate_content   — understand the request and generate structured section content
2. create_pdf         — if user wants a PDF (default for reports)
   OR create_docx     — if user wants a Word document
   OR export_csv      — if user wants a spreadsheet/CSV
3. upload_to_drive    — ONLY after explicit human approval (YELLOW zone)

After upload, the Drive link is returned in the task result as a deliverable.

=== DOCUMENT TYPE GUIDE ===

Use the right format for the request:
  PDF   → reports, summaries, proposals, letters, anything to be shared or printed
  DOCX  → editable documents, templates, documents user wants to modify later
  CSV   → structured data, lists, exports (invoices, contacts, task lists, etc.)

=== WORKING WITH SOURCE DATA ===

If the user provides emails, task history, or raw text:
- Pass it as source_data to generate_content
- The LLM will use it to write accurate content
- Always mention in the document where the data came from

If the user asks for something like "export my tasks as CSV":
- Build the rows from the available task data
- Use export_csv directly (no need for generate_content for CSV)

=== SECTION WRITING RULES ===

When writing document sections after generate_content:
- Write fully — do not truncate or leave placeholders like "[add details here]"
- Use specific facts, numbers, and dates from the source data
- Be professional and concise — no filler phrases
- For bullet points, start lines with "- "

=== HARD RULES ===

1. NEVER upload to Drive without explicit human approval (upload_to_drive is YELLOW zone)
2. ALWAYS call generate_content before create_pdf or create_docx
3. For CSV, you may call export_csv directly if you already have the tabular data
4. If Drive is not connected, inform the user and still create the local file
5. After upload, always include the drive_url in your final answer

=== AVAILABLE TOOLS ===

Document tools:
  generate_content  : LLM generates structured section content — call FIRST
  create_pdf        : Creates a PDF file from sections (GREEN — auto)
  create_docx       : Creates a Word .docx file from sections (GREEN — auto)
  export_csv        : Creates a CSV file from tabular data (GREEN — auto)
  upload_to_drive   : Uploads file to Google Drive (YELLOW — REQUIRES human approval)

General tools:
  calculator        : Compute totals, percentages, date differences
  current_time      : Current date/time for document headers

Typical flow:
  generate_content → create_pdf → [human approves] → upload_to_drive → return drive_url
"""


class DocumentAgent(BaseAgent):
    """KRYPSOS Document Agent — create and upload professional documents.

    Generates PDFs, Word docs, and CSVs from prompts, email data,
    task history, or raw text. Uploads to Google Drive after approval.

    Default limits:
        max_steps : 15  (generate + create + upload = ~5 steps typical)
        max_cost  : $0.30 per task

    Example (production)::

        agent = DocumentAgent(llm_provider=GroqProvider(), workspace_id="<uuid>")
        result = agent.run("Write a Q2 sales report and upload to Drive.")

    Example (tests)::

        agent = DocumentAgent(
            llm_provider=MockLLMProvider(responses),
            workspace_id=None,
        )
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        task_id: str | None = None,
        workspace_id: str | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        self._workspace_id = workspace_id

        default_tools = [
            GenerateContentTool(),
            CreatePdfTool(),
            CreateDocxTool(),
            ExportCsvTool(),
            UploadToDriveTool(workspace_id=workspace_id),
            CalculatorTool(),
            CurrentTimeTool(),
        ]

        # Optional web search if available
        try:
            default_tools.append(WebSearchTool())
        except Exception:
            pass

        tools = default_tools + (extra_tools or [])

        super().__init__(
            name="DocumentAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=15,
            max_cost=0.30,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
