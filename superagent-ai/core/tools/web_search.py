"""
WebSearchTool — search the web using DuckDuckGo.
"""
from __future__ import annotations

import json

from core.tools.base_tool import BaseTool, ToolZone


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for current information. Input JSON: {\"query\": \"search query\"}."
    zone: ToolZone = ToolZone.GREEN

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

    def to_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                },
                "required": ["query"],
            },
        }}
