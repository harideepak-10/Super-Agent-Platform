"""
Celery task: run_agent_task + resume_agent_task

Bridges the Django Task/Approval models with the superagent-ai ReAct engine.

Flow:
  POST /tasks/create/  ->  run_agent_task.delay(task_id)
       |
  run_agent_task  ->  builds tools, creates DjangoAgent, calls agent.run()
       |  (normal)              |  (needs approval)
  Task=completed         Task=waiting_approval + Approval record created
                                |  (user POSTs /approvals/{id}/decide/ approved)
                         resume_agent_task.delay(task_id, approval_id)
                                |
                         Agent resumes from snapshot -> Task=completed
"""
from __future__ import annotations

import json
import os
import sys

# ---------------------------------------------------------------------------
# Make superagent-ai importable (fallback — superagent-django/core/ is used
# directly since Django runs from that directory, but keep this for safety)
# ---------------------------------------------------------------------------
_THIS    = os.path.abspath(__file__)
_DJANGO  = os.path.dirname(os.path.dirname(os.path.dirname(_THIS)))
_REPO    = os.path.dirname(_DJANGO)
_AI_PATH = os.path.join(_REPO, "superagent-ai")
if _AI_PATH not in sys.path:
    sys.path.insert(0, _AI_PATH)

import logging

from celery import shared_task
from django.utils import timezone

from core.tools.base_tool import BaseTool, ToolZone

_logger = logging.getLogger(__name__)
from core.base_agent import (
    BaseAgent, ApprovalRequired, RedZoneBlocked,
    StepLimitReached, CostLimitReached,
)


# =============================================================================
# TOOL IMPLEMENTATIONS
# Each tool overrides to_schema() to return OpenAI function-calling format
# =============================================================================

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for current information. Input JSON: {\"query\": \"search query\"}."
    zone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
            query = data.get("query", input_str)
        except Exception:
            query = input_str.strip()
        try:
            from duckduckgo_search import DDGS
            results = DDGS().text(query, max_results=5)
            if not results:
                return "No results found for '{}'.".format(query)
            lines = []
            for r in results:
                lines.append("**{}**\n{}\n{}".format(
                    r.get("title", ""), r.get("body", ""), r.get("href", "")
                ))
            return "Search results for '{}':\n\n".format(query) + "\n\n".join(lines)
        except Exception as exc:
            return "Web search error: {}".format(exc)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]},
        }}


class ClassifyTextTool(BaseTool):
    name = "classify_text"
    description = "Classify text into categories. Input JSON: {\"text\": \"...\", \"categories\": [\"urgent\",\"normal\",\"spam\"]}."
    zone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
            text = data.get("text", input_str)
            cats = data.get("categories", ["urgent", "invoice", "normal", "spam"])
        except Exception:
            text = input_str
            cats = ["urgent", "invoice", "normal", "spam"]
        tl = text.lower()
        if any(w in tl for w in ["urgent", "asap", "immediately", "critical", "emergency"]):
            cat = "urgent"
        elif any(w in tl for w in ["invoice", "payment", "bill", "amount due", "overdue"]):
            cat = "invoice"
        elif any(w in tl for w in ["unsubscribe", "marketing", "promotion", "offer", "deal"]):
            cat = "spam"
        else:
            cat = "normal"
        if cat not in cats:
            cat = cats[0]
        return json.dumps({"classification": cat, "confidence": 0.85, "text_preview": text[:100]})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text"]},
        }}


class GenerateReportTool(BaseTool):
    name = "generate_report"
    description = "Generate a structured report. Input JSON: {\"title\": \"...\", \"data\": \"findings or content\"}."
    zone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
            title = data.get("title", "Report")
            content = data.get("data", data.get("content", input_str))
        except Exception:
            title, content = "Report", input_str
        ts = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
        return "# {}\n_Generated: {}_\n\n{}\n".format(title, ts, content)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "data": {"type": "string"},
                },
                "required": ["data"]},
        }}


class ReadEmailTool(BaseTool):
    name = "read_email"
    description = "Fetch emails from the Gmail inbox. Input JSON: {\"limit\": 10, \"filter\": \"is:unread\"}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def _gmail_service(self):
        if not self._workspace_id:
            return None
        try:
            from apps.integrations.models import Integration
            integration = Integration.objects.filter(
                workspace_id=self._workspace_id,
                provider=Integration.Provider.GMAIL,
                status=Integration.Status.ACTIVE,
            ).first()
            if not integration or not integration.access_token:
                return None
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials(
                token=integration.access_token,
                refresh_token=integration.refresh_token,
                client_id=os.environ.get("GOOGLE_CLIENT_ID"),
                client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
                token_uri="https://oauth2.googleapis.com/token",
            )
            return build("gmail", "v1", credentials=creds)
        except Exception:
            return None

    def run(self, input_str: str) -> str:
        service = self._gmail_service()
        if service:
            from core.tools.gmail.read_emails import ReadEmailsTool
            return ReadEmailsTool(gmail_service=service).run(input_str)
        return json.dumps({
            "note": "No Gmail integration connected. Go to Integrations to connect Gmail.",
            "emails": [],
        })

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "filter": {"type": "string"},
                }},
        }}


class DownloadAttachmentTool(BaseTool):
    """Download a Gmail attachment and save it locally."""
    name = "download_attachment"
    description = (
        "Download an email attachment from Gmail and save it to a local file. "
        "Input JSON: {\"message_id\": \"...\", \"attachment_id\": \"...\", \"filename\": \"...(optional)\"}. "
        "Get message_id and attachment_id from the 'attachments' list in read_emails response. "
        "Returns file_path of the saved file."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def _gmail_service(self):
        if not self._workspace_id:
            return None
        try:
            from apps.integrations.models import Integration
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            integration = Integration.objects.filter(
                workspace_id=self._workspace_id,
                provider=Integration.Provider.GMAIL,
                status=Integration.Status.ACTIVE,
            ).first()
            if not integration or not integration.access_token:
                return None
            import os
            creds = Credentials(
                token=integration.access_token,
                refresh_token=integration.refresh_token,
                client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
                client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                token_uri="https://oauth2.googleapis.com/token",
            )
            return build("gmail", "v1", credentials=creds)
        except Exception:
            return None

    def run(self, input_str: str) -> str:
        from core.tools.gmail.download_attachment import DownloadAttachmentTool as CoreTool
        return CoreTool(gmail_service=self._gmail_service()).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "message_id":    {"type": "string"},
                    "attachment_id": {"type": "string"},
                    "filename":      {"type": "string"},
                },
                "required": ["message_id", "attachment_id"],
            },
        }}


