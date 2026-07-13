"""
Document Agent — full document lifecycle for KRYPSOS.

Responsibilities:
  READ:
    1. read_from_drive    — list or download files from Google Drive
    2. summarize_document — extract key points and action items from any file
    3. extract_tables     — pull tabular data from PDF / DOCX
    4. ocr_document       — OCR scanned PDFs to extract text

  CREATE:
    5. generate_content   — LLM generates structured section content (call first)
    6. create_pdf         — build PDF from sections
    7. create_docx        — build Word .docx from sections
    8. create_presentation— build PowerPoint .pptx slide deck
    9. fill_template      — populate a Word template with {{FIELD}} placeholders
   10. merge_pdfs         — combine multiple PDFs into one
   11. export_csv         — create CSV from tabular data

  ANALYSE:
   12. compare_documents  — diff two file versions
   13. translate_document — translate content to 10+ languages

  SAVE (YELLOW — requires human approval):
   14. upload_to_drive    — save completed file to Google Drive

Nothing is uploaded automatically. upload_to_drive is YELLOW zone —
human approval required before any file lands in Drive.
"""

from __future__ import annotations
from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.web_search import WebSearchTool

# Document — read
from core.tools.document.read_from_drive import ReadFromDriveTool
from core.tools.document.summarize_document import SummarizeDocumentTool
from core.tools.document.extract_tables import ExtractTablesTool
from core.tools.document.ocr_document import OcrDocumentTool

# Document — create
from core.tools.document.generate_content import GenerateContentTool
from core.tools.document.create_pdf import CreatePdfTool
from core.tools.document.create_docx import CreateDocxTool
from core.tools.document.create_presentation import CreatePresentationTool
from core.tools.document.fill_template import FillTemplateTool
from core.tools.document.merge_pdfs import MergePdfsTool
from core.tools.document.export_csv import ExportCsvTool

# Document — analyse
from core.tools.document.compare_documents import CompareDocumentsTool
from core.tools.document.translate_document import TranslateDocumentTool

# Document — save
from core.tools.document.upload_to_drive import UploadToDriveTool


