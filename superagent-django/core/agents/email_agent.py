"""
Email Agent — manages the full email lifecycle for KRYPSOS.

Responsibilities (per architecture guide):
  1. Read Emails          (read_emails         — GREEN)
  2. Search Emails        (search_emails        — GREEN)
  3. Retrieve Threads     (get_thread           — GREEN)
  4. Summarize Conversations (summarize_thread  — GREEN)
  5. Extract Action Items (extract_action_items — GREEN)
  6. Detect Urgency       (classify_email       — GREEN)
  7. Draft Responses      (draft_reply          — GREEN)
  8. Read Customer Memory (get_customer_memory  — GREEN)
  9. Update Customer Memory (update_customer_memory — GREEN)
 10. Send Approved Emails (send_email           — YELLOW, human approval required)

Nothing is ever sent automatically. send_email is YELLOW zone, so
BaseAgent raises ApprovalRequired before execution.

Tools accept an injectable service for testing:
    EmailAgent(llm_provider=mock, gmail_service=mock_service)
"""

from __future__ import annotations
from typing import Any

from core.base_agent import BaseAgent
from core.llm.base import LLMProvider
from core.tools.calculator import CalculatorTool
from core.tools.current_time import CurrentTimeTool
from core.tools.echo import EchoTool
from core.tools.gmail.read_emails import ReadEmailsTool
from core.tools.gmail.search_emails import SearchEmailsTool
from core.tools.gmail.get_thread import GetThreadTool
from core.tools.gmail.summarize_thread import SummarizeThreadTool
from core.tools.gmail.summarize_emails import SummarizeEmailsTool
from core.tools.gmail.extract_action_items import ExtractActionItemsTool
from core.tools.gmail.classify_email import ClassifyEmailTool
from core.tools.gmail.draft_reply import DraftReplyTool
from core.tools.gmail.create_gmail_draft import CreateGmailDraftTool
from core.tools.gmail.download_attachment import DownloadAttachmentTool
from core.tools.gmail.read_attachment_content import ReadAttachmentContentTool
from core.tools.gmail.extract_data_from_attachment import ExtractDataFromAttachmentTool
from core.tools.gmail.mark_as_read import MarkAsReadTool
from core.tools.gmail.label_email import LabelEmailTool
from core.tools.gmail.move_to_folder import MoveToFolderTool
from core.tools.gmail.delete_email import DeleteEmailTool
from core.tools.gmail.reply_to_email import ReplyToEmailTool
from core.tools.gmail.forward_email import ForwardEmailTool
from core.tools.gmail.schedule_email import ScheduleEmailTool
from core.tools.gmail.extract_invoice_data import ExtractInvoiceDataTool
from core.tools.gmail.detect_follow_up import DetectFollowUpTool
from core.tools.gmail.send_email import SendEmailTool
from core.tools.memory.customer_memory_tool import GetCustomerMemoryTool, UpdateCustomerMemoryTool
from core.tools.memory.list_customer_profiles import ListCustomerProfilesTool
from core.tools.memory.search_customer_by_email import SearchCustomerByEmailTool