class SummarizeEmailsTool(BaseTool):
    """Thin wrapper — delegates to core SummarizeEmailsTool (no Gmail credentials needed)."""
    name = "summarize_emails"
    description = (
        "Summarize a list of emails from different senders into a clean numbered report in one step. "
        "Input JSON: {\"emails\": [...]} — the list returned by read_emails. "
        "Returns formatted_summary ready to show the user, plus structured summaries per email."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass  # no credentials needed

    def run(self, input_str: str) -> str:
        from core.tools.gmail.summarize_emails import SummarizeEmailsTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "emails": {
                        "type": "array",
                        "description": "List of email objects returned by read_emails.",
                        "items": {"type": "object"},
                    }
                },
                "required": ["emails"],
            },
        }}


class SendEmailTool(BaseTool):
    name = "send_email"
    description = "Send an email. Input JSON: {\"to\": \"recipient@email.com\", \"subject\": \"...\", \"body\": \"...\"}."
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def _gmail_service(self):
        _svc_log = logging.getLogger("send_email.service")
        if not self._workspace_id:
            _svc_log.warning("_gmail_service: no workspace_id")
            return None
        try:
            from apps.integrations.models import Integration
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            integration = Integration.objects.filter(
                workspace_id=self._workspace_id,
                provider=Integration.Provider.GMAIL,
                status=Integration.Status.ACTIVE,
            ).first()
            if not integration:
                _svc_log.warning("_gmail_service: no active Gmail integration for workspace=%s", self._workspace_id)
                return None
            if not integration.access_token:
                _svc_log.warning("_gmail_service: integration found but no access_token for workspace=%s", self._workspace_id)
                return None
            _svc_log.info("_gmail_service: building service for workspace=%s has_refresh=%s", self._workspace_id, bool(integration.refresh_token))
            creds = Credentials(
                token=integration.access_token,
                refresh_token=integration.refresh_token,
                client_id=os.environ.get("GOOGLE_CLIENT_ID"),
                client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
                token_uri="https://oauth2.googleapis.com/token",
            )
            return build("gmail", "v1", credentials=creds)
        except Exception as exc:
            _svc_log.error("_gmail_service: exception building service err=%s", exc, exc_info=True)
            return None

    def run(self, input_str: str) -> str:
        _run_log = logging.getLogger("send_email")
        _run_log.info("SendEmailTool.run called input=%r", input_str[:200] if input_str else "")
        try:
            data = json.loads(input_str)
        except Exception as parse_exc:
            _run_log.warning("SendEmailTool.run json.loads failed err=%s input=%r", parse_exc, input_str[:100])
            data = {"raw": input_str}

        to      = data.get("to") or data.get("recipient", "")
        subject = data.get("subject", "(no subject)")
        body    = data.get("body", "")
        _run_log.info("SendEmailTool.run to=%r subject=%r body_len=%d", to, subject, len(body))

        service = self._gmail_service()
        _run_log.info("SendEmailTool.run gmail_service_ok=%s", service is not None)
        if service:
            try:
                import base64
                from email.mime.text import MIMEText
                msg = MIMEText(body)
                msg["to"]      = to
                msg["subject"] = subject
                raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                send_result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
                _run_log.info("SendEmailTool.run SENT OK to=%r msg_id=%s", to, send_result.get("id"))
                return json.dumps({"status": "sent", "to": to, "subject": subject, "msg_id": send_result.get("id", "")})
            except Exception as exc:
                _run_log.error("SendEmailTool.run SEND FAILED to=%r err=%s", to, exc, exc_info=True)
                return json.dumps({"status": "error", "error": str(exc)})

        result = json.dumps({
            "status": "no_gmail",
            "note": "Gmail not connected. Go to Integrations to connect Gmail first.",
            "to": to,
            "subject": subject,
        })
        _run_log.warning("SendEmailTool.run no_gmail to=%r", to)
        return result

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "recipient": {"type": "string", "description": "Recipient email address (alias for 'to')"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"]},
        }}


class CreateDraftTool(BaseTool):
    name = "create_draft"
    description = "Create an email draft (does NOT send). Input JSON: {\"to\": \"email\", \"subject\": \"...\", \"body\": \"...\"}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
        except Exception:
            data = {}
        return json.dumps({
            "status": "draft_created",
            "to": data.get("to", ""),
            "subject": data.get("subject", "(no subject)"),
            "body_preview": data.get("body", "")[:100],
            "draft_id": "draft_{}".format(timezone.now().strftime("%Y%m%d%H%M%S")),
        })

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"]},
        }}


class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read file contents. Input JSON: {\"path\": \"/path/to/file\"}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        try:
            path = json.loads(input_str).get("path", input_str.strip())
        except Exception:
            path = input_str.strip()
        try:
            with open(path) as f:
                return f.read()
        except Exception as exc:
            return "Cannot read '{}': {}".format(path, exc)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]},
        }}


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "Write content to a file. Requires approval. Input JSON: {\"path\": \"...\", \"content\": \"...\"}."
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
            path = data.get("path", "output.txt")
            content = data.get("content", "")
            with open(path, "w") as f:
                f.write(content)
            return json.dumps({"status": "written", "path": path, "bytes": len(content)})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"]},
        }}


class ExportCsvTool(BaseTool):
    name = "export_csv"
    description = "Export data as CSV. Input JSON: {\"headers\": [\"col1\"], \"data\": [[\"row1\"]]}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        import csv, io
        try:
            d = json.loads(input_str)
            rows = d.get("data", [])
            headers = d.get("headers", [])
            out = io.StringIO()
            w = csv.writer(out)
            if headers:
                w.writerow(headers)
            for r in rows:
                w.writerow(r)
            return json.dumps({"status": "exported", "rows": len(rows), "csv_preview": out.getvalue()[:300]})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "data": {"type": "array"},
                },
                "required": ["data"]},
        }}


class BrowseWebTool(BaseTool):
    name = "browse_web"
    description = "Browse a URL and return its text content. Input JSON: {\"url\": \"https://...\"}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        try:
            url = json.loads(input_str).get("url", input_str.strip())
        except Exception:
            url = input_str.strip()
        try:
            import re
            import requests as req
            r = req.get(url, timeout=15, headers={"User-Agent": "SuperAgent/1.0"})
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:2000] + ("..." if len(text) > 2000 else "")
        except Exception as exc:
            return "Cannot browse '{}': {}".format(url, exc)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"]},
        }}


