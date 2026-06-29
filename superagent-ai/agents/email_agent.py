"""
Email Agent -- full 10-step email workflow.

EmailAgent orchestrates:
  1.  Read/search emails from Gmail (GREEN)
  2.  Retrieve full conversation threads (GREEN)
  3.  Classify each email by type (GREEN)
  4.  Summarise threads for context (GREEN)
  5.  Extract action items and deadlines (GREEN)
  6.  Draft a tailored, context-aware reply (GREEN)
  7.  Request human approval, then send (YELLOW)

Nothing is ever sent automatically.  send_email is YELLOW zone, so
BaseAgent raises ApprovalRequired before it executes.
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
from core.tools.gmail.classify_email import ClassifyEmailTool
from core.tools.gmail.summarize_thread import SummarizeThreadTool
from core.tools.gmail.extract_action_items import ExtractActionItemsTool
from core.tools.gmail.draft_reply import DraftReplyTool
from core.tools.gmail.send_email import SendEmailTool


_SYSTEM_PROMPT = """You are EmailAgent, an AI assistant for email management.

Workflow:
STEP 1 - Read/Search: Use read_emails or search_emails to find emails.
STEP 2 - Thread: Use get_thread to retrieve the full conversation.
STEP 3 - Classify: Use classify_email to determine type and priority.
STEP 4 - Summarise: Use summarize_thread for long threads.
STEP 5 - Actions: Use extract_action_items to find tasks and deadlines.
STEP 6 - Draft: Use draft_reply to prepare a context-aware reply.
STEP 7 - Present: Show the draft to the human operator.
STEP 8 - Send: Use send_email ONLY after explicit human approval.

Email types: invoice, supplier_inquiry, customer_complaint, newsletter,
contract, payment_confirmation, other.

Hard rules:
1. NEVER send an email without explicit human approval.
2. NEVER share sensitive email content with third parties.
3. Always extract action items from emails with tasks or deadlines.
4. When in doubt, ask the human operator.

Tools (GREEN = auto, YELLOW = needs approval):
- read_emails:           Fetch recent emails (GREEN).
- search_emails:         Search Gmail with query syntax (GREEN).
- get_thread:            Retrieve full conversation thread (GREEN).
- classify_email:        Classify email by type and urgency (GREEN).
- summarize_thread:      Summarise a thread into key points (GREEN).
- extract_action_items:  Extract tasks, deadlines, follow-ups (GREEN).
- draft_reply:           Generate a draft reply -- never sends (GREEN).
- send_email:            Send email (YELLOW -- requires human approval).
- calculator:            Compute invoice totals, date arithmetic (GREEN).
- current_time:          Get current date/time (GREEN).
- echo:                  Testing and debugging only (GREEN).
"""


class EmailAgent(BaseAgent):
    """Specialised agent for Gmail-powered email management.

    Extends BaseAgent with a full 7-step workflow: read/search, thread
    retrieval, classification, summarisation, action-item extraction,
    reply drafting, and gated send.

    Default limits:
        max_steps : 20
        max_cost  : $0.50 per task

    Example (production)::

        agent = EmailAgent(llm_provider=GroqProvider())
        result = agent.run("Check my inbox and summarise unread emails.")

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
        extra_tools: list[Any] | None = None,
    ) -> None:
        default_tools = [
            ReadEmailsTool(gmail_service=gmail_service),
            SearchEmailsTool(gmail_service=gmail_service),
            GetThreadTool(gmail_service=gmail_service),
            ClassifyEmailTool(),
            SummarizeThreadTool(),
            ExtractActionItemsTool(),
            DraftReplyTool(),
            SendEmailTool(gmail_service=gmail_service),
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
