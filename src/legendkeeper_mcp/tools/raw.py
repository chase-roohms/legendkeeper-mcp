"""Escape hatch: call any LegendKeeper v3 endpoint of the configured project."""

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..client import API_PREFIX
from .common import WRITE, ProjectArg, ToolContext, dump, require_confirm

MAX_OUTPUT = 120_000
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~:@!$&'()*+,;=%-]+$")


def register(t: ToolContext) -> None:
    registry = t.registry
    read_only = t.settings.read_only

    @t.tool(annotations=WRITE)
    async def api_request(
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        path: Annotated[
            str,
            Field(
                description="Path relative to the project, e.g. '/resources/<id>/docs/<docId>/blocks', "
                "'/calendars', '/resources/<id>/properties'. See https://www.legendkeeper.com/developers/api/."
            ),
        ],
        query: Annotated[dict[str, Any] | None, Field(description="Query-string parameters.")] = None,
        body: Annotated[Any | None, Field(description="JSON request body.")] = None,
        if_match: Annotated[
            str | None, Field(description="Document/calendar version for optimistic locking.")
        ] = None,
        confirm: Annotated[bool, Field(description="Required (true) for DELETE.")] = False,
        project: ProjectArg = None,
    ) -> str:
        """Call any LegendKeeper REST v3 endpoint of this project directly (advanced: boards, calendars, block-level
        edits, properties, invites, restores…). Returns the raw JSON response. Writes are disabled in read-only
        mode."""
        if read_only and method != "GET":
            raise ToolError("The server is in read-only mode; only GET requests are allowed.")
        if method == "DELETE":
            require_confirm(confirm, f"send DELETE {path}")
        session = registry.session(project)
        api = await session.api()
        clean = path.strip()
        if clean.startswith(("http://", "https://")):
            raise ToolError("Pass a path, not a full URL.")
        project_prefix = api.path()
        if clean.startswith(f"{API_PREFIX}/projects/"):
            if not clean.startswith(project_prefix):
                raise ToolError("API keys only work inside their own project.")
            clean = clean[len(project_prefix) :]
        clean = clean.split("?", 1)[0].strip("/")
        segments = [s for s in clean.split("/") if s]
        if any(s in (".", "..") or not _SEGMENT_RE.match(s) for s in segments):
            raise ToolError("Invalid path segment.")
        full = project_prefix + ("/" + "/".join(segments) if segments else "")
        result = await session.client.request(method, full, params=query, json=body, if_match=if_match)
        if method != "GET":
            session.invalidate()
        text = "204 No Content" if result is None else dump(result)
        if len(text) > MAX_OUTPUT:
            text = text[:MAX_OUTPUT] + f"\n… truncated ({len(text) - MAX_OUTPUT} more characters)."
        return text