class CalReadTool(BaseTool):
    name = "cal_read"
    description = "Read calendar events. Input JSON: {\"days_ahead\": 7}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        return json.dumps({"note": "Google Calendar not connected. Connect in Integrations.", "events": []})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {"days_ahead": {"type": "integer"}}},
        }}


class CalWriteTool(BaseTool):
    name = "cal_write"
    description = "Create a calendar event. Requires approval. Input JSON: {\"title\": \"...\", \"date\": \"2026-07-01\", \"duration_mins\": 60}."
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        return json.dumps({"note": "Calendar write not implemented yet."})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "duration_mins": {"type": "integer"},
                },
                "required": ["title", "date"]},
        }}


class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = "Delete a file. REQUIRES APPROVAL. Input JSON: {\"path\": \"...\"}."
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        try:
            path = json.loads(input_str).get("path", "")
            os.remove(path)
            return json.dumps({"status": "deleted", "path": path})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]},
        }}


class GenerateContentTool(BaseTool):
    """Delegate to core GenerateContentTool."""
    name = "generate_content"
    description = (
        "Generate structured document content using the LLM. "
        "Input JSON: {\"title\": \"...\", \"doc_type\": \"report|summary|proposal|letter|table\", "
        "\"prompt\": \"...\", \"source_data\": \"...(optional)\", \"sections\": [...](optional)}. "
        "Returns structured sections ready to pass to create_pdf or create_docx. "
        "Always call this first before creating any document file."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        from core.tools.document.generate_content import GenerateContentTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "doc_type":    {"type": "string", "enum": ["report", "summary", "proposal", "letter", "table"]},
                    "prompt":      {"type": "string"},
                    "source_data": {"type": "string"},
                    "sections":    {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "doc_type", "prompt"],
            },
        }}


class CreatePdfTool(BaseTool):
    """Delegate to core CreatePdfTool."""
    name = "create_pdf"
    description = (
        "Create a PDF file from structured document content. "
        "Input JSON: {\"title\": \"...\", \"sections\": [{\"heading\": \"...\", \"content\": \"...\"}]}. "
        "Returns file_path. Pass it to upload_to_drive."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        from core.tools.document.create_pdf import CreatePdfTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title":    {"type": "string"},
                    "sections": {"type": "array", "items": {"type": "object"}},
                    "author":   {"type": "string"},
                },
                "required": ["title", "sections"],
            },
        }}


class CreateDocxTool(BaseTool):
    """Delegate to core CreateDocxTool."""
    name = "create_docx"
    description = (
        "Create a Word (.docx) file from structured document content. "
        "Input JSON: {\"title\": \"...\", \"sections\": [{\"heading\": \"...\", \"content\": \"...\"}]}. "
        "Returns file_path. Pass it to upload_to_drive."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        from core.tools.document.create_docx import CreateDocxTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "title":    {"type": "string"},
                    "sections": {"type": "array", "items": {"type": "object"}},
                    "author":   {"type": "string"},
                },
                "required": ["title", "sections"],
            },
        }}


class UploadToDriveTool(BaseTool):
    """Delegate to core UploadToDriveTool (YELLOW — requires approval)."""
    name = "upload_to_drive"
    description = (
        "Upload a file to Google Drive. ALWAYS requires human approval (YELLOW zone). "
        "Input JSON: {\"file_path\": \"...\", \"filename\": \"...(optional)\", "
        "\"folder_name\": \"...(optional)\"}. "
        "Returns drive_url saved to task deliverables. Requires Drive integration."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        from core.tools.document.upload_to_drive import UploadToDriveTool as CoreTool
        return CoreTool(workspace_id=self._workspace_id).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "file_path":   {"type": "string"},
                    "filename":    {"type": "string"},
                    "folder_name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["file_path"],
            },
        }}


# =============================================================================
# NEW EMAIL AGENT TOOL WRAPPERS
# =============================================================================

def _gmail_service_for_workspace(workspace_id):
    """Helper: build a Gmail service from the workspace's active integration."""
    if not workspace_id:
        return None
    try:
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        integration = Integration.objects.filter(
            workspace_id=workspace_id,
            provider=Integration.Provider.GMAIL,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            return None
        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        return build("gmail", "v1", credentials=creds)
    except Exception:
        return None


class CreateGmailDraftTool(BaseTool):
    """Save a draft to Gmail's Drafts folder (GREEN — no approval needed)."""
    name = "create_gmail_draft"
    description = (
        "Save an email as a draft in Gmail's Drafts folder. GREEN zone — no approval needed. "
        "NOT sent — user reviews from Gmail or agent sends via send_email (YELLOW). "
        "Input JSON: {\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\", "
        "\"cc\": \"...(optional)\", \"thread_id\": \"...(optional)\"}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.create_gmail_draft import CreateGmailDraftTool as CoreTool
        return CoreTool(
            gmail_service=_gmail_service_for_workspace(self._workspace_id),
            workspace_id=self._workspace_id,
        ).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "to":                  {"type": "string"},
                "subject":             {"type": "string"},
                "body":                {"type": "string"},
                "cc":                  {"type": "string"},
                "thread_id":           {"type": "string"},
                "reply_to_message_id": {"type": "string"},
            }, "required": ["to", "subject", "body"]},
        }}


class MarkAsReadTool(BaseTool):
    """Mark emails as read."""
    name = "mark_as_read"
    description = (
        "Mark one or more emails as read. "
        "Input JSON: {\"message_ids\": [\"...\", \"...\"]} or {\"message_id\": \"...\"}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.mark_as_read import MarkAsReadTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "message_id":  {"type": "string"},
            }},
        }}


class LabelEmailTool(BaseTool):
    """Add or remove Gmail labels."""
    name = "label_email"
    description = (
        "Add or remove Gmail labels from emails. "
        "Input JSON: {\"message_ids\": [...], \"add_labels\": [\"Invoice\"], \"remove_labels\": [\"UNREAD\"]}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.label_email import LabelEmailTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids":    {"type": "array", "items": {"type": "string"}},
                "add_labels":     {"type": "array", "items": {"type": "string"}},
                "remove_labels":  {"type": "array", "items": {"type": "string"}},
            }, "required": ["message_ids"]},
        }}


class MoveToFolderTool(BaseTool):
    """Move emails to a Gmail folder."""
    name = "move_to_folder"
    description = (
        "Move emails to a Gmail folder. "
        "Input JSON: {\"message_ids\": [...], \"folder\": \"inbox|spam|trash|starred|important\"}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.move_to_folder import MoveToFolderTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "folder":      {"type": "string"},
            }, "required": ["message_ids", "folder"]},
        }}


