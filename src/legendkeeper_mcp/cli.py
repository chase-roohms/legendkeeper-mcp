"""Command-line entry point: ``legendkeeper-mcp [serve|check]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence

from . import __version__
from .config import ConfigError, Settings
from .errors import LegendKeeperMCPError

logger = logging.getLogger("legendkeeper_mcp")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legendkeeper-mcp",
        description="Unofficial MCP server for the LegendKeeper worldbuilding API. Configure with environment "
        "variables (LEGENDKEEPER_API_KEY, …); flags override them.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the MCP server (default).")
    for p in (parser, serve):
        p.add_argument("--transport", choices=["stdio", "http"], help="Transport (env MCP_TRANSPORT).")
        p.add_argument("--host", help="HTTP bind address (env MCP_HOST, default 127.0.0.1).")
        p.add_argument("--port", type=int, help="HTTP port (env MCP_PORT, default 8000).")
        p.add_argument("--read-only", action="store_true", default=None, help="Only expose reading tools.")
    sub.add_parser("check", help="Verify the configured API keys and print the projects they open.")
    return parser


def _apply_overrides(args: argparse.Namespace) -> None:
    if getattr(args, "transport", None):
        os.environ["MCP_TRANSPORT"] = args.transport
    if getattr(args, "host", None):
        os.environ["MCP_HOST"] = args.host
    if getattr(args, "port", None):
        os.environ["MCP_PORT"] = str(args.port)
    if getattr(args, "read_only", None):
        os.environ["LEGENDKEEPER_READ_ONLY"] = "true"


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    _apply_overrides(args)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"legendkeeper-mcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "check":
            raise SystemExit(asyncio.run(check(settings)))
        asyncio.run(serve(settings))
    except ConfigError as exc:
        print(f"legendkeeper-mcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        pass


async def serve(settings: Settings) -> None:
    from .server import create_server

    server, registry = create_server(settings)
    projects = ", ".join(f"{p.name} ({p.masked_key})" for p in settings.projects.values())
    logger.info(
        "legendkeeper-mcp %s starting (%s transport, read_only=%s, projects: %s)",
        __version__, settings.transport, settings.read_only, projects,
    )  # fmt: skip
    try:
        if settings.transport == "http":
            import uvicorn

            from .http_app import build_http_app

            app = build_http_app(server, settings)
            config = uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
                lifespan="on",
            )
            logger.info("Listening on http://%s:%s%s", settings.host, settings.port, settings.http_path)
            await uvicorn.Server(config).serve()
        else:
            await server.run_stdio_async()
    finally:
        await registry.aclose()


async def check(settings: Settings) -> int:
    from .service import Registry

    registry = Registry(settings)
    failures = 0
    try:
        for name in registry.names:
            session = registry.session(name)
            label = f"[{name}{' (default)' if name == settings.default_project else ''}] key {session.config.masked_key}"
            try:
                info = await session.info()
                api = await session.api()
                page = await api.get("resources", params={"limit": 1})
                more = "+" if (page or {}).get("hasMore") else ""
                count = len((page or {}).get("data") or [])
                rate = session.client.rate_limit
                remaining = (
                    f", rate limit {rate.remaining}/{rate.limit} left" if rate.remaining is not None else ""
                )
                print(f"OK   {label}: project '{info.get('name')}' (id {info.get('id')}), "
                      f"resources readable ({count}{more}){remaining}")  # fmt: skip
            except LegendKeeperMCPError as exc:
                failures += 1
                print(f"FAIL {label}: {exc}")
    finally:
        await registry.aclose()
    print(f"Read-only mode: {settings.read_only}")
    return 1 if failures else 0


if __name__ == "__main__":
    main()
