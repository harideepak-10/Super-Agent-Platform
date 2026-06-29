"""
Gmail tools package.

Exports all Gmail-related BaseTool implementations plus the
GmailAuth helper.  Real Gmail API calls are made via GmailAuth;
tests inject a MockGmailService instead.
"""

from .auth import GmailAuth
from .read_emails import ReadEmailsTool
from .classify_email import ClassifyEmailTool
from .draft_reply import DraftReplyTool
from .send_email import SendEmailTool
from .search_emails import SearchEmailsTool
from .get_thread import GetThreadTool
from .summarize_thread import SummarizeThreadTool
from .extract_action_items import ExtractActionItemsTool

__all__ = [
    "GmailAuth",
    "ReadEmailsTool",
    "ClassifyEmailTool",
    "DraftReplyTool",
    "SendEmailTool",
    "SearchEmailsTool",
    "GetThreadTool",
    "SummarizeThreadTool",
    "ExtractActionItemsTool",
]
