"""MCP server assembly."""

from __future__ import annotations

import httpx
from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Settings
from .guides import MARKDOWN_GUIDE, SERVER_INSTRUCTIONS, STYLE_GUIDE
from .service import Registry
from .tools import assets, browse, documents, facts, maps, raw, resources, timelines
from .tools.common import ToolContext


def create_server(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[MCPServer, Registry]:
    """Build the MCP server and the project registry it uses.

    ``transport`` lets tests substitute an in-memory LegendKeeper API.
    """
    registry = Registry(settings, transport=transport)
    instructions = SERVER_INSTRUCTIONS
    if settings.read_only:
        instructions += "\nThis server is in READ-ONLY mode: only reading tools are available.\n"
    if len(settings.projects) > 1:
        names = ", ".join(sorted(settings.projects))
        instructions += (
            f"\nSeveral worlds are configured ({names}); default: {settings.default_project}. "
            "Pass `project` to target another one.\n"
        )

    server = MCPServer(
        name="legendkeeper",
        title="LegendKeeper",
        description="Unofficial MCP server for the LegendKeeper worldbuilding API",
        instructions=instructions,
        website_url="https://github.com/chase-roohms/legendkeeper-mcp",
        version=__version__,
        log_level=settings.log_level
        if settings.log_level in ("DEBUG", "INFO", "WARNING", "ERROR")
        else "INFO",  # type: ignore[arg-type]
    )

    ctx = ToolContext(server=server, registry=registry, settings=settings)
    for module in (browse, documents, resources, facts, maps, timelines, assets, raw):
        module.register(ctx)

    @server.resource(
        "legendkeeper://guides/markdown",
        name="markdown-guide",
        title="LegendKeeper Markdown dialect",
        description="How Markdown maps to LegendKeeper blocks, mentions, callouts and preserved blocks.",
        mime_type="text/markdown",
    )
    def markdown_guide() -> str:
        return MARKDOWN_GUIDE

    @server.resource(
        "legendkeeper://guides/style",
        name="style-guide",
        title="LegendKeeper content conventions",
        description="Conventions for tags, icons, infoboxes, links, map pins and timeline events.",
        mime_type="text/markdown",
    )
    def style_guide() -> str:
        return STYLE_GUIDE

    return server, registry
