"""
Tests for Gmail tools: ReadEmailsTool, ClassifyEmailTool,
DraftReplyTool, SendEmailTool, SearchEmailsTool, GetThreadTool,
SummarizeThreadTool, ExtractActionItemsTool.

All Gmail API calls are intercepted by MockGmailService.
No real credentials, network calls, or email sends are made.
"""

from __future__ import annotations

import base64
import json

import pytest

from core.tools.gmail.read_emails import ReadEmailsTool
from core.tools.gmail.classify_email import ClassifyEmailTool
from core.tools.gmail.draft_reply import DraftReplyTool
from core.tools.gmail.send_email import SendEmailTool
from core.tools.gmail.search_emails import SearchEmailsTool
from core.tools.gmail.get_thread import GetThreadTool
from core.tools.gmail.summarize_thread import SummarizeThreadTool
from core.tools.gmail.extract_action_items import ExtractActionItemsTool
from core.tools.base_tool import ToolZone
from core.base_agent import BaseAgent, ApprovalRequired
from core.llm.mock_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64(text: str) -> str:
    """Base64url-encode a string the same way Gmail does."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _make_gmail_message(
    msg_id: str,
    subject: str,
    sender: str,
    date: str,
    body: str,
    has_attachment: bool = False,
    thread_id: str | None = None,
    snippet: str = "",
) -> dict:
    """Build a minimal Gmail API message dict."""
    parts = [
        {
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
            "filename": "",
        }
    ]
    if has_attachment:
        parts.append({
            "mimeType": "application/pdf",
            "body": {"attachmentId": "att001"},
            "filename": "invoice.pdf",
        })

    return {
        "id": msg_id,
        "threadId": thread_id or msg_id,
        "snippet": snippet or body[:100],
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/mixed" if has_attachment else "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@business.com"},
                {"name": "Date", "value": date},
            ],
            "parts": parts,
            "body": {"data": _b64(body) if not has_attachment else ""},
        },
    }


# ---------------------------------------------------------------------------
# MockGmailService
# ---------------------------------------------------------------------------


class _MockExecutable:
    def __init__(self, value) -> None:
        self._value = value

    def execute(self):
        return self._value


class _MockMessagesSend:
    def __init__(self, return_id: str = "sent_mock_001") -> None:
        self._return_id = return_id

    def execute(self):
        return {"id": self._return_id}


class _MockMessages:
    def __init__(self, messages: list[dict], send_id: str = "sent_mock_001") -> None:
        self._index = {m["id"]: m for m in messages}
        self._all_ids = [{"id": m["id"]} for m in messages]
        self._send_id = send_id

    def list(self, userId: str, q: str = "", maxResults: int = 10, **kwargs):  # noqa: N803
        return _MockExecutable({"messages": self._all_ids[:maxResults]})

    def get(self, userId: str, id: str, format: str = "full", **kwargs):  # noqa: A002
        msg = self._index.get(id, {})
        return _MockExecutable(msg)

    def send(self, userId: str, body: dict):
        return _MockMessagesSend(self._send_id)


class _MockThread:
    """Represents a single thread with multiple messages."""

    def __init__(self, thread_id: str, messages: list[dict]) -> None:
        self._thread_id = thread_id
        self._messages = messages

    def as_dict(self) -> dict:
        return {
            "id": self._thread_id,
            "messages": self._messages,
        }


class _MockThreads:
    def __init__(self, threads: dict[str, list[dict]]) -> None:
        # threads: { thread_id: [msg, msg, ...] }
        self._threads = threads

    def get(self, userId: str, id: str, format: str = "full", **kwargs):  # noqa: A002
        messages = self._threads.get(id, [])
        return _MockExecutable({"id": id, "messages": messages})


class _MockUsers:
    def __init__(self, messages: list[dict], threads: dict | None = None) -> None:
        self._messages = _MockMessages(messages)
        # Build default threads from messages if not explicitly provided:
        # group messages by threadId
        if threads is None:
            thread_map: dict[str, list[dict]] = {}
            for m in messages:
                tid = m.get("threadId", m["id"])
                thread_map.setdefault(tid, []).append(m)
            self._threads = _MockThreads(thread_map)
        else:
            self._threads = _MockThreads(threads)

    def messages(self):
        return self._messages

    def threads(self):
        return self._threads


class MockGmailService:
    """Mimics a googleapiclient Gmail resource object.

    Usage::

        service = MockGmailService(messages=[...])
        tool = ReadEmailsTool(gmail_service=service)
    """

    def __init__(
        self,
        messages: list[dict] | None = None,
        threads: dict[str, list[dict]] | None = None,
    ) -> None:
        self._users = _MockUsers(messages or [], threads=threads)

    def users(self):
        return self._users


class MockGmailServiceError:
    """A MockGmailService that always raises on API calls."""

    class _ErrorMessages:
        def list(self, **kwargs):
            raise RuntimeError("Simulated Gmail API failure")

        def get(self, **kwargs):
            raise RuntimeError("Simulated Gmail API failure")

        def send(self, **kwargs):
            raise RuntimeError("Simulated Gmail API failure")

    class _ErrorThreads:
        def get(self, **kwargs):
            raise RuntimeError("Simulated Gmail API failure")

    class _ErrorUsers:
        def messages(self):
            return MockGmailServiceError._ErrorMessages()

        def threads(self):
            return MockGmailServiceError._ErrorThreads()

    def users(self):
        return MockGmailServiceError._ErrorUsers()


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------


def _sample_messages():
    return [
        _make_gmail_message(
            "msg001", "Invoice #1042", "vendor@example.com",
            "Mon, 22 Jun 2026 10:00:00 +0000",
            "Please find attached invoice #1042 for $5,400.",
            has_attachment=True,
            thread_id="thread001",
        ),
        _make_gmail_message(
            "msg002", "Weekly Newsletter", "news@acme.com",
            "Mon, 22 Jun 2026 09:00:00 +0000",
            "This week's top stories and promotions.",
            thread_id="thread002",
        ),
    ]


# ---------------------------------------------------------------------------
# ReadEmailsTool tests
# ---------------------------------------------------------------------------


class TestReadEmailsTool:

    def _make_tool(self, messages=None) -> ReadEmailsTool:
        return ReadEmailsTool(gmail_service=MockGmailService(messages or []))

    def test_name_and_zone(self):
        assert self._make_tool().name == "read_emails"
        assert self._make_tool().zone == ToolZone.GREEN

    def test_returns_json_string(self):
        result = self._make_tool(_sample_messages()).run('{"limit": 10}')
        assert isinstance(json.loads(result), list)

    def test_returns_correct_count(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("{}"))
        assert len(emails) == 2

    def test_email_has_required_fields(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("{}"))
        required = {"id", "subject", "sender", "date", "body_preview", "full_body", "has_attachments"}
        for email in emails:
            assert required.issubset(email.keys())

    def test_subject_is_correct(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("{}"))
        subjects = {e["subject"] for e in emails}
        assert "Invoice #1042" in subjects
        assert "Weekly Newsletter" in subjects

    def test_has_attachments_flag(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("{}"))
        invoice = next(e for e in emails if e["subject"] == "Invoice #1042")
        newsletter = next(e for e in emails if e["subject"] == "Weekly Newsletter")
        assert invoice["has_attachments"] is True
        assert newsletter["has_attachments"] is False

    def test_full_body_decoded(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("{}"))
        invoice = next(e for e in emails if e["subject"] == "Invoice #1042")
        assert "invoice" in invoice["full_body"].lower()

    def test_empty_mailbox(self):
        assert json.loads(self._make_tool([]).run("{}")) == []

    def test_api_failure_graceful(self):
        result = json.loads(ReadEmailsTool(gmail_service=MockGmailServiceError()).run("{}"))
        assert "error" in result
        assert result["emails"] == []

    def test_respects_limit(self):
        emails = json.loads(self._make_tool(_sample_messages()).run('{"limit": 1}'))
        assert len(emails) == 1

    def test_non_json_fallback(self):
        emails = json.loads(self._make_tool(_sample_messages()).run("get my emails please"))
        assert isinstance(emails, list)


# ---------------------------------------------------------------------------
# SearchEmailsTool tests
# ---------------------------------------------------------------------------


class TestSearchEmailsTool:

    def _make_tool(self, messages=None) -> SearchEmailsTool:
        return SearchEmailsTool(gmail_service=MockGmailService(messages or []))

    def test_name_and_zone(self):
        tool = self._make_tool()
        assert tool.name == "search_emails"
        assert tool.zone == ToolZone.GREEN

    def test_returns_dict_with_emails_key(self):
        result = self._make_tool(_sample_messages()).run(
            json.dumps({"query": "is:unread"})
        )
        assert "emails" in result
        assert "total" in result
        assert "query" in result

    def test_query_echoed_in_result(self):
        result = self._make_tool(_sample_messages()).run(
            json.dumps({"query": "subject:invoice"})
        )
        assert result["query"] == "subject:invoice"

    def test_returns_matching_emails(self):
        result = self._make_tool(_sample_messages()).run(
            json.dumps({"query": "is:unread", "max_results": 10})
        )
        assert result["total"] == 2

    def test_respects_max_results(self):
        result = self._make_tool(_sample_messages()).run(
            json.dumps({"query": "is:unread", "max_results": 1})
        )
        assert result["total"] == 1

    def test_empty_mailbox_returns_empty(self):
        result = self._make_tool([]).run(
            json.dumps({"query": "subject:invoice"})
        )
        assert result["emails"] == []
        assert result["total"] == 0

    def test_missing_query_returns_error(self):
        result = self._make_tool(_sample_messages()).run(json.dumps({}))
        assert "error" in result

    def test_accepts_json_string_input(self):
        """Tool must parse a JSON string, not just accept a dict."""
        result = self._make_tool(_sample_messages()).run(
            '{"query": "from:vendor@example.com"}'
        )
        assert "emails" in result

    def test_email_result_has_id_and_subject(self):
        result = self._make_tool(_sample_messages()).run(
            json.dumps({"query": "is:unread"})
        )
        for email in result["emails"]:
            assert "id" in email
            assert "subject" in email

    def test_non_json_input_returns_error(self):
        """Unparseable input → missing query → error."""
        result = self._make_tool(_sample_messages()).run("find invoices please")
        assert "error" in result


# ---------------------------------------------------------------------------
# GetThreadTool tests
# ---------------------------------------------------------------------------


class TestGetThreadTool:

    def _thread_messages(self) -> list[dict]:
        return [
            _make_gmail_message(
                "msg_t1", "Re: Project Update", "alice@company.com",
                "Mon, 22 Jun 2026 08:00:00 +0000",
                "Hi Bob, here's the project status update.",
                thread_id="thread_proj",
            ),
            _make_gmail_message(
                "msg_t2", "Re: Project Update", "bob@company.com",
                "Mon, 22 Jun 2026 09:30:00 +0000",
                "Thanks Alice, looks good. Can you send the final report by Friday?",
                thread_id="thread_proj",
            ),
        ]

    def _make_tool(self, messages=None) -> GetThreadTool:
        msgs = messages or self._thread_messages()
        return GetThreadTool(gmail_service=MockGmailService(msgs))

    def test_name_and_zone(self):
        assert self._make_tool().name == "get_thread"
        assert self._make_tool().zone == ToolZone.GREEN

    def test_returns_thread_dict(self):
        result = self._make_tool().run(json.dumps({"thread_id": "thread_proj"}))
        assert "thread_id" in result
        assert "messages" in result
        assert "message_count" in result

    def test_thread_id_echoed(self):
        result = self._make_tool().run(json.dumps({"thread_id": "thread_proj"}))
        assert result["thread_id"] == "thread_proj"

    def test_message_count_correct(self):
        result = self._make_tool().run(json.dumps({"thread_id": "thread_proj"}))
        assert result["message_count"] == 2

    def test_messages_have_required_fields(self):
        result = self._make_tool().run(json.dumps({"thread_id": "thread_proj"}))
        for msg in result["messages"]:
            assert "id" in msg
            assert "from" in msg
            assert "body" in msg

    def test_missing_thread_id_returns_error(self):
        result = self._make_tool().run(json.dumps({}))
        assert "error" in result

    def test_accepts_json_string_input(self):
        result = self._make_tool().run('{"thread_id": "thread_proj"}')
        assert result["message_count"] == 2

    def test_unknown_thread_returns_empty_messages(self):
        result = self._make_tool().run(json.dumps({"thread_id": "nonexistent"}))
        assert result["message_count"] == 0
        assert result["messages"] == []

    def test_body_content_present(self):
        result = self._make_tool().run(json.dumps({"thread_id": "thread_proj"}))
        bodies = [m["body"] for m in result["messages"]]
        assert any("project" in b.lower() for b in bodies)


# ---------------------------------------------------------------------------
# SummarizeThreadTool tests
# ---------------------------------------------------------------------------


class TestSummarizeThreadTool:

    def setup_method(self):
        self.tool = SummarizeThreadTool()

    def _sample_messages(self):
        return [
            {
                "from": "alice@company.com",
                "date": "Mon, 22 Jun 2026 08:00:00 +0000",
                "body": "Hi Bob, the project is on track. We need to finish testing by Thursday.",
            },
            {
                "from": "bob@company.com",
                "date": "Mon, 22 Jun 2026 09:00:00 +0000",
                "body": "Thanks Alice. I'll complete testing by Thursday EOD. Please confirm the deadline.",
            },
        ]

    def test_name_and_zone(self):
        assert self.tool.name == "summarize_thread"
        assert self.tool.zone == ToolZone.GREEN

    def test_returns_transcript_and_participants(self):
        result = self.tool.run(json.dumps({"messages": self._sample_messages()}))
        assert "transcript_for_analysis" in result
        assert "participants" in result
        assert "message_count" in result

    def test_message_count_correct(self):
        result = self.tool.run(json.dumps({"messages": self._sample_messages()}))
        assert result["message_count"] == 2

    def test_participants_extracted(self):
        result = self.tool.run(json.dumps({"messages": self._sample_messages()}))
        assert len(result["participants"]) == 2

    def test_transcript_contains_message_content(self):
        result = self.tool.run(json.dumps({"messages": self._sample_messages()}))
        assert "testing" in result["transcript_for_analysis"].lower()

    def test_instruction_present(self):
        result = self.tool.run(json.dumps({"messages": self._sample_messages()}))
        assert "instruction" in result
        assert len(result["instruction"]) > 0

    def test_topic_passed_through(self):
        result = self.tool.run(json.dumps({
            "messages": self._sample_messages(),
            "topic": "Project deadline discussion",
        }))
        assert result["topic"] == "Project deadline discussion"

    def test_missing_messages_returns_error(self):
        result = self.tool.run(json.dumps({}))
        assert "error" in result

    def test_accepts_json_string_input(self):
        payload = json.dumps({"messages": self._sample_messages()})
        result = self.tool.run(payload)
        assert "transcript_for_analysis" in result

    def test_empty_messages_returns_error(self):
        result = self.tool.run(json.dumps({"messages": []}))
        assert "error" in result

    def test_non_json_input_returns_error(self):
        result = self.tool.run("please summarize this thread")
        assert "error" in result


# ---------------------------------------------------------------------------
# ExtractActionItemsTool tests
# ---------------------------------------------------------------------------


class TestExtractActionItemsTool:

    def setup_method(self):
        self.tool = ExtractActionItemsTool()

    def _urgent_email(self):
        return {
            "id": "msg001",
            "subject": "URGENT: Invoice overdue",
            "from": "finance@acme.com",
            "date": "Mon, 22 Jun 2026 10:00:00 +0000",
            "body": (
                "Please process this invoice asap. "
                "Action required: approve payment immediately. "
                "Respond by Friday 06/27."
            ),
        }

    def _followup_email(self):
        return {
            "id": "msg002",
            "subject": "Follow-up on proposal",
            "from": "sales@vendor.com",
            "date": "Mon, 22 Jun 2026 11:00:00 +0000",
            "body": (
                "Could you please review our proposal? "
                "We need to follow up by end of day. "
                "Please get back to us with feedback."
            ),
        }

    def _newsletter_email(self):
        return {
            "id": "msg003",
            "subject": "Monthly Newsletter",
            "from": "news@company.com",
            "date": "Mon, 22 Jun 2026 08:00:00 +0000",
            "body": "This month's highlights and updates from the team.",
        }

    def test_name_and_zone(self):
        assert self.tool.name == "extract_action_items"
        assert self.tool.zone == ToolZone.GREEN

    def test_returns_action_items_structure(self):
        result = self.tool.run(json.dumps({"emails": [self._urgent_email()]}))
        assert "action_items" in result
        assert "total_emails_scanned" in result
        assert "emails_with_actions" in result

    def test_detects_urgent_email(self):
        result = self.tool.run(json.dumps({"emails": [self._urgent_email()]}))
        assert result["emails_with_actions"] >= 1
        items = result["action_items"]
        assert any(item["urgency"] == "high" for item in items)

    def test_detects_follow_up_actions(self):
        result = self.tool.run(json.dumps({"emails": [self._followup_email()]}))
        assert result["emails_with_actions"] >= 1

    def test_total_emails_scanned_correct(self):
        emails = [self._urgent_email(), self._followup_email(), self._newsletter_email()]
        result = self.tool.run(json.dumps({"emails": emails}))
        assert result["total_emails_scanned"] == 3

    def test_newsletter_no_action_items(self):
        result = self.tool.run(json.dumps({"emails": [self._newsletter_email()]}))
        assert result["emails_with_actions"] == 0

    def test_action_item_has_required_fields(self):
        result = self.tool.run(json.dumps({"emails": [self._urgent_email()]}))
        for item in result["action_items"]:
            assert "email_id" in item
            assert "subject" in item
            assert "urgency" in item
            assert "actions" in item

    def test_urgency_is_valid_value(self):
        emails = [self._urgent_email(), self._followup_email()]
        result = self.tool.run(json.dumps({"emails": emails}))
        valid_urgencies = {"low", "medium", "high"}
        for item in result["action_items"]:
            assert item["urgency"] in valid_urgencies

    def test_missing_emails_returns_error(self):
        result = self.tool.run(json.dumps({}))
        assert "error" in result
        assert result["action_items"] == []

    def test_empty_emails_list_returns_error(self):
        result = self.tool.run(json.dumps({"emails": []}))
        assert "error" in result

    def test_accepts_json_string_input(self):
        payload = json.dumps({"emails": [self._urgent_email()]})
        result = self.tool.run(payload)
        assert "action_items" in result

    def test_actions_capped_at_five_per_email(self):
        email = {
            "id": "msg_long",
            "subject": "Many action items",
            "from": "busy@company.com",
            "date": "Mon, 22 Jun 2026 10:00:00 +0000",
            "body": (
                "Please do this. Please do that. Please review this. "
                "Could you check this? Can you confirm this? "
                "Please also action this. Could you follow up on this?"
            ),
        }
        result = self.tool.run(json.dumps({"emails": [email]}))
        if result["emails_with_actions"]:
            assert len(result["action_items"][0]["actions"]) <= 5


# ---------------------------------------------------------------------------
# ClassifyEmailTool tests
# ---------------------------------------------------------------------------


class TestClassifyEmailTool:

    def setup_method(self):
        self.tool = ClassifyEmailTool()

    def test_name_and_zone(self):
        assert self.tool.name == "classify_email"
        assert self.tool.zone == ToolZone.GREEN

    def test_classifies_invoice(self):
        email = "Subject: Invoice #1042\nFrom: vendor@acme.com\n\nPlease find attached invoice #1042. Amount due: $5,400. Payment due: 30 days."
        assert json.loads(self.tool.run(email))["category"] == "invoice"

    def test_classifies_newsletter(self):
        email = "Subject: Weekly Newsletter\n\nThis week's promotions and offers. Unsubscribe at any time."
        assert json.loads(self.tool.run(email))["category"] == "newsletter"

    def test_classifies_customer_complaint(self):
        email = "I am very disappointed. The item I received is broken. I am unhappy and want a refund."
        assert json.loads(self.tool.run(email))["category"] == "customer_complaint"

    def test_classifies_supplier_inquiry(self):
        email = "Dear Supplier, we would like to request a quotation (RFQ) for bulk order procurement."
        assert json.loads(self.tool.run(email))["category"] == "supplier_inquiry"

    def test_classifies_contract(self):
        email = "Please find attached the non-disclosure agreement (NDA). Kindly sign the contract."
        assert json.loads(self.tool.run(email))["category"] == "contract"

    def test_classifies_payment_confirmation(self):
        email = "Your payment has been received. Transaction ID: TXN-20240601. Amount credited."
        assert json.loads(self.tool.run(email))["category"] == "payment_confirmation"

    def test_returns_confidence_field(self):
        result = json.loads(self.tool.run("Invoice #999 amount due $500"))
        assert result["confidence"] in ("high", "medium", "low")

    def test_high_confidence_for_many_keywords(self):
        text = "invoice bill receipt amount due payment due balance due remittance"
        assert json.loads(self.tool.run(text))["confidence"] == "high"

    def test_returns_summary_field(self):
        result = json.loads(self.tool.run("Please find invoice #42 attached."))
        assert "summary" in result and len(result["summary"]) > 0

    def test_newsletter_does_not_require_reply(self):
        email = "newsletter unsubscribe mailing list promotions offer discount"
        assert json.loads(self.tool.run(email))["requires_reply"] is False

    def test_invoice_requires_reply(self):
        email = "invoice amount due payment due"
        assert json.loads(self.tool.run(email))["requires_reply"] is True

    def test_unknown_email_returns_other(self):
        assert json.loads(self.tool.run("Hello, just wanted to say hi."))["category"] == "other"

    def test_raises_on_empty_input(self):
        with pytest.raises(ValueError):
            self.tool.run("")


# ---------------------------------------------------------------------------
# DraftReplyTool tests
# ---------------------------------------------------------------------------


class TestDraftReplyTool:

    def setup_method(self):
        self.tool = DraftReplyTool()

    def _invoice_input(self, tone="professional"):
        return json.dumps({
            "original_email": (
                "From: John Smith <john@acme.com>\n"
                "Subject: Invoice #1042\n\n"
                "Please find attached invoice #1042 for $5,400. Payment due within 30 days."
            ),
            "context": "We have received the invoice and will process payment within 5 business days.",
            "tone": tone,
        })

    def test_name_and_zone(self):
        assert self.tool.name == "draft_reply"
        assert self.tool.zone == ToolZone.GREEN

    def test_status_is_always_draft(self):
        assert json.loads(self.tool.run(self._invoice_input()))["status"] == "draft"

    def test_status_is_draft_for_all_tones(self):
        for tone in ("professional", "friendly", "formal"):
            assert json.loads(self.tool.run(self._invoice_input(tone)))["status"] == "draft"

    def test_subject_has_re_prefix(self):
        assert json.loads(self.tool.run(self._invoice_input()))["subject"].startswith("Re:")

    def test_subject_no_double_re(self):
        payload = json.dumps({
            "original_email": "From: a@b.com\nSubject: Re: Invoice\n\nBody.",
            "context": "Acknowledged.",
            "tone": "professional",
        })
        assert json.loads(self.tool.run(payload))["subject"].count("Re:") == 1

    def test_to_contains_sender_email(self):
        assert "john@acme.com" in json.loads(self.tool.run(self._invoice_input()))["to"]

    def test_body_contains_context(self):
        assert "5 business days" in json.loads(self.tool.run(self._invoice_input()))["body"]

    def test_professional_tone(self):
        assert "Best regards" in json.loads(self.tool.run(self._invoice_input("professional")))["body"]

    def test_friendly_tone(self):
        body = json.loads(self.tool.run(self._invoice_input("friendly")))["body"]
        assert "Cheers" in body or "Hi" in body

    def test_formal_tone(self):
        assert "Yours sincerely" in json.loads(self.tool.run(self._invoice_input("formal")))["body"]

    def test_raises_on_empty_input(self):
        with pytest.raises(ValueError):
            self.tool.run("")


# ---------------------------------------------------------------------------
# SendEmailTool tests
# ---------------------------------------------------------------------------


class TestSendEmailTool:

    def test_name_and_zone(self):
        assert SendEmailTool().name == "send_email"
        assert SendEmailTool().zone == ToolZone.YELLOW

    def test_zone_is_yellow_with_injected_service(self):
        assert SendEmailTool(gmail_service=MockGmailService()).zone == ToolZone.YELLOW

    def test_raises_approval_required_via_base_agent(self):
        send_tool = SendEmailTool(gmail_service=MockGmailService())
        llm = MockLLMProvider([
            {
                "content": "",
                "tool_call": {
                    "name": "send_email",
                    "input": json.dumps({
                        "to": "customer@example.com",
                        "subject": "Re: Invoice #1042",
                        "body": "Dear Customer, we will process payment shortly.",
                    }),
                },
                "tokens_used": 100,
                "cost_usd": 0.0,
            }
        ])
        agent = BaseAgent(name="TestAgent", llm_provider=llm, tools=[send_tool])
        with pytest.raises(ApprovalRequired) as exc_info:
            agent.run("Send a reply.")
        assert exc_info.value.tool_name == "send_email"

    def test_run_sends_email_with_mock_service(self):
        tool = SendEmailTool(gmail_service=MockGmailService())
        result = json.loads(tool.run(json.dumps({
            "to": "recipient@example.com",
            "subject": "Test Subject",
            "body": "Test body text.",
        })))
        assert result["status"] == "sent"
        assert "message_id" in result

    def test_run_raises_on_missing_fields(self):
        with pytest.raises(ValueError):
            SendEmailTool(gmail_service=MockGmailService()).run(
                json.dumps({"to": "x@y.com"})  # missing subject + body
            )

    def test_run_raises_on_api_failure(self):
        with pytest.raises(RuntimeError):
            SendEmailTool(gmail_service=MockGmailServiceError()).run(
                json.dumps({"to": "x@y.com", "subject": "Test", "body": "Hello."})
            )