class DeleteEmailTool(BaseTool):
    """Move email to trash (YELLOW)."""
    name = "delete_email"
    description = (
        "Move emails to Gmail trash. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_ids\": [...]}."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.delete_email import DeleteEmailTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_ids": {"type": "array", "items": {"type": "string"}},
            }, "required": ["message_ids"]},
        }}


class ReplyToEmailTool(BaseTool):
    """Reply to an email thread (YELLOW)."""
    name = "reply_to_email"
    description = (
        "Send a reply in the same thread. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_id\": \"...\", \"thread_id\": \"...\", \"to\": \"...\", "
        "\"subject\": \"...\", \"body\": \"...\", \"cc\": \"...(optional)\"}."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.reply_to_email import ReplyToEmailTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_id": {"type": "string"},
                "thread_id":  {"type": "string"},
                "to":         {"type": "string"},
                "subject":    {"type": "string"},
                "body":       {"type": "string"},
                "cc":         {"type": "string"},
            }, "required": ["message_id", "to", "subject", "body"]},
        }}


class ForwardEmailTool(BaseTool):
    """Forward an email (YELLOW)."""
    name = "forward_email"
    description = (
        "Forward an email to other recipients. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"message_id\": \"...\", \"to\": [\"email@...\"], \"note\": \"...(optional)\"}."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.forward_email import ForwardEmailTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "message_id": {"type": "string"},
                "to":         {"type": "array", "items": {"type": "string"}},
                "note":       {"type": "string"},
            }, "required": ["message_id", "to"]},
        }}


class ScheduleEmailTool(BaseTool):
    """Schedule an email for future delivery (YELLOW)."""
    name = "schedule_email"
    description = (
        "Schedule an email to be sent at a future time. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\", "
        "\"send_at\": \"2026-07-09T09:00:00\"}."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.schedule_email import ScheduleEmailTool as CoreTool
        return CoreTool(workspace_id=self._workspace_id).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "to":      {"type": "string"},
                "subject": {"type": "string"},
                "body":    {"type": "string"},
                "cc":      {"type": "string"},
                "send_at": {"type": "string", "description": "ISO 8601 datetime"},
            }, "required": ["to", "subject", "body", "send_at"]},
        }}


class ExtractInvoiceDataTool(BaseTool):
    """Extract invoice data from email body."""
    name = "extract_invoice_data"
    description = (
        "Extract invoice number, amount, due date, vendor, and payment status from email text. "
        "Input JSON: {\"email_body\": \"...\", \"subject\": \"...(optional)\"}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str):
        from core.tools.gmail.extract_invoice_data import ExtractInvoiceDataTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "email_body": {"type": "string"},
                "subject":    {"type": "string"},
            }, "required": ["email_body"]},
        }}


class DetectFollowUpTool(BaseTool):
    """Detect emails needing follow-up."""
    name = "detect_follow_up_needed"
    description = (
        "Scan inbox for emails that haven't been replied to in N days. "
        "Input JSON: {\"days\": 3, \"max_results\": 10}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.gmail.detect_follow_up import DetectFollowUpTool as CoreTool
        return CoreTool(gmail_service=_gmail_service_for_workspace(self._workspace_id)).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "days":        {"type": "integer"},
                "max_results": {"type": "integer"},
            }},
        }}


class ReadAttachmentContentTool(BaseTool):
    """Read text from a downloaded attachment."""
    name = "read_attachment_content"
    description = (
        "Read text content from a downloaded attachment (PDF, DOCX, CSV, TXT). "
        "Input JSON: {\"file_path\": \"...\", \"max_chars\": 8000}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str):
        from core.tools.gmail.read_attachment_content import ReadAttachmentContentTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string"},
                "max_chars": {"type": "integer"},
            }, "required": ["file_path"]},
        }}


class ExtractDataFromAttachmentTool(BaseTool):
    """Extract structured data from an attachment."""
    name = "extract_data_from_attachment"
    description = (
        "Extract amounts, dates, emails, phones, and tables from a downloaded attachment. "
        "Input JSON: {\"file_path\": \"...\"}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str):
        from core.tools.gmail.extract_data_from_attachment import ExtractDataFromAttachmentTool as CoreTool
        return CoreTool().run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string"},
            }, "required": ["file_path"]},
        }}


class ListCustomerProfilesTool(BaseTool):
    """List customer profiles in the workspace."""
    name = "list_customer_profiles"
    description = (
        "List all known customer profiles in the workspace. "
        "Input JSON: {\"workspace_id\": \"...\", \"limit\": 20}."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except Exception:
            data = {}
        if self._workspace_id and not data.get("workspace_id"):
            data["workspace_id"] = self._workspace_id
        from core.tools.memory.list_customer_profiles import ListCustomerProfilesTool as CoreTool
        return CoreTool().run(json.dumps(data))

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "workspace_id": {"type": "string"},
                "limit":        {"type": "integer"},
            }, "required": ["workspace_id"]},
        }}


class SearchCustomerByEmailTool(BaseTool):
    """Look up a customer profile by email address."""
    name = "search_customer_by_email"
    description = (
        "Look up a customer profile by their email address. "
        "Input JSON: {\"email\": \"...\"}. Returns the full profile or found=false."
    )
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else input_str
        except Exception:
            data = {}
        if self._workspace_id and not data.get("workspace_id"):
            data["workspace_id"] = self._workspace_id
        from core.tools.memory.search_customer_by_email import SearchCustomerByEmailTool as CoreTool
        return CoreTool().run(json.dumps(data))

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "email":        {"type": "string"},
                "workspace_id": {"type": "string"},
            }, "required": ["email"]},
        }}


class CreateMeetingTool(BaseTool):
    """Create a Google Calendar event with attendees (YELLOW)."""
    name = "create_meeting"
    description = (
        "Create a Google Calendar event and send invitations. REQUIRES human approval (YELLOW zone). "
        "Input JSON: {\"title\": \"...\", \"start_time\": \"2026-07-08T11:00:00\", "
        "\"attendees\": [\"email@...\"], \"duration_mins\": 60, "
        "\"description\": \"...(optional)\", \"timezone\": \"Asia/Kolkata\"}."
    )
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str):
        from core.tools.calendar.create_meeting import CreateMeetingTool as CoreTool
        return CoreTool(workspace_id=self._workspace_id).run(input_str)

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "title":         {"type": "string"},
                "start_time":    {"type": "string"},
                "duration_mins": {"type": "integer"},
                "attendees":     {"type": "array", "items": {"type": "string"}},
                "description":   {"type": "string"},
                "location":      {"type": "string"},
                "timezone":      {"type": "string"},
            }, "required": ["title", "start_time", "attendees"]},
        }}


