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

__all__ = [
    "GmailAuth",
    "ReadEmailsTool",
    "ClassifyEmailTool",
    "DraftReplyTool",
    "SendEmailTool",
]
