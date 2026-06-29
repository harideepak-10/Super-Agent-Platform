"""
SummarizeThreadTool — summarize an email conversation (GREEN zone).

Takes a thread (list of messages) and produces a concise summary
including: topic, participants, key decisions, and open action items.
This tool uses the LLM directly for summarization.
"""

from __future__ import annotations
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone


class SummarizeThreadTool(BaseTool):
    name = "summarize_thread"
    description = (
        "Summarize a full email thread into key points. "
        "Input: { messages: list[{from, date, body}], topic: str = '' }. "
        "Returns: { summary: str, participants: list, key_decisions: list, open_items: list, urgency: str }."
    )
    zone = ToolZone.GREEN

    def run(self, tool_input: "str | dict[str, Any]") -> Any:
        if isinstance(tool_input, str):
            try:
                import json
                tool_input = json.loads(tool_input)
            except (json.JSONDecodeError, TypeError):
                tool_input = {}
        messages = tool_input.get("messages", [])
        topic = tool_input.get("topic", "")

        if not messages:
            return {"error": "messages list is required"}

        # Build a readable transcript
        transcript_parts = []
        participants = set()
        for i, msg in enumerate(messages, 1):
            sender = msg.get("from", "Unknown")
            date = msg.get("date", "")
            body = msg.get("body", msg.get("snippet", ""))[:500]
            participants.add(sender.split("<")[0].strip())
            transcript_parts.append(f"[{i}] From: {sender} | {date}\n{body}")

        transcript = "\n\n".join(transcript_parts)

        # The agent's LLM will process this in the ReAct loop.
        # We return a structured analysis the agent can use.
        return {
            "transcript_for_analysis": transcript,
            "topic": topic or "(detect from content)",
            "participants": list(participants),
            "message_count": len(messages),
            "instruction": (
                "Analyze the above email transcript and provide: "
                "1) A 2-3 sentence summary, "
                "2) Key decisions made, "
                "3) Open action items, "
                "4) Urgency level (low/medium/high/critical)."
            ),
        }