# =============================================================================
# TOOL REGISTRY
# =============================================================================

_TOOL_REGISTRY: dict = {
    # Email core
    "send_email":               SendEmailTool,
    "read_email":               ReadEmailTool,
    "summarize_emails":         SummarizeEmailsTool,
    "download_attachment":      DownloadAttachmentTool,
    "create_draft":             CreateDraftTool,
    "create_gmail_draft":       CreateGmailDraftTool,
    # Email inbox management
    "mark_as_read":             MarkAsReadTool,
    "label_email":              LabelEmailTool,
    "move_to_folder":           MoveToFolderTool,
    "delete_email":             DeleteEmailTool,
    # Email compose
    "reply_to_email":           ReplyToEmailTool,
    "forward_email":            ForwardEmailTool,
    "schedule_email":           ScheduleEmailTool,
    # Email intelligence
    "extract_invoice_data":     ExtractInvoiceDataTool,
    "detect_follow_up_needed":  DetectFollowUpTool,
    # Attachment tools
    "read_attachment_content":  ReadAttachmentContentTool,
    "extract_data_from_attachment": ExtractDataFromAttachmentTool,
    # Customer memory
    "list_customer_profiles":   ListCustomerProfilesTool,
    "search_customer_by_email": SearchCustomerByEmailTool,
    # Calendar
    "create_meeting":           CreateMeetingTool,
    # Document tools
    "generate_content":         GenerateContentTool,
    "create_pdf":               CreatePdfTool,
    "create_docx":              CreateDocxTool,
    "upload_to_drive":          UploadToDriveTool,
    # General
    "web_search":               WebSearchTool,
    "browse_web":               BrowseWebTool,
    "classify_text":            ClassifyTextTool,
    "generate_report":          GenerateReportTool,
    "file_read":                FileReadTool,
    "file_write":               FileWriteTool,
    "export_csv":               ExportCsvTool,
    "cal_read":                 CalReadTool,
    "cal_write":                CalWriteTool,
    "delete_file":              DeleteFileTool,
}

_HIGH_ZONE_TOOLS = {
    "send_email", "reply_to_email", "forward_email", "schedule_email",
    "delete_email", "delete_file", "cal_write", "file_write",
    "create_meeting", "upload_to_drive",
}


def _build_tools(agent_model, workspace_id=None):
    tools = []
    for tool_name in (agent_model.tools or []):
        cls = _TOOL_REGISTRY.get(tool_name)
        if cls:
            try:
                tools.append(cls(workspace_id=workspace_id))
            except TypeError:
                tools.append(cls())
    if not tools:
        # No agent or agent has no tools configured — give ALL registered tools
        for cls in _TOOL_REGISTRY.values():
            try:
                tools.append(cls(workspace_id=workspace_id))
            except TypeError:
                tools.append(cls())
    return tools


# =============================================================================
# DJANGO AGENT
# =============================================================================

class DjangoAgent(BaseAgent):
    def __init__(self, system_prompt: str = "", **kwargs):
        super().__init__(**kwargs)
        self._db_system_prompt = system_prompt

    def _system_prompt(self) -> str:
        if self._db_system_prompt:
            return self._db_system_prompt
        tool_names = list(self._tools.keys())
        tools_str = ", ".join(tool_names) if tool_names else "none"
        return (
            f"You are an autonomous AI agent. You have these tools available: {tools_str}.\n\n"
            "RULES — follow these exactly:\n"
            "1. ALWAYS use a tool to complete the task. NEVER say you cannot do something if you have the tool for it.\n"
            "2. To send an email: call the 'send_email' tool with the fields: to, subject, body. Do this immediately — do not search first.\n"
            "3. Do NOT search the web for 'how to send email' or any implementation details — you already have the send_email tool.\n"
            "4. Do NOT write code or instructions — call the tool directly.\n"
            "5. After the tool runs, give a short confirmation to the user."
        )

    def _log(self, event_type: str, details: dict) -> None:
        """Override to push standardised WS events to the channel layer in real-time.

        Standardised event shapes
        ─────────────────────────
        step_started   — LLM thinking or tool about to run
        step_finished  — tool result received
        status_changed — task status transition
        step_output    — partial/streaming output (reserved for future streaming)

        Every event has a common envelope:
        {
          "event":      "<event_name>",
          "task_id":    "<uuid>",
          "agent_name": "<name>",
          "step":       <int | null>,
          "title":      "<human label>",
          "detail":     "<one-liner>",
          "data":       { ...raw details... }
        }
        """
        super()._log(event_type, details)
        import json as _json
        import logging as _logging
        _ws_logger = _logging.getLogger("ws.live")
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if not channel_layer or not self.task_id:
                return

            try:
                safe_details = _json.loads(_json.dumps(details, default=str))
            except Exception:
                safe_details = {"raw": str(details)}

            tname = details.get("tool_name", "")
            title, detail = _step_title_detail(event_type, tname, details)

            # Map internal event_type → standardised WS event name
            _WS_EVENT_MAP = {
                "llm_called":      "step_started",
                "tool_called":     "step_started",
                "tool_result":     "step_finished",
                "approval_needed": "status_changed",
                "task_completed":  "status_changed",
                "task_failed":     "status_changed",
                "task_resumed":    "step_finished",
            }
            ws_event = _WS_EVENT_MAP.get(event_type, event_type)

            # status_changed carries the new status explicitly
            new_status = None
            if event_type == "approval_needed":
                new_status = "waiting_approval"
            elif event_type == "task_completed":
                new_status = "completed"
            elif event_type == "task_failed":
                new_status = "failed"

            payload = {
                "event":      ws_event,
                "task_id":    self.task_id,
                "agent_name": self.name,
                "step":       details.get("step"),
                "title":      title,
                "detail":     detail,
                "data":       safe_details,
            }
            if new_status:
                payload["status"] = new_status

            group_name = "task_{}".format(self.task_id)
            async_to_sync(channel_layer.group_send)(group_name, {
                "type": "task_update",
                **payload,
            })
            _ws_logger.info("WS_PUSH_OK task=%s ws_event=%s", self.task_id, ws_event)
        except Exception as _exc:
            _ws_logger.error("WS_PUSH_ERR task=%s event=%s err=%s", self.task_id, event_type, _exc, exc_info=True)


# =============================================================================
# HELPER — save audit_log entries as TaskStep records
# =============================================================================

