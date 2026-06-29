"""
Move file tool — moves a Drive file to a different folder.

Zone: YELLOW — ALWAYS requires human approval before execution.

Moving files can be destructive (wrong folder, loss of access).
BaseAgent raises ApprovalRequired before this tool's run() is called.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.tools.base_tool import BaseTool, ToolZone

logger = logging.getLogger(__name__)


class MoveFileTool(BaseTool):
    """Move a Google Drive file to a new parent folder.

    Zone: YELLOW — BaseAgent will raise ApprovalRequired before
    this tool's ``run()`` is ever called automatically.

    Input format (JSON string)::

        {
            "file_id":          "<Drive file ID>",
            "destination_id":   "<Drive folder ID>",
            "file_name":        "Invoice_2024_001.pdf"  // optional, for logging
        }

    Returns:
        JSON string with keys:
            ``status``        : "moved"
            ``file_id``       : str
            ``destination_id``: str
            ``file_name``     : str
            ``previous_parents``: list[str]
    """

    name: str = "move_file"
    description: str = (
        "Moves a Google Drive file to a different folder. "
        "YELLOW zone — always requires human approval. "
        "Input JSON: {\"file_id\": \"...\", \"destination_id\": \"...\", "
        "\"file_name\": \"optional\"}. "
        "Returns JSON with status, file_id, destination_id, previous_parents."
    )
    zone: ToolZone = ToolZone.YELLOW  # ← Never changes

    def __init__(self, drive_service: Any = None) -> None:
        self._injected_service = drive_service
        self._service: Any = None

    def run(self, input_str: str) -> str:
        """Move the file (only called after human approval).

        Args:
            input_str: JSON string with file_id and destination_id.

        Returns:
            JSON result string.

        Raises:
            ValueError: If required fields are missing.
            RuntimeError: If the Drive API call fails.
        """
        params = self._parse_and_validate(input_str)
        file_id: str = params["file_id"]
        destination_id: str = params["destination_id"]
        file_name: str = params.get("file_name", file_id)

        logger.info(
            f"MoveFileTool: moving file {file_name!r} ({file_id}) "
            f"to folder {destination_id}"
        )

        service = self._get_service()

        try:
            # Get current parents
            file_meta = service.files().get(
                fileId=file_id, fields="parents"
            ).execute()
            previous_parents: list[str] = file_meta.get("parents", [])
            previous_parents_str = ",".join(previous_parents)

            # Move: add new parent, remove old parents
            updated = service.files().update(
                fileId=file_id,
                addParents=destination_id,
                removeParents=previous_parents_str,
                fields="id, parents",
            ).execute()

        except Exception as exc:
            error_msg = f"Drive API error while moving file {file_id!r}: {exc}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from exc

        logger.info(f"MoveFileTool: file {file_name!r} moved successfully")

        return json.dumps({
            "status": "moved",
            "file_id": file_id,
            "file_name": file_name,
            "destination_id": destination_id,
            "previous_parents": previous_parents,
        })

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        if self._service is None:
            from core.tools.drive.auth import DriveAuth
            self._service = DriveAuth().build_service("default")
        return self._service

    @staticmethod
    def _parse_and_validate(input_str: str) -> dict[str, str]:
        if not input_str or not input_str.strip():
            raise ValueError("MoveFileTool received empty input.")

        try:
            params = json.loads(input_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"MoveFileTool expects JSON with 'file_id' and 'destination_id'. "
                f"Got: {input_str!r}"
            ) from exc

        missing = [f for f in ("file_id", "destination_id") if not params.get(f)]
        if missing:
            raise ValueError(f"MoveFileTool missing required fields: {missing}")

        return {
            "file_id": str(params["file_id"]).strip(),
            "destination_id": str(params["destination_id"]).strip(),
            "file_name": str(params.get("file_name", params["file_id"])).strip(),
        }