_SYSTEM_PROMPT = """You are DocumentAgent, the KRYPSOS AI assistant for the full document lifecycle.

⚠️ AGENT BEHAVIOUR — READ FIRST ⚠️
You are an ACTIVE agent with real tools. You MUST call tools directly — never describe, narrate, or show pseudocode.

FORBIDDEN (will break the pipeline):
  ✗ Writing text like "I will call generate_content..." or "First, let's generate..."
  ✗ Showing Python snippets, code blocks, or pseudocode with function calls
  ✗ Explaining your plan before acting

REQUIRED:
  ✓ Your FIRST action on ANY document creation request MUST be a direct tool call to generate_content
  ✓ Tool call JSON must use the exact field names: title, doc_type, prompt, sections (optional)
  ✓ After generate_content returns file_path, give the user a short final answer with the path


=== READ TOOLS (GREEN — run automatically) ===

  read_from_drive    — list or download files from Google Drive
                       action: "list" to browse, action: "download" to fetch a file
  summarize_document — extract key points, action items, and overview from PDF/DOCX/TXT
  extract_tables     — pull tables from PDF or DOCX (returns headers + rows + CSV string)
  ocr_document       — extract text from scanned/image PDFs using OCR

=== CREATE TOOLS (GREEN — run automatically) ===

  generate_content   — generates content AND creates the PDF in one step. Returns file_path.
                       Call this ONCE for any PDF/report creation task. Do NOT call create_pdf after.
  create_pdf         — build a PDF from manually-supplied sections (use only if you already have sections)
  create_docx        — build a Word .docx from sections
  create_presentation— build a PowerPoint .pptx slide deck (4 themes: blue/green/dark/minimal)
  fill_template      — populate a .docx template that uses {{FIELD_NAME}} placeholders
                       Provide template_path + data dict; returns filled file
  merge_pdfs         — combine a list of PDF files into a single PDF in order
  export_csv         — create a CSV file from tabular data (rows/columns dict)

=== ANALYSE TOOLS (GREEN — run automatically) ===

  compare_documents  — diff two file versions; returns added/removed lines, similarity score
                       mode: "summary" (default) or "full_diff" for raw diff text
  translate_document — translate content to another language
                       target_lang codes: ta=Tamil, hi=Hindi, fr=French, de=German,
                       es=Spanish, ar=Arabic, zh=Chinese, ja=Japanese, pt=Portuguese, ru=Russian

=== SAVE TOOL (YELLOW — requires human approval) ===

  upload_to_drive    — save the completed file to Google Drive
                       Pass file_path from any create/translate/merge tool
                       Returns drive_url — include this in your final answer

=== WORKFLOW RULES ===

For DOCUMENT CREATION tasks (PDF/report/summary/proposal/letter):
  1. Call generate_content — it writes content AND saves the PDF automatically
  2. generate_content returns file_path — tell the user the file is ready
  3. [optional] upload_to_drive after human approval → return drive_url
  DO NOT call create_pdf separately after generate_content. It is redundant.

For TEMPLATE FILLING tasks:
  1. fill_template with template_path + data dict (no generate_content needed)
  2. [optional] upload_to_drive after approval

For READ / ANALYSE tasks:
  - Call read_from_drive, summarize_document, extract_tables, or ocr_document directly
  - No generate_content needed for read-only tasks

For COMPARISON tasks:
  - Call compare_documents with both file paths
  - Use mode="full_diff" only if user asks for the raw diff

For TRANSLATION tasks:
  - Call translate_document with file_path + target_lang
  - Default output_format is "docx"; use "txt" for plain text

=== HARD RULES ===

1. NEVER upload to Drive without explicit human approval (YELLOW zone)
2. For PDF creation, call generate_content ONCE — it creates the file automatically. Never call create_pdf after generate_content.
3. If Drive is not connected, still create the local file and share the path with the user
4. After Drive upload, always include drive_url in your final answer
5. Always pass file_path (not filename) to upload_to_drive
6. For merge_pdfs, ensure all input files exist before calling

=== GENERAL TOOLS ===

  calculator   — compute totals, percentages, date differences
  current_time — today's date and time for document headers
  web_search   — find supporting data or references to include in documents
"""


class DocumentAgent(BaseAgent):
    """KRYPSOS Document Agent — full document lifecycle.

    READ:   Google Drive listing, file download, summarisation, OCR, table extraction
    CREATE: PDF, Word, PowerPoint, template filling, PDF merge, CSV export
    ANALYSE: Document comparison (diff), translation to 10+ languages
    SAVE:   Upload to Google Drive (YELLOW — requires approval)

    Default limits:
        max_steps : 20  (read + create + analyse + upload = typical 5-8 steps)
        max_cost  : $0.50 per task

    Example (production)::

        agent = DocumentAgent(
            llm_provider=GroqProvider(),
            workspace_id="<uuid>",
            drive_service=build("drive", "v3", credentials=creds),
        )
        result = agent.run("Summarise the Q2 report in my Drive and translate it to Tamil.")

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
        drive_service: Any | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        self._workspace_id = workspace_id

        # kwargs shared by Drive-backed tools
        drive_kwargs = {"drive_service": drive_service} if drive_service else {}

        default_tools = [
            CurrentTimeTool(),
            CalculatorTool(),
            # Read
            ReadFromDriveTool(**drive_kwargs),
            SummarizeDocumentTool(),
            ExtractTablesTool(),
            OcrDocumentTool(),
            # Create
            GenerateContentTool(),
            CreatePdfTool(),
            CreateDocxTool(),
            CreatePresentationTool(),
            FillTemplateTool(),
            MergePdfsTool(),
            ExportCsvTool(),
            # Analyse
            CompareDocumentsTool(),
            TranslateDocumentTool(workspace_id=workspace_id),
            # Save (YELLOW)
            UploadToDriveTool(workspace_id=workspace_id),
        ]

        # Optional web search
        try:
            default_tools.append(WebSearchTool())
        except Exception:
            pass

        tools = default_tools + (extra_tools or [])

        super().__init__(
            name="DocumentAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=20,
            max_cost=0.50,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