# Human-readable labels for every tool
_TOOL_LABELS = {
    # Email core
    "send_email":               ("Sending email",              "Sending message to recipient"),
    "read_email":               ("Reading emails",             "Fetching from Gmail inbox"),
    "summarize_emails":         ("Summarising emails",         "Building summary of all emails"),
    "download_attachment":      ("Downloading attachment",     "Saving attachment from Gmail"),
    "create_draft":             ("Creating email draft",       "Drafting message"),
    "create_gmail_draft":       ("Saving Gmail draft",         "Saving draft to Gmail Drafts folder"),
    # Inbox management
    "mark_as_read":             ("Marking as read",            "Updating read status in Gmail"),
    "label_email":              ("Labelling email",            "Adding/removing Gmail labels"),
    "move_to_folder":           ("Moving email",               "Moving to Gmail folder"),
    "delete_email":             ("Deleting email",             "Moving email to trash"),
    # Compose
    "reply_to_email":           ("Replying to email",          "Sending reply in thread"),
    "forward_email":            ("Forwarding email",           "Forwarding to new recipients"),
    "schedule_email":           ("Scheduling email",           "Setting up future delivery"),
    # Intelligence
    "extract_invoice_data":     ("Extracting invoice data",    "Parsing amounts and due dates"),
    "detect_follow_up_needed":  ("Checking follow-ups",        "Finding emails without replies"),
    # Attachment
    "read_attachment_content":  ("Reading attachment",         "Extracting text from file"),
    "extract_data_from_attachment": ("Extracting data",        "Parsing tables and amounts from file"),
    # Customer memory
    "list_customer_profiles":   ("Listing customers",          "Loading customer profiles"),
    "search_customer_by_email": ("Looking up customer",        "Searching customer by email"),
    # Calendar
    "create_meeting":           ("Creating meeting",           "Scheduling Google Calendar event"),
    # Document tools
    "generate_content":         ("Generating content",         "Writing document content"),
    "create_pdf":               ("Creating PDF",               "Building PDF document"),
    "create_docx":              ("Creating Word document",     "Building .docx file"),
    "upload_to_drive":          ("Uploading to Drive",         "Saving file to Google Drive"),
    # General
    "web_search":               ("Searching the web",          "Looking up information online"),
    "browse_web":               ("Browsing webpage",           "Reading page content"),
    "classify_text":            ("Classifying content",        "Analysing and categorising text"),
    "generate_report":          ("Generating report",          "Creating structured document"),
    "file_read":                ("Reading file",               "Loading file contents"),
    "file_write":               ("Writing file",               "Saving to file"),
    "export_csv":               ("Exporting CSV",              "Creating spreadsheet export"),
    "cal_read":                 ("Reading calendar",           "Fetching upcoming events"),
    "cal_write":                ("Creating calendar event",    "Adding event to calendar"),
    "delete_file":              ("Deleting file",              "Removing file permanently"),
}


def _step_title_detail(event_type, tname, details):
    """Return (title, detail) for a step."""
    if event_type == "llm_called":
        return "Thinking", "Planning next action (step {})".format(details.get("step", ""))
    if event_type in ("task_completed", "task_resumed"):
        result_preview = str(details.get("result", ""))[:80]
        return "Task complete", result_preview
    if event_type == "approval_needed":
        title, _ = _TOOL_LABELS.get(tname, ("Awaiting approval", ""))
        return "Waiting for approval", "{} requires your review".format(title)
    if event_type == "tool_called":
        title, default_detail = _TOOL_LABELS.get(tname, ("Running tool", tname))
        # Try to extract a meaningful detail from tool_input
        raw_in = details.get("tool_input", "")
        try:
            inp = json.loads(raw_in) if isinstance(raw_in, str) else raw_in
            if tname in ("send_email", "create_draft") and isinstance(inp, dict):
                detail = "To {}".format(inp.get("to") or inp.get("recipient", ""))
            elif tname in ("web_search",) and isinstance(inp, dict):
                detail = "Query: {}".format(inp.get("query", ""))[:80]
            elif tname in ("browse_web",) and isinstance(inp, dict):
                detail = inp.get("url", "")[:80]
            elif tname in ("file_read", "file_write", "delete_file") and isinstance(inp, dict):
                detail = inp.get("path", "")[:80]
            else:
                detail = default_detail
        except Exception:
            detail = default_detail
        return title, detail
    if event_type == "tool_result":
        title, _ = _TOOL_LABELS.get(tname, ("Got result", ""))
        return "Got result", "{} completed".format(title)
    return event_type.replace("_", " ").title(), ""


def _save_audit_steps(task, audit_log, step_offset=0):
    from .models import TaskStep

    agent_name = task.agent.name if task.agent else "Agent"
    saved = 0

    for i, entry in enumerate(audit_log):
        event_type = entry.get("event_type", "")
        details    = entry.get("details", {})
        step_num   = step_offset + i + 1

        if TaskStep.objects.filter(task=task, step_number=step_num).exists():
            continue

        tname, tinput, toutput = "", None, None

        if event_type == "llm_called":
            stype = TaskStep.StepType.THOUGHT
            content = "Thinking... (step {})".format(details.get("step", i + 1))

        elif event_type == "tool_called":
            stype = TaskStep.StepType.TOOL_CALL
            tname = details.get("tool_name", "")
            raw_in = details.get("tool_input", "")
            content = "Calling tool: {}".format(tname)
            try:
                tinput = json.loads(raw_in) if isinstance(raw_in, str) else raw_in
            except Exception:
                tinput = {"raw": str(raw_in)}

        elif event_type == "tool_result":
            stype = TaskStep.StepType.TOOL_RESULT
            tname = details.get("tool_name", "")
            raw_out = details.get("result", "")
            content = str(raw_out)[:500]
            try:
                toutput = (
                    json.loads(raw_out)
                    if isinstance(raw_out, str) and raw_out.strip().startswith("{")
                    else {"result": str(raw_out)}
                )
            except Exception:
                toutput = {"result": str(raw_out)}

        elif event_type in ("task_completed", "task_resumed"):
            stype = TaskStep.StepType.FINAL_ANSWER
            content = str(details.get("result", details.get("task", "Completed")))[:2000]

        elif event_type == "approval_needed":
            stype = TaskStep.StepType.TOOL_CALL
            tname = details.get("tool_name", "")
            content = "Approval required for: {}".format(tname)
            tinput = {"raw": str(details.get("tool_input", ""))}

        else:
            stype = TaskStep.StepType.THOUGHT
            content = "{}: {}".format(event_type, json.dumps(details)[:200])

        title, detail = _step_title_detail(event_type, tname, details)

        TaskStep.objects.create(
            task=task,
            step_number=step_num,
            step_type=stype,
            content=content[:2000],
            tool_name=tname,
            tool_input=tinput,
            tool_output=toutput,
            tool_zone="yellow" if tname in _HIGH_ZONE_TOOLS else "green",
            tokens_used=0,
            agent_name=agent_name,
            title=title,
            detail=detail,
        )
        saved += 1

    return saved


