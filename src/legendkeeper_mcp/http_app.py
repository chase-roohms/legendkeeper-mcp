"""Streamable-HTTP hosting with bearer-token protection."""

from __future__ import annotations

import hmac
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .config import ConfigError, Settings

PUBLIC_PATHS = frozenset({"/healthz"})
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class BearerAuthMiddleware:
    """Reject HTTP requests that do not carry ``Authorization: Bearer <token>`` (except public paths)."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token.encode() if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        header = b""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                header = value
                break
        scheme, _, supplied = header.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(supplied.strip(), self.token):
            response = JSONResponse(
                {"error": "unauthorized", "message": "Missing or invalid bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="legendkeeper-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def check_http_security(settings: Settings) -> None:
    if settings.auth_token or settings.allow_unauthenticated:
        return
    if settings.host in _LOOPBACK:
        return
    raise ConfigError(
        f"Refusing to serve HTTP on {settings.host} without MCP_AUTH_TOKEN. Set MCP_AUTH_TOKEN (recommended), "
        "bind MCP_HOST=127.0.0.1, or set MCP_ALLOW_UNAUTHENTICATED=true if you really mean it."
    )


def build_http_app(server: MCPServer, settings: Settings) -> Any:
    check_http_security(settings)

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    security: TransportSecuritySettings | None = None
    if settings.allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"http://{h}" for h in settings.allowed_hosts]
            + [f"https://{h}" for h in settings.allowed_hosts],
        )
    app = server.streamable_http_app(
        streamable_http_path=settings.http_path,
        transport_security=security,
        host=settings.host,
    )
    return BearerAuthMiddleware(app, settings.auth_token)
