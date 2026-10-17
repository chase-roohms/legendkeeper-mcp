"""Shared helpers for tool modules."""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..config import ConfigError, Settings
from ..errors import LegendKeeperMCPError
from ..service import Registry

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

ProjectArg = Annotated[
    str | None,
    Field(
        description="Configured project (world) name. Omit to use the default project; see list_projects.",
    ),
]
ResourceArg = Annotated[str, Field(description="Resource id, or its exact name/alias (case-insensitive).")]
DocumentArg = Annotated[
    str | None,
    Field(description="Document id or name within the resource. Omit to pick the resource's main document."),
]

READ = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
UPDATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)


@dataclass
class ToolContext:
    server: MCPServer
    registry: Registry
    settings: Settings

    def tool(
        self, *, annotations: ToolAnnotations, write: bool = False, name: str | None = None
    ) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
        """Register an async tool (skipped for write tools in read-only mode) with error translation."""

        def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
            if write and self.settings.read_only:
                return fn
            wrapped = translate_errors(fn)
            self.server.add_tool(
                wrapped,
                name=name or fn.__name__,
                description=(fn.__doc__ or "").strip(),
                annotations=annotations,
                structured_output=False,
            )
            return fn

        return decorator


def translate_errors(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except (LegendKeeperMCPError, ConfigError) as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(f"Invalid input: {exc}") from exc

    return wrapper


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values (so optional tool arguments are not sent to the API)."""
    return {k: v for k, v in d.items() if v is not None}


def require_confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise ToolError(
            f"Refusing to {what} without confirm=true. Ask the user, then call again with confirm=true."
        )