# =============================================================================
# CELERY TASK: send_scheduled_email  (used by schedule_email tool)
# =============================================================================

@shared_task(name="apps.tasks.tasks.send_scheduled_email", max_retries=3, default_retry_delay=60)
def send_scheduled_email(workspace_id: str, to: str, subject: str, body: str, cc: str = ""):
    """Send a previously scheduled email via Gmail API."""
    try:
        from apps.integrations.models import Integration
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        integration = Integration.objects.filter(
            workspace_id=workspace_id,
            provider=Integration.Provider.GMAIL,
            status=Integration.Status.ACTIVE,
        ).first()
        if not integration or not integration.access_token:
            _logger.error("send_scheduled_email: no Gmail integration for workspace=%s", workspace_id)
            return {"error": "Gmail not connected."}

        creds = Credentials(
            token=integration.access_token,
            refresh_token=integration.refresh_token,
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            token_uri="https://oauth2.googleapis.com/token",
        )
        service = build("gmail", "v1", credentials=creds)

        import base64
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["to"]      = to
        msg["subject"] = subject
        if cc:
            msg["cc"] = cc
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        _logger.info("send_scheduled_email: sent to=%r msg_id=%s", to, result.get("id"))
        return {"status": "sent", "to": to, "subject": subject, "msg_id": result.get("id", "")}
    except Exception as exc:
        _logger.exception("send_scheduled_email failed to=%r subject=%r", to, subject)
        raise


# =============================================================================
# CELERY TASK: run_agent_task
# =============================================================================

@shared_task(bind=True, name="apps.tasks.tasks.run_agent_task", max_retries=0)
def run_agent_task(self, task_id: str):
    """Execute the ReAct agent loop for a queued Task."""
    from .models import Task, TaskStep
    from apps.approvals.models import Approval

    try:
        task = Task.objects.select_related("agent", "workspace").get(id=task_id)
    except Task.DoesNotExist:
        return {"error": "Task {} not found".format(task_id)}

    if task.status != Task.Status.QUEUED:
        return {"skipped": "Task already {}".format(task.status)}

    task.status = Task.Status.RUNNING
    task.started_at = timezone.now()
    task.celery_task_id = self.request.id or ""
    task.save(update_fields=["status", "started_at", "celery_task_id"])

    agent_model = task.agent
    workspace_id = task.workspace_id

    # ── Auto-sync agent from template if template has been updated ──────────
    if agent_model and agent_model.template_id:
        try:
            from apps.agents.views import _TEMPLATE_ID_MAP, sync_agent_from_template
            tmpl = _TEMPLATE_ID_MAP.get(agent_model.template_id)
            if tmpl and tmpl.get("version", 0) > (agent_model.template_version or 0):
                synced = sync_agent_from_template(agent_model, tmpl)
                if synced:
                    agent_model.refresh_from_db()
                    _logger.info(
                        "AUTO_SYNC task=%s agent=%s template=%s→v%s",
                        task_id, agent_model.id, tmpl["slug"], tmpl["version"],
                    )
        except Exception as _sync_exc:
            _logger.warning("AUTO_SYNC failed (non-fatal): %s", _sync_exc)
    # ────────────────────────────────────────────────────────────────────────

    tools = (
        _build_tools(agent_model, workspace_id=workspace_id)
        if agent_model
        else [WebSearchTool(), ClassifyTextTool(), GenerateReportTool()]
    )

    from core.llm.groq_provider import GroqProvider
    llm_model = (agent_model.llm_model if agent_model else None) or "llama-3.3-70b-versatile"
    llm = GroqProvider(model=llm_model)

    react_agent = DjangoAgent(
        name=(agent_model.name if agent_model else "Agent"),
        llm_provider=llm,
        tools=tools,
        max_steps=int((agent_model.max_steps if agent_model else None) or 20),
        max_cost=float((agent_model.max_cost_usd if agent_model else None) or 1.0),
        task_id=task_id,
        system_prompt=(agent_model.system_prompt if agent_model else "") or "",
    )

    try:
        result = react_agent.run(task.prompt)
        _save_audit_steps(task, react_agent.audit_log)
        cost = react_agent.get_cost_summary()
        task.status = Task.Status.COMPLETED
        task.result = result
        task.completed_at = timezone.now()
        task.steps_taken = cost["total_steps"]
        task.cost_usd = cost["total_cost_usd"]
        task.total_tokens = llm.total_tokens
        task.save()
        from apps.notifications.utils import notify_task_complete
        notify_task_complete(task)
        return {"status": "completed", "task_id": task_id}

    except ApprovalRequired as exc:
        _save_audit_steps(task, react_agent.audit_log)
        try:
            tool_input_data = json.loads(exc.tool_input) if exc.tool_input else {}
        except Exception:
            tool_input_data = {"raw": str(exc.tool_input)}

        last_step = TaskStep.objects.filter(task=task).order_by("-step_number").first()
        approval = Approval.objects.create(
            task=task,
            step=last_step,
            tool_name=exc.tool_name,
            tool_input=tool_input_data,
            tool_zone="yellow",
            resume_snapshot=react_agent.pending_approval or {},
        )
        cost = react_agent.get_cost_summary()
        task.status = Task.Status.WAITING_APPROVAL
        task.steps_taken = cost["total_steps"]
        task.cost_usd = cost["total_cost_usd"]
        task.save(update_fields=["status", "steps_taken", "cost_usd"])
        from apps.notifications.utils import notify_approval_needed
        notify_approval_needed(task, approval)
        return {"status": "waiting_approval", "approval_id": str(approval.id)}

    except (StepLimitReached, CostLimitReached, RedZoneBlocked) as exc:
        _save_audit_steps(task, react_agent.audit_log)
        cost = react_agent.get_cost_summary()
        task.status = Task.Status.FAILED
        task.error_message = str(exc)
        task.completed_at = timezone.now()
        task.steps_taken = cost["total_steps"]
        task.cost_usd = cost["total_cost_usd"]
        task.save()
        from apps.notifications.utils import notify_task_failed
        notify_task_failed(task)
        return {"status": "failed", "error": str(exc)}

    except Exception as exc:
        _save_audit_steps(task, getattr(react_agent, "audit_log", []))
        task.status = Task.Status.FAILED
        task.error_message = str(exc)[:500]
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        from apps.notifications.utils import notify_task_failed
        notify_task_failed(task)
        raise


