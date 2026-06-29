"""
Extract structured data tool — uses the LLM to pull fields from document text.

Zone: GREEN — runs automatically, no human approval required.

Accepts an optional ``llm_provider`` so tests can inject MockLLMProvider
instead of making real API calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract structured data from the document text below.
Return ONLY a valid JSON object with these fields (use null if not found):
{
  "document_type": "invoice | contract | receipt | quote | other",
  "vendor_name": "string or null",
  "vendor_email": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "total_amount": "number as string (e.g. '1250.00') or null",
  "currency": "3-letter code e.g. USD or null",
  "line_items": [{"description": "...", "quantity": ..., "unit_price": ..., "total": ...}],
  "payment_terms": "string or null",
  "notes": "string or null"
}

Document text:
\"\"\"
{document_text}
\"\"\"

Return ONLY the JSON object, no explanation."""


class ExtractDataTool(BaseTool):
    """Use the LLM to extract structured fields from document text.

    Input format (JSON string)::

        {
            "text": "<full document text>",
            "document_type": "invoice"   // optional hint
        }

    Returns:
        JSON string with the extracted structured fields.
        On failure, returns ``{"error": "...", "raw_text": "..."}``.
    """

    name: str = "extract_data"
    description: str = (
        "Extracts structured fields (invoice number, vendor, amounts, dates, etc.) "
        "from document text using the LLM. "
        "Input JSON: {\"text\": \"<document text>\", \"document_type\": \"invoice\"}. "
        "Returns a JSON object with extracted fields."
    )
    zone: ToolZone = ToolZone.GREEN

    def __init__(self, llm_provider: Any = None) -> None:
        """Initialise with an optional LLM provider.

        Args:
            llm_provider: If provided, used for extraction calls.
                          If None, the caller must set ``_llm_provider``
                          (DocumentAgent injects its own LLM).
        """
        self._llm_provider = llm_provider

    def run(self, input_str: str) -> str:
        text, doc_type = self._parse_input(input_str)
        if not text.strip():
            return json.dumps({"error": "No document text provided."})

        if self._llm_provider is None:
            return json.dumps({
                "error": "No LLM provider configured for extract_data tool.",
                "raw_text": text[:200],
            })

        prompt = _EXTRACTION_PROMPT.replace("{document_text}", text[:4000])
        try:
            response = self._llm_provider.send(
                [{"role": "user", "content": prompt}],
                tools=None,
            )
            content = response.get("content", "")
            return self._parse_llm_response(content, text)
        except Exception as exc:
            error_msg = f"ExtractDataTool LLM error: {exc}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg, "raw_text": text[:200]})

    @staticmethod
    def _parse_input(input_str: str) -> tuple[str, str]:
        if input_str and input_str.strip().startswith("{"):
            try:
                data = json.loads(input_str)
                return str(data.get("text", "")), str(data.get("document_type", ""))
            except json.JSONDecodeError:
                pass
        return input_str, ""

    @staticmethod
    def _parse_llm_response(content: str, original_text: str) -> str:
        """Try to parse the LLM's JSON response; fall back gracefully."""
        content = content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                l for l in lines
                if not l.startswith("```")
            ).strip()

        try:
            parsed = json.loads(content)
            return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            logger.warning("ExtractDataTool: LLM did not return valid JSON")
            return json.dumps({
                "error": "LLM response was not valid JSON",
                "raw_response": content[:500],
            })
