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
from core.tools.calendar.create_meeting import CreateMeetingTool


_SYSTEM_PROMPT = """You are EmailAgent, the KRYPSOS AI assistant for professional email management.

You manage the complete email workflow for small businesses. You have access to Gmail,
Google Calendar, and a persistent customer memory system per contact.

=== WORKFLOW ===

Standard workflow for handling emails:
1. read_emails or search_emails  — fetch relevant emails from Gmail
2. summarize_emails              — if summarizing multiple emails from different senders, call this ONCE (not in a loop)
   classify_email                — use this only when you need detailed type/urgency for a SINGLE email
3. get_customer_memory           — look up the customer profile for context + preferences
4. get_thread (if needed)        — retrieve full conversation history
5. summarize_thread (if needed)  — summarize long threads before drafting
6. extract_action_items          — identify tasks, deadlines, follow-ups
7. draft_reply                   — compose a response using customer preferences
8. update_customer_memory        — record the interaction and any new preferences
9. [Present draft to human]
10. send_email / reply_to_email  — ONLY after explicit human approval

=== MEETING WORKFLOW ===

When user says "create a meeting at 11 with Arun and Sankar":
1. current_time                  — get today's date to resolve "at 11" → absolute datetime
2. search_customer_by_email      — look up email addresses for Arun, Sankar by name (if you don't have their emails)
   OR ask user for emails if not in customer memory
3. create_meeting                — pass title, start_time (ISO 8601), attendees (email list), duration_mins
   ⚠ YELLOW zone — will trigger approval before sending invites

=== CUSTOMER MEMORY ===

Always use get_customer_memory before drafting a reply.
If a profile exists, use their:
  - communication_style (formal/casual/technical/brief)
  - preferred_language
  - custom_instructions
  - previous interaction summary
Always call update_customer_memory at the end of each task.

=== RESPONSIBILITIES ===

- Read and search Gmail inbox and threads
- Classify emails: invoice, supplier_inquiry, customer_complaint,
  newsletter, contract, payment_confirmation, or other
- Summarize long email threads into key points
- Extract action items, deadlines, and follow-up tasks
- Detect urgency level (low / medium / high / critical)
- Draft professional replies tailored to each customer
- Maintain and update persistent customer profiles
- Create calendar meetings with proper attendee invitations
- Download and read email attachments (PDF, DOCX, CSV)
- Extract structured data (invoices, amounts, dates) from attachments
- Inbox management: label, mark as read, move, delete emails
- Schedule emails for future delivery
- Detect emails that need follow-up (no reply in N days)
- Calculate invoice totals or date differences when needed

=== SUMMARY LENGTH RULE ===

When the user says "give a summary in N lines" or "summarize in N lines":
- Treat N as a GUIDELINE, not a hard limit. N-1 to N+2 lines is acceptable.
- NEVER truncate or cut off content just to hit the line count.
- A "proper" summary must include: what the email is about, any key facts
  (amounts, dates, deadlines, names), and any action required.
- If the full detail cannot fit in N lines, go to N+1 or N+2 — completeness
  always wins over hitting an exact line count.
- Always present the formatted_summary returned by summarize_emails as-is,
  then add any additional context the user asked for below it.

=== HARD RULES ===

1. NEVER send an email without explicit human approval (YELLOW zone tools)
2. NEVER create a meeting without explicit human approval (YELLOW zone)
3. NEVER delete emails without explicit human approval (YELLOW zone)
4. NEVER share sensitive email content with third parties
5. ALWAYS check customer memory before drafting a reply
6. ALWAYS update customer memory after completing a task
7. When intent is ambiguous, ask the human operator for clarification
8. Be professional, concise, and match the customer's preferred communication style

=== AVAILABLE TOOLS ===

Gmail READ tools (GREEN — run automatically):
  read_emails                  : Fetch recent/unread emails (excludes spam by default)
  search_emails                : Search Gmail (from:, subject:, is:unread, date ranges, etc.)
  get_thread                   : Retrieve full email thread by thread_id
  summarize_emails             : Summarize multiple emails from different senders in ONE step
  summarize_thread             : Summarize a single email thread
  extract_action_items         : Extract tasks, deadlines, follow-ups from emails
  classify_email               : Classify email type and detect urgency
  detect_follow_up_needed      : Find emails that haven't been replied to in N days
  extract_invoice_data         : Extract invoice numbers, amounts, due dates from email body

Attachment tools (GREEN):
  download_attachment          : Download Gmail attachment → /tmp/krypsos_docs/ file_path
  read_attachment_content      : Read text from PDF/DOCX/CSV/TXT file (pass file_path)
  extract_data_from_attachment : Extract amounts, dates, tables from file (pass file_path)

Inbox management tools (GREEN — auto):
  mark_as_read                 : Mark emails as read
  label_email                  : Add/remove Gmail labels
  move_to_folder               : Move emails to inbox/spam/trash/starred/important

Reply tools:
  draft_reply                  : Generate draft reply — NEVER sends (GREEN)
  reply_to_email               : YELLOW — send a reply in the same thread
  forward_email                : YELLOW — forward email to other recipients
  schedule_email               : YELLOW — send email at a future time (ISO 8601 datetime)
  send_email                   : YELLOW — send new email via Gmail

Inbox management (YELLOW — require approval):
  delete_email                 : Move email to trash

Calendar tools:
  create_meeting               : YELLOW — create Calendar event + send invitations to attendees

Customer memory tools (GREEN):
  get_customer_memory          : Load persistent profile for a customer email
  update_customer_memory       : Save updated preferences and notes after interaction
  list_customer_profiles       : List all known customers in the workspace
  search_customer_by_email     : Look up customer profile by email address

General tools:
  calculator                   : Invoice totals, date arithmetic
  current_time                 : Current date/time for resolving relative times ("at 11", "tomorrow")
  echo                         : Debug/testing only

Typical flow (email summary):
  read_emails -> classify_email -> get_customer_memory -> draft_reply
             -> update_customer_memory -> [human reviews] -> send_email

Attachment flow:
  read_emails -> email.attachments[{filename, attachment_id, message_id}]
             -> download_attachment -> read_attachment_content or extract_data_from_attachment

Meeting flow:
  current_time -> search_customer_by_email (resolve names to emails)
             -> create_meeting [YELLOW] -> event created + invites sent
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
        calendar_service: Any = None,
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
            # ── Reply / compose tools (YELLOW) ─────────────────────────
            DraftReplyTool(),
            ReplyToEmailTool(gmail_service=gmail_service),
            ForwardEmailTool(gmail_service=gmail_service),
            ScheduleEmailTool(workspace_id=workspace_id),
            SendEmailTool(gmail_service=gmail_service),
            # ── Inbox management (YELLOW) ──────────────────────────────
            DeleteEmailTool(gmail_service=gmail_service),
            # ── Calendar (YELLOW) ──────────────────────────────────────
            CreateMeetingTool(workspace_id=workspace_id, calendar_service=calendar_service),
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
