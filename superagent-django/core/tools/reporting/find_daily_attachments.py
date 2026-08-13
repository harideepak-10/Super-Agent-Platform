"""
FindDailyAttachmentsTool — find a given day's email attachments, download
them locally, and classify each into a section. GREEN — read-only from
Gmail's perspective (downloading to a local temp file is not an external
write), so no approval needed for this step. The actual Drive upload is a
separate YELLOW tool (organize_attachments_to_drive) that takes this tool's
output as input, matching the existing GREEN-prepare/YELLOW-commit pattern
used everywhere else in this system (list_events → create_meeting,
generate_content → create_pdf).
"""
from __future__ import annotations

import base64
import json
import os
import tempfile

from core.tools.base_tool import BaseTool, ToolZone


# Best-effort, deterministic keyword classification — no LLM call, so it's
# fast, free, and never invents a section that isn't supported by the
# filename/subject text actually present.
_SECTION_KEYWORDS = {
    "Invoices":  ["invoice", "bill", "receipt", "payment due", "amount due"],
    "Contracts": ["contract", "agreement", "nda", "terms and conditions", "sow", "statement of work"],
    "Reports":   ["report", "summary", "statement", "analysis"],
    "Images":    [],  # matched by file extension instead, see below
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


def _classify_section(filename: str, subject: str) -> str:
    lower = f"{filename} {subject}".lower()
    ext = os.path.splitext(filename)[1].lower()
    if ext in _IMAGE_EXTS:
        return "Images"
    for section, keywords in _SECTION_KEYWORDS.items():
        if section == "Images":
            continue
        if any(kw in lower for kw in keywords):
            return section
    return "Other"


class FindDailyAttachmentsTool(BaseTool):
    """Find and download a day's email attachments, classified by section.

    Input::

        {"date": "today", "max_emails": 50}

    Returns::

        {
            "date": "2026-08-13",
            "emails_scanned": 7,
            "emails_with_attachments": 3,
            "attachments": [
                {
                    "filename": "invoice_INV-2026-1001.pdf",
                    "section": "Invoices",
                    "local_path": "/tmp/krypsos_docs/eod_.../invoice_INV-2026-1001.pdf",
                    "size_bytes": 48213,
                    "source_email_subject": "Invoice Summary - INV-2026-1001",
                    "source_email_from": "ABC Technologies <billing@abc.com>",
                },
                ...
            ],
            "errors": [ {"filename": "...", "error": "..."} , ... ]  # any attachment that failed to download
        }

    Pass the "attachments" list from this result straight into
    organize_attachments_to_drive to actually upload and organize them.
    """

    name: str = "find_daily_attachments"
    description: str = (
        "Find a given day's email attachments, download them locally, and classify each into "
        "a section (Invoices, Contracts, Reports, Images, Other) based on filename/subject "
        "keywords — a real, deterministic classification, not a guess. GREEN — read-only, no "
        "approval needed. Input JSON: {\"date\": \"today\"(optional, default today; accepts "
        "'yesterday' or YYYY-MM-DD), \"max_emails\": 50(optional)}. Returns the real list of "
        "attachments found (empty list is a valid, honest result if none exist) — pass that "
        "list directly to organize_attachments_to_drive to upload and organize them."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, gmail_service=None, workspace_id=None):
        self._gmail_service = gmail_service
        self._workspace_id = workspace_id

    # ------------------------------------------------------------------
    def run(self, input_str: str) -> str:
        try:
            data = json.loads(input_str) if isinstance(input_str, str) else (input_str or {})
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid input. Expected JSON."})

        if not isinstance(data, dict):
            data = {}

        date_raw = str(data.get("date") or "today").strip()
        try:
            max_emails = min(int(data.get("max_emails", 50) or 50), 200)
        except (TypeError, ValueError):
            max_emails = 50

        if not self._gmail_service:
            return json.dumps({
                "error": "Gmail is not connected. Please go to Integrations and connect your Gmail account first.",
            })

        date_label, gmail_query_date = self._resolve_date(date_raw)
        if date_label is None:
            return json.dumps({"error": f"Could not understand date '{date_raw}'. Use 'today', 'yesterday', or YYYY-MM-DD."})

        query = f"has:attachment after:{gmail_query_date[0]} before:{gmail_query_date[1]}"

        try:
            result = (
                self._gmail_service.users().messages()
                .list(userId="me", q=query, maxResults=max_emails)
                .execute()
            )
            msg_refs = result.get("messages", [])
        except Exception as exc:
            return json.dumps({"error": f"Gmail search failed: {exc}"})

        out_dir = os.path.join(tempfile.gettempdir(), "krypsos_docs", f"eod_{date_label}")
        os.makedirs(out_dir, exist_ok=True)

        attachments = []
        errors = []
        emails_with_attachments = 0

        for ref in msg_refs:
            try:
                msg = self._gmail_service.users().messages().get(
                    userId="me", id=ref["id"], format="full"
                ).execute()
            except Exception as exc:
                errors.append({"message_id": ref.get("id", ""), "error": f"Could not fetch email: {exc}"})
                continue

            payload = msg.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            subject = headers.get("subject", "(no subject)")
            sender = headers.get("from", "(unknown)")

            parts = list(self._walk_parts(payload.get("parts", [])))
            if not parts:
                continue
            emails_with_attachments += 1

            for fname, att_id, inline_data in parts:
                try:
                    if att_id:
                        raw_att = (
                            self._gmail_service.users().messages().attachments()
                            .get(userId="me", messageId=ref["id"], id=att_id)
                            .execute()
                        )
                        file_bytes = base64.urlsafe_b64decode(raw_att.get("data", "") + "==")
                    elif inline_data:
                        file_bytes = base64.urlsafe_b64decode(inline_data + "==")
                    else:
                        errors.append({"filename": fname, "error": "No data available for this attachment."})
                        continue

                    safe_name = fname or f"attachment_{len(attachments) + 1}"
                    local_path = os.path.join(out_dir, safe_name)
                    with open(local_path, "wb") as f:
                        f.write(file_bytes)

                    attachments.append({
                        "filename": safe_name,
                        "section": _classify_section(safe_name, subject),
                        "local_path": local_path,
                        "size_bytes": len(file_bytes),
                        "source_email_subject": subject,
                        "source_email_from": sender,
                    })
                except Exception as exc:
                    errors.append({"filename": fname, "error": f"Could not download attachment: {exc}"})

        return json.dumps({
            "date": date_label,
            "emails_scanned": len(msg_refs),
            "emails_with_attachments": emails_with_attachments,
            "attachment_count": len(attachments),
            "attachments": attachments,
            "errors": errors,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    @staticmethod
    def _walk_parts(parts):
        """Yield (filename, attachment_id, inline_data) for every file part, recursively."""
        for part in parts:
            fname = part.get("filename", "")
            body = part.get("body", {})
            att_id = body.get("attachmentId", "")
            inline = body.get("data", "")
            sub_parts = part.get("parts", [])
            if fname:
                yield fname, att_id, inline
            if sub_parts:
                yield from FindDailyAttachmentsTool._walk_parts(sub_parts)

    @staticmethod
    def _resolve_date(date_raw: str):
        """Return (label, (after_str, before_str)) for Gmail's after:/before: query syntax."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Kolkata")
        now = datetime.now(tz)
        lower = date_raw.lower().strip()

        if lower == "today":
            target = now.date()
        elif lower == "yesterday":
            target = (now - timedelta(days=1)).date()
        else:
            try:
                target = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                return None, None

        label = target.isoformat()
        after = target.strftime("%Y/%m/%d")
        before = (target + timedelta(days=1)).strftime("%Y/%m/%d")
        return label, (after, before)

    # ------------------------------------------------------------------
    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "'today' (default), 'yesterday', or YYYY-MM-DD"},
                    "max_emails": {"type": "integer", "description": "Max emails to scan (default 50, max 200)"},
                },
            },
        }}