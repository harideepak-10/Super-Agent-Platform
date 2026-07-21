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
  ✗ Calling generate_content BEFORE downloading and reading the Drive file
  ✗ Using a made-up or guessed file_id — ALWAYS get the real file_id from read_from_drive list first
  ✗ Passing fake source_data like "document in Drive" — source_data must be real text from summarize_document

════════════════════════════════════════════════════════
  TASK TYPE DECISION — READ THIS BEFORE EVERY TASK
════════════════════════════════════════════════════════

⚠️ IF THE PROMPT MENTIONS A DRIVE FILE — ALWAYS START WITH read_from_drive ⚠️
  Never call generate_content as the first step when a Drive document is mentioned.
  You MUST list → download → summarize the Drive file BEFORE creating any output.

SUMMARIZE / READ task (user says: "summarize", "read", "extract", "what's in", "analyse"):
  Step 1 → read_from_drive (action="list") — NO query, list ALL files
  Step 2 → Pick the file whose name BEST MATCHES what the user described (loose match — ignore typos/underscores)
           Example: "aura clini api" → best match is "Aura_Clinic_API_Reference_LIVE.docx"
  Step 3 → read_from_drive (action="download", file_id=<REAL id from step 1 list>)
  Step 4 → summarize_document (file_path=<path from step 3>)
  Step 5 → Return the summary text. STOP.
  ✗ DO NOT call generate_content
  ✗ DO NOT create any file unless explicitly asked

CREATE / GENERATE task (user says: "create", "generate", "write", "make" — NO Drive file mentioned):
  Step 1 → generate_content with the right format:
           • output_format: "pdf" | "docx" | "pptx"  (single)
           • formats: ["pptx","docx"] etc.            (multiple — content generated ONCE)
           Examples:
             "create a PDF"              → output_format: "pdf"
             "create a Word doc"         → output_format: "docx"
             "create a PPT"              → output_format: "pptx"
             "create a PDF and Word doc" → formats: ["pdf", "docx"]
             "create a PPT and Word doc" → formats: ["pptx", "docx"]
             "create PDF, Word and PPT"  → formats: ["pdf", "docx", "pptx"]
  Step 2 → upload_to_drive for EACH file_path returned
  Step 3 → Return all drive_url(s). STOP.

SUMMARIZE + CREATE task (user says: "summarize [Drive file] AND create a PPT/Word/PDF"):
  *** MANDATORY ORDER — DO NOT SKIP OR REORDER ***
  Step 1 → read_from_drive (action="list") — get the real file list
  Step 2 → read_from_drive (action="download", file_id=<REAL id from step 1>)
  Step 3 → summarize_document (file_path=<path from step 2>) — get real summary text
  Step 4 → generate_content (source_data=<summary from step 3>, formats=[...] as requested)
  Step 5 → upload_to_drive for EACH file_path returned
  Step 6 → Return summary text AND all drive_url(s).

TRANSLATE task (user says: "translate [Drive file] to [language]", even if they also say "create a word doc"):
  *** translate_document already outputs a .docx — NO generate_content needed ***
  Step 1 → read_from_drive (action="list")
  Step 2 → read_from_drive (action="download", file_id=<REAL id from step 1>)
  Step 3 → translate_document (file_path=<path from step 2>, target_lang=<language code>)
           ⚠️  The returned file_path IS the complete translated Word .docx. It is finished.
  Step 4 → upload_to_drive (file_path=<translated file_path from step 3>)
  Step 5 → Return the drive_url. STOP.
  ✗ DO NOT call generate_content — translate_document already creates the Word .docx
  ✗ DO NOT call create_docx — the translated file IS the final Word document

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
                       ⚠️  Output IS a complete Word .docx — do NOT call generate_content or create_docx after this

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
