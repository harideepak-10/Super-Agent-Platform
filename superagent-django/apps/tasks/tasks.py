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

from celery import shared_task
from django.utils import timezone

from core.tools.base_tool import BaseTool, ToolZone
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
            import requests as req
            from urllib.parse import quote
            url = "https://api.duckduckgo.com/?q={}&format=json&no_html=1&skip_disambig=1".format(quote(query))
            r = req.get(url, timeout=10)
            d = r.json()
            abstract = d.get("AbstractText", "")
            related = [t["Text"] for t in d.get("RelatedTopics", [])[:4] if "Text" in t]
            if abstract:
                return "Result for '{}':\n{}\n\nRelated: {}".format(query, abstract, "; ".join(related))
            if related:
                return "Results for '{}':\n".format(query) + "\n".join("- {}".format(r) for r in related)
            return "No results for '{}'. Try a more specific query.".format(query)
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
                integration_type="gmail",
                is_active=True,
            ).first()
            if not integration or not integration.credentials:
                return None
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials(
                token=integration.credentials.get("access_token"),
                refresh_token=integration.credentials.get("refresh_token"),
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


class SendEmailTool(BaseTool):
    name = "send_email"
    description = "Send an email. REQUIRES HUMAN APPROVAL. Input JSON: {\"to\": \"email\", \"subject\": \"...\", \"body\": \"...\"}."
    zone = ToolZone.YELLOW

    def __init__(self, workspace_id=None):
        self._workspace_id = workspace_id

    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str)
        except Exception:
            data = {"raw": input_str}
        return json.dumps({
            "status": "simulated",
            "note": "Email send simulated (no Gmail connected).",
            "to": data.get("to", ""),
            "subject": data.get("subject", ""),
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


class UploadToDriveTool(BaseTool):
    name = "upload_to_drive"
    description = "Upload to Google Drive. Input JSON: {\"filename\": \"...\", \"content\": \"...\", \"folder\": \"...\"}."
    zone = ToolZone.GREEN

    def __init__(self, workspace_id=None):
        pass

    def run(self, input_str: str) -> str:
        return json.dumps({"note": "Google Drive not connected. Connect in Integrations.", "mock": True})

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                    "folder": {"type": "string"},
                },
                "required": ["filename", "content"]},
        }}


# =============================================================================
# TOOL REGISTRY
# =============================================================================

_TOOL_REGISTRY: dict = {
    "web_search":      WebSearchTool,
    "classify_text":   ClassifyTextTool,
    "generate_report": GenerateReportTool,
    "read_email":      ReadEmailTool,
    "send_email":      SendEmailTool,
    "create_draft":    CreateDraftTool,
    "file_read":       FileReadTool,
    "file_write":      FileWriteTool,
    "export_csv":      ExportCsvTool,
    "browse_web":      BrowseWebTool,
    "cal_read":        CalReadTool,
    "cal_write":       CalWriteTool,
    "delete_file":     DeleteFileTool,
    "upload_to_drive": UploadToDriveTool,
}

_HIGH_ZONE_TOOLS = {"send_email", "delete_file", "cal_write", "file_write"}


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
        tools = [WebSearchTool(), ClassifyTextTool(), GenerateReportTool()]
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
        return (
            "You are a helpful AI agent. Use the available tools to complete the task. "
            "Think step by step. When you have enough information, give a clear final answer."
        )


# =============================================================================
# HELPER — save audit_log entries as TaskStep records
# =============================================================================

def _save_audit_steps(task, audit_log, step_offset=0):
    from .models import TaskStep

    saved = 0
    for i, entry in enumerate(audit_log):
        event_type = entry.get("event_type", "")
        details    = entry.get("details", {})
        step_num   = step_offset + i + 1

        if TaskStep.objects.filter(task=task, step_number=step_num).exists():
            continue

        if event_type == "llm_called":
            stype = TaskStep.StepType.THOUGHT
            content = "Thinking... (step {})".format(details.get("step", i + 1))
            tname, tinput, toutput = "", None, None

        elif event_type == "tool_called":
            stype = TaskStep.StepType.TOOL_CALL
            tname = details.get("tool_name", "")
            raw_in = details.get("tool_input", "")
            content = "Calling tool: {}".format(tname)
            try:
                tinput = json.loads(raw_in) if isinstance(raw_in, str) else raw_in
            except Exception:
                tinput = {"raw": str(raw_in)}
            toutput = None

        elif event_type == "tool_result":
            stype = TaskStep.StepType.TOOL_RESULT
            tname = details.get("tool_name", "")
            raw_out = details.get("result", "")
            content = str(raw_out)[:500]
            tinput = None
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
            tname, tinput, toutput = "", None, None

        elif event_type == "approval_needed":
            stype = TaskStep.StepType.TOOL_CALL
            tname = details.get("tool_name", "")
            content = "Approval required for: {}".format(tname)
            tinput = {"raw": str(details.get("tool_input", ""))}
            toutput = None

        else:
            stype = TaskStep.StepType.THOUGHT
            content = "{}: {}".format(event_type, json.dumps(details)[:200])
            tname, tinput, toutput = "", None, None

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
        )
        saved += 1

    return saved


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

    tools = (
        _build_tools(agent_model, workspace_id=workspace_id)
        if agent_model
        else [WebSearchTool(), ClassifyTextTool(), GenerateReportTool()]
    )

    from core.llm.groq_provider import GroqProvider
    llm_model = (agent_model.llm_model if agent_model else None) or "llama-3.1-8b-instant"
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
        return {"status": "failed", "error": str(exc)}

    except Exception as exc:
        _save_audit_steps(task, getattr(react_agent, "audit_log", []))
        task.status = Task.Status.FAILED
        task.error_message = str(exc)[:500]
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
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
    llm_model = (agent_model.llm_model if agent_model else None) or "llama-3.1-8b-instant"
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

    messages = list(snapshot.get("messages_snapshot", []))
    messages.append({
        "role": "assistant",
        "content": snapshot.get("last_assistant_content", ""),
        "tool_call": snapshot.get("last_tool_call"),
    })
    messages.append({
        "role": "tool",
        "name": approval.tool_name,
        "content": json.dumps({
            "approved": True,
            "message": "Action '{}' was approved by a human. Proceed.".format(approval.tool_name),
        }),
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
        return {"status": "waiting_approval", "approval_id": str(new_approval.id)}

    except Exception as exc:
        _save_audit_steps(task, getattr(react_agent, "audit_log", []), step_offset=step_offset)
        task.status = Task.Status.FAILED
        task.error_message = str(exc)[:500]
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_message", "completed_at"])
        raise
