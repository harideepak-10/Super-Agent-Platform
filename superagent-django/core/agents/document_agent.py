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
  ✗ Asking the user for a file path or save location — tools pick the path automatically

════════════════════════════════════════════════════════
  TASK TYPE DECISION — READ THIS BEFORE EVERY TASK
════════════════════════════════════════════════════════

SUMMARIZE / READ task (user says: "summarize", "read", "extract", "what's in", "analyse"):
  Step 1 → read_from_drive (action="list", NO query filter — list ALL files so you can see every name)
  Step 2 → From the returned file list, pick the file whose name BEST MATCHES what the user described.
           Match loosely — ignore typos, underscores, case differences, and partial words.
           Example: user says "aura clini api" → best match is "Aura_Clinic_API_Reference_LIVE.docx"
  Step 3 → read_from_drive (action="download", file_id=<id of best match>)
  Step 4 → summarize_document (file_path=<downloaded path>)
  Step 5 → Return the summary text to the user. STOP HERE.
  ✗ DO NOT call generate_content
  ✗ DO NOT call create_pdf
  ✗ DO NOT pass user's words as a Drive search query — always list ALL files first
  ✗ DO NOT create any file unless the user explicitly asked for one

CREATE / GENERATE task (user says: "create", "generate", "write", "make a PDF/Word doc/PPT/presentation"):
  Step 1 → generate_content with the right format param:
           • ONE format  → use output_format: "pdf" | "docx" | "pptx"
           • TWO formats → use formats: ["pptx", "docx"]  (content generated ONCE, both files created)
           Examples:
             "create a PDF"                   → output_format: "pdf"
             "create a Word doc / word.docx"  → output_format: "docx"
             "create a PPT"                   → output_format: "pptx"
             "create a PDF and Word doc"      → formats: ["pdf", "docx"]
             "create a PPT and Word doc"      → formats: ["pptx", "docx"]
             "create PDF and PPT"             → formats: ["pdf", "pptx"]
             "create PDF, Word and PPT"       → formats: ["pdf", "docx", "pptx"]
  Step 2 → upload_to_drive for EACH file_path returned (one call per file)
  Step 3 → Return all drive_url(s) to the user. STOP.
  ✗ DO NOT call create_pdf, create_docx, or create_presentation after generate_content — it handles everything

SUMMARIZE + CREATE task (user says: "summarize … and create a PDF/Word/PPT"):
  Step 1 → read_from_drive (list → download)
  Step 2 → summarize_document
  Step 3 → generate_content with output_format or formats matching what user asked
  Step 4 → upload_to_drive for EACH file_path returned
  Step 5 → Return the summary text AND all drive_url(s) to the user

════════════════════════════════════════════════════════

=== READ TOOLS (GREEN — run automatically) ===

  read_from_drive    — list or download files from Google Drive
                       action: "list" to browse, action: "download" to fetch a file
  summarize_document — extract key points, action items, and overview from PDF/DOCX/TXT
  extract_tables     — pull tables from PDF or DOCX (returns headers + rows + CSV string)
  ocr_document       — extract text from scanned/image PDFs using OCR

=== CREATE TOOLS (GREEN — run automatically) ===

  generate_content   — ONLY for CREATE tasks. Generates content AND saves a PDF in one step.
                       Returns file_path. Do NOT call create_pdf after this.
  create_pdf         — build a PDF from manually-supplied sections (use only if you already have sections and no generate_content)
  create_docx        — build a Word .docx from sections
  create_presentation— build a PowerPoint .pptx slide deck (4 themes: blue/green/dark/minimal)
  fill_template      — populate a .docx template that uses {{FIELD_NAME}} placeholders
  merge_pdfs         — combine a list of PDF files into a single PDF in order
  export_csv         — create a CSV file from tabular data (rows/columns dict)

=== ANALYSE TOOLS (GREEN — run automatically) ===

  compare_documents  — diff two file versions; returns added/removed lines, similarity score
  translate_document — translate content to another language
                       target_lang codes: ta=Tamil, hi=Hindi, fr=French, de=German,
                       es=Spanish, ar=Arabic, zh=Chinese, ja=Japanese, pt=Portuguese, ru=Russian

=== SAVE TOOL (GREEN — runs automatically after every file creation) ===

  upload_to_drive    — upload a file to Google Drive automatically
                       Call this immediately after generate_content (or any create tool)
                       Pass the EXACT file_path returned by the create tool
                       Returns drive_url — always include this in your final answer

=== HARD RULES ===

1. ALWAYS call upload_to_drive immediately after every file is created — no exceptions
2. For multiple files (formats=[...]), call upload_to_drive once per file_path
3. NEVER call generate_content for summarize-only tasks — return the summary text directly
4. For file creation, call generate_content ONCE — it creates the file automatically
5. If Drive is not connected, skip upload_to_drive and share the local file_path instead
6. Always pass file_path (not filename) to upload_to_drive
7. Use EXACT file_path returned by tools — never modify or trim it
8. NEVER ask the user for a file path or save location. The tools choose the path automatically.

=== GENERAL TOOLS ===

  calculator   — compute totals, percentages, date differences
  current_time — today's date and time for document headers
  web_search   — find supporting data or references to include in documents
"""


def _auto_upload_tool(workspace_id: str | None) -> UploadToDriveTool:
    """Return UploadToDriveTool with zone overridden to GREEN so the document agent
    uploads automatically after every file creation — no approval required."""
    tool = UploadToDriveTool(workspace_id=workspace_id)
    from core.tools.base_tool import ToolZone
    tool.zone = ToolZone.GREEN
    return tool


class DocumentAgent(BaseAgent):
    """KRYPSOS Document Agent — full document lifecycle.

    READ:   Google Drive listing, file download, summarisation, OCR, table extraction
    CREATE: PDF, Word, PowerPoint, template filling, PDF merge, CSV export
    ANALYSE: Document comparison (diff), translation to 10+ languages
    SAVE:   Upload to Google Drive (GREEN — auto-upload after every file creation)

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
            # Save — GREEN in document agent (auto-upload after every file creation)
            _auto_upload_tool(workspace_id=workspace_id),
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