# =============================================================================
# CELERY TASK: resume_agent_task  (called after human approval)
# =============================================================================

@shared_task(bind=True, name="apps.tasks.tasks.resume_agent_task", max_retries=0)
def resume_agent_task(self, task_id: str, approval_id: str, approved: bool = True, note: str = ""):
    """Resume a paused agent task after a human decision on an approval."""
    from .models import Task, TaskStep
    from apps.approvals.models import Approval

    try:
        task = Task.objects.select_related("agent", "workspace").get(id=task_id)
        approval = Approval.objects.get(id=approval_id)
    except (Task.DoesNotExist, Approval.DoesNotExist) as exc:
        return {"error": str(exc)}

    if not approved:
        task.status = Task.Status.FAILED
        task.error_message = (
            "Approval rejected by reviewer. Note: {}".format(note)
            if note
            else "Approval rejected by reviewer."
        )
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        from apps.notifications.utils import notify_task_failed
        notify_task_failed(task)
        return {"status": "rejected", "task_id": task_id}

    snapshot = approval.resume_snapshot
    if not snapshot:
        task.status = Task.Status.FAILED
        task.error_message = "No resume snapshot found."
        task.save(update_fields=["status", "error_message"])
        return {"error": "No snapshot"}

    task.status = Task.Status.RUNNING
    task.celery_task_id = self.request.id or ""
    task.save(update_fields=["status", "celery_task_id"])

    agent_model = task.agent
    workspace_id = task.workspace_id
    step_offset = TaskStep.objects.filter(task=task).count()

    tools = (
        _build_tools(agent_model, workspace_id=workspace_id)
        if agent_model
        else [WebSearchTool(), ClassifyTextTool(), GenerateReportTool()]
    )

    from core.llm.groq_provider import GroqProvider
    llm_model = (agent_model.llm_model if agent_model else None) or "llama-3.3-70b-versatile"
    llm = GroqProvider(model=llm_model)

    react_agent = DjangoAgent(
        name=(agent_model.name if agent_model else "Agent"),
        llm_provider=llm,
        tools=tools,
        max_steps=int((agent_model.max_steps if agent_model else None) or 20),
        max_cost=float((agent_model.max_cost_usd if agent_model else None) or 1.0),
        task_id=task_id,
        system_prompt=(agent_model.system_prompt if agent_model else "") or "",
    )

    # Execute the approved tool NOW and inject the real result into messages.
    # (Do NOT send a fake "approved" message — the LLM would call the tool again.)
    _tools_for_resume = _build_tools(agent_model, workspace_id=task.workspace_id)
    _tool_map = {t.name: t for t in _tools_for_resume}
    _approved_tool = _tool_map.get(approval.tool_name)
    _last_tool_call = snapshot.get("last_tool_call", {})
    _tool_input = _last_tool_call.get("input", "") if isinstance(_last_tool_call, dict) else ""
    if not isinstance(_tool_input, str):
        _tool_input = json.dumps(_tool_input)
    _logger.info(
        "RESUME_EXEC task=%s tool=%s tool_found=%s input=%r",
        task_id, approval.tool_name, _approved_tool is not None, str(_tool_input)[:200],
    )
    try:
        _tool_result = _approved_tool.run(_tool_input) if _approved_tool else json.dumps({"error": "tool not found: {}".format(approval.tool_name)})
    except Exception as _exc:
        _logger.error("RESUME_EXEC tool=%s raised %s", approval.tool_name, _exc, exc_info=True)
        _tool_result = json.dumps({"error": str(_exc)})
    _logger.info("RESUME_EXEC task=%s tool=%s result=%r", task_id, approval.tool_name, str(_tool_result)[:300])

    messages = list(snapshot.get("messages_snapshot", []))
    messages.append({
        "role": "assistant",
        "content": snapshot.get("last_assistant_content", ""),
        "tool_call": snapshot.get("last_tool_call"),
    })
    messages.append({
        "role": "tool",
        "name": approval.tool_name,
        "content": _tool_result,  # real result — LLM sees it as done
    })
    # Explicitly tell the LLM the tool finished — prevents small models from
    # looping into irrelevant web searches after an approved action.
    messages.append({
        "role": "user",
        "content": (
            "The '{}' tool has been approved and executed. "
            "Result: {}. "
            "Please give the user a brief one-sentence confirmation. "
            "Do NOT call any more tools."
        ).format(approval.tool_name, _tool_result),
    })

    try:
        result = react_agent.run(
            task=snapshot.get("task", task.prompt),
            initial_messages=messages,
        )
        _save_audit_steps(task, react_agent.audit_log, step_offset=step_offset)
        cost = react_agent.get_cost_summary()
        task.status = Task.Status.COMPLETED
        task.result = result
        task.completed_at = timezone.now()
        task.steps_taken = (task.steps_taken or 0) + cost["total_steps"]
        task.cost_usd = float(task.cost_usd or 0) + cost["total_cost_usd"]
        task.total_tokens = (task.total_tokens or 0) + llm.total_tokens
        task.save()
        from apps.notifications.utils import notify_task_complete
        notify_task_complete(task)
        return {"status": "completed", "task_id": task_id}

    except ApprovalRequired as exc:
        _save_audit_steps(task, react_agent.audit_log, step_offset=step_offset)
        try:
            tool_input_data = json.loads(exc.tool_input) if exc.tool_input else {}
        except Exception:
            tool_input_data = {"raw": str(exc.tool_input)}
        last_step = TaskStep.objects.filter(task=task).order_by("-step_number").first()
        new_approval = Approval.objects.create(
            task=task,
            step=last_step,
            tool_name=exc.tool_name,
            tool_input=tool_input_data,
            tool_zone="yellow",
            resume_snapshot=react_agent.pending_approval or {},
        )
        cost = react_agent.get_cost_summary()
        task.status = Task.Status.WAITING_APPROVAL
        task.steps_taken = (task.steps_taken or 0) + cost["total_steps"]
        task.cost_usd = float(task.cost_usd or 0) + cost["total_cost_usd"]
        task.save(update_fields=["status", "steps_taken", "cost_usd"])
        from apps.notifications.utils import notify_approval_needed
        notify_approval_needed(task, new_approval)
        return {"status": "waiting_approval", "approval_id": str(new_approval.id)}

    except Exception as exc:
        _save_audit_steps(task, getattr(react_agent, "audit_log", []), step_offset=step_offset)
        task.status = Task.Status.FAILED
        task.error_message = str(exc)[:500]
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        from apps.notifications.utils import notify_task_failed
        notify_task_failed(task)
        raise