_SYSTEM_PROMPT = """You are EmailAgent, the KRYPSOS AI assistant for professional email management.

=== CORE WORKFLOW ===

Reading / summarizing emails:
  1. read_emails  → returns {"emails": [...], "count": N}
  2. summarize_emails(emails=result["emails"])  → returns formatted_summary
  3. Present formatted_summary directly to the user — do not rewrite it.

Drafting a reply:
  1. read_emails or search_emails
  2. get_customer_memory(email=sender_email)
  3. draft_reply
  4. update_customer_memory
  5. [wait for human approval] → send_email / reply_to_email

Reading an attachment:
  1. read_emails → find email where has_attachments is true
  2. download_attachment(message_id, attachment_id, filename)  → returns file_path
  3. read_attachment_content(file_path)  → returns text content
  4. Summarize the content in your final answer

=== READ EMAIL RULES ===

ALWAYS use filter "-in:spam -in:trash" by default (ALL emails, read + unread).
ONLY use "is:unread" if the user explicitly says "unread" or "new emails".

  "read my last 5 emails"   → filter: "-in:spam -in:trash", limit: 5
  "recent emails"           → filter: "-in:spam -in:trash", limit: 10
  "unread emails"           → filter: "is:unread -in:spam -in:trash"

CRITICAL — If read_emails returns 0 emails or an empty list:
  → You MUST immediately call search_emails with query="" and max_results=10
  → Do NOT give any text response until you have called search_emails
  → Only say "no emails found" if search_emails ALSO returns 0 results
  → NEVER describe, assume, or guess what emails might contain

=== SUMMARIZE EMAILS RULE ===

After read_emails returns emails, call summarize_emails passing the emails array.
Present the formatted_summary exactly as returned — never rewrite or rephrase it.
NEVER invent, assume, or describe email content you didn't receive from the tool.

=== SEND vs DRAFT RULE ===

"create a draft", "write an email", "draft an email to X" → use send_email (YELLOW, needs approval)
"save as draft", "don't send yet", "save to drafts folder" → use create_gmail_draft (GREEN)
"send", "reply" → send_email / reply_to_email (YELLOW, needs approval)

=== ATTACHMENT RULES ===

ALWAYS follow this sequence for attachments — never skip steps:
  1. read_emails → find email with has_attachments: true
  2. Get attachment_id, message_id, filename from attachments[]
  3. download_attachment(message_id, attachment_id, filename)
  4. read_attachment_content(file_path)
  5. Summarize content in final answer

NEVER say "tool doesn't support attachments" — you have all the tools needed.

=== HARD RULES ===

1. NEVER send email without human approval (YELLOW zone)
2. NEVER delete email without human approval
3. NEVER create meetings without human approval
4. ALWAYS use get_customer_memory before drafting replies
5. ALWAYS update_customer_memory after each task
6. NEVER mention tool names, steps, or "please wait" in final answer

=== AVAILABLE TOOLS ===

READ (GREEN — auto):
  read_emails              → fetch emails. Returns {"emails":[...], "count":N}
  search_emails            → Gmail search syntax. Returns {"emails":[...]}
  get_thread               → full thread by thread_id
  summarize_emails         → summarize list of emails → formatted_summary
  summarize_thread         → summarize one thread
  extract_action_items     → tasks/deadlines from email text
  classify_email           → type + urgency for a single email
  detect_follow_up_needed  → emails with no reply in N days
  extract_invoice_data     → amounts, dates, invoice numbers

ATTACHMENTS (GREEN):
  download_attachment           → save attachment to disk, returns file_path
  read_attachment_content       → read text from PDF/DOCX/CSV/TXT by file_path
  extract_data_from_attachment  → extract structured data from file

INBOX MANAGEMENT (GREEN):
  mark_as_read    → mark emails as read by id
  label_email     → add/remove Gmail labels
  move_to_folder  → move to inbox/spam/trash/starred

COMPOSE (YELLOW — need approval):
  send_email        → send new email
  reply_to_email    → reply in thread
  forward_email     → forward to recipients
  schedule_email    → send at future ISO datetime
  delete_email      → move to trash

DRAFT (GREEN — no send):
  draft_reply        → generate reply text only
  create_gmail_draft → save to Drafts folder (not sent)

MEMORY (GREEN):
  get_customer_memory      → load profile by email address
  update_customer_memory   → save profile after interaction
  list_customer_profiles   → all known customers
  search_customer_by_email → find profile by email

GENERAL:
  calculator    → invoice totals, date math
  current_time  → current date/time

=== FINAL RESPONSE FORMAT ===

Your response must contain ONLY the actual result from the tools.
NEVER:
- Invent, guess, or describe email content you did not receive from a tool
- Use placeholder names like "Subject 1", "Sender 1", "example@email.com"
- Say "assuming the response is..." or "for example..."
- Say "I don't have the actual email content"
- Describe what a tool response might look like
- Include tool names, "please wait", "I will now", "I have successfully", or step descriptions

If you do not have real email data from a tool call, call the tool — do not describe it.
"""


class EmailAgent(BaseAgent):
    """KRYPSOS Email Agent — full email lifecycle with customer memory.

    Implements all responsibilities from the KRYPSOS Email Agent Architecture Guide:
    read, search, thread retrieval, summarization, action item extraction,
    urgency detection, drafting, customer memory, and approval-gated sending.

    Default limits:
        max_steps : 20  (increased to accommodate memory + summarization steps)
        max_cost  : $0.50 per task

    Example (production)::

        agent = EmailAgent(llm_provider=GroqProvider())
        result = agent.run("Summarize unread emails and extract action items.")

    Example (tests)::

        agent = EmailAgent(
            llm_provider=MockLLMProvider(responses),
            gmail_service=MockGmailService(messages),
        )
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        task_id: str | None = None,
        gmail_service: Any = None,
        workspace_id: str | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        self._workspace_id = workspace_id

        default_tools = [
            # ── Gmail read tools (GREEN) ───────────────────────────────
            ReadEmailsTool(gmail_service=gmail_service),
            SearchEmailsTool(gmail_service=gmail_service),
            GetThreadTool(gmail_service=gmail_service),
            SummarizeThreadTool(),
            SummarizeEmailsTool(),
            ExtractActionItemsTool(),
            ClassifyEmailTool(),
            DetectFollowUpTool(gmail_service=gmail_service),
            ExtractInvoiceDataTool(),
            # ── Attachment tools (GREEN) ───────────────────────────────
            DownloadAttachmentTool(gmail_service=gmail_service),
            ReadAttachmentContentTool(),
            ExtractDataFromAttachmentTool(),
            # ── Inbox management (GREEN) ───────────────────────────────
            MarkAsReadTool(gmail_service=gmail_service),
            LabelEmailTool(gmail_service=gmail_service),
            MoveToFolderTool(gmail_service=gmail_service),
            # ── Reply / compose tools ─────────────────────────────────
            DraftReplyTool(),
            CreateGmailDraftTool(gmail_service=gmail_service, workspace_id=workspace_id),
            ReplyToEmailTool(gmail_service=gmail_service),
            ForwardEmailTool(gmail_service=gmail_service),
            ScheduleEmailTool(workspace_id=workspace_id),
            SendEmailTool(gmail_service=gmail_service),
            # ── Inbox management (YELLOW) ──────────────────────────────
            DeleteEmailTool(gmail_service=gmail_service),
            # ── Customer memory (GREEN) ────────────────────────────────
            GetCustomerMemoryTool(),
            UpdateCustomerMemoryTool(),
            ListCustomerProfilesTool(),
            SearchCustomerByEmailTool(),
            # ── General ────────────────────────────────────────────────
            CalculatorTool(),
            CurrentTimeTool(),
            EchoTool(),
        ]
        tools = default_tools + (extra_tools or [])

        super().__init__(
            name="EmailAgent",
            llm_provider=llm_provider,
            tools=tools,
            max_steps=20,
            max_cost=0.50,
            task_id=task_id,
        )

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT
