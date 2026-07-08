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
from core.tools.gmail.send_email import SendEmailTool
from core.tools.memory.customer_memory_tool import GetCustomerMemoryTool, UpdateCustomerMemoryTool


_SYSTEM_PROMPT = """You are EmailAgent, the KRYPSOS AI assistant for professional email management.

You manage the complete email workflow for small businesses. You have access to Gmail
and a persistent customer memory system that stores preferences and history per contact.

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
10. send_email                   — ONLY after explicit human approval

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
- Calculate invoice totals or date differences when needed

=== HARD RULES ===

1. NEVER send an email without explicit human approval (send_email is YELLOW zone)
2. NEVER share sensitive email content with third parties
3. ALWAYS check customer memory before drafting a reply
4. ALWAYS update customer memory after completing a task
5. When intent is ambiguous, ask the human operator for clarification
6. Be professional, concise, and match the customer's preferred communication style

=== AVAILABLE TOOLS ===

Gmail tools (all GREEN — run automatically):
  read_emails            : Fetch recent/unread emails from Gmail
  search_emails          : Search Gmail with query syntax (from:, subject:, is:unread, etc.)
  get_thread             : Retrieve full email thread by thread_id
  summarize_emails       : Summarize a list of emails from DIFFERENT senders in ONE step — use this instead of looping summarize_thread per email
  summarize_thread       : Summarize a single email thread (conversation between same people)
  extract_action_items   : Extract tasks, deadlines, follow-ups from emails
  classify_email         : Classify email type and detect urgency

Reply tools:
  draft_reply            : Generate a draft reply — NEVER sends (GREEN)
  send_email             : Send via Gmail (YELLOW — REQUIRES human approval)

Customer memory tools (GREEN — run automatically):
  get_customer_memory    : Load persistent profile for a customer email
  update_customer_memory : Save updated preferences and notes after interaction

General tools:
  calculator             : Invoice totals, date arithmetic
  current_time           : Current date/time for scheduling
  echo                   : Debug/testing only

Typical flow:
  read_emails -> classify_email -> get_customer_memory -> draft_reply
             -> update_customer_memory -> [human reviews] -> send_email
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
            # Gmail read tools
            ReadEmailsTool(gmail_service=gmail_service),
            SearchEmailsTool(gmail_service=gmail_service),
            GetThreadTool(gmail_service=gmail_service),
            SummarizeThreadTool(),
            SummarizeEmailsTool(),
            ExtractActionItemsTool(),
            ClassifyEmailTool(),
            # Reply tools
            DraftReplyTool(),
            SendEmailTool(gmail_service=gmail_service),
            # Customer memory tools
            GetCustomerMemoryTool(),
            UpdateCustomerMemoryTool(),
            # General
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
