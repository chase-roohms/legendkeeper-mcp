"""Live smoke tests against the real LegendKeeper API.

Skipped unless ``LEGENDKEEPER_LIVE_TESTS=1`` and ``LEGENDKEEPER_API_KEY`` are set. Write tests additionally need
``LEGENDKEEPER_LIVE_WRITE=1``. They create one sandbox resource (named ``[mcp-test] …``), put everything under it,
and delete it (to the trash) afterwards.

    LEGENDKEEPER_LIVE_TESTS=1 LEGENDKEEPER_LIVE_WRITE=1 uv run pytest tests/test_live.py -v
"""

from __future__ import annotations

import json
import os
import re
import struct
import time
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client

from legendkeeper_mcp.config import Settings
from legendkeeper_mcp.server import create_server

from .conftest import call, call_error

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("LEGENDKEEPER_LIVE_TESTS") == "1" and os.environ.get("LEGENDKEEPER_API_KEY")),
        reason="set LEGENDKEEPER_LIVE_TESTS=1 and LEGENDKEEPER_API_KEY to run live tests",
    ),
]
WRITE = os.environ.get("LEGENDKEEPER_LIVE_WRITE") == "1"


def tiny_png(width: int = 64, height: int = 32) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + b"".join(bytes((x * 4 % 256, y * 8 % 256, 160)) for x in range(width))
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@asynccontextmanager
async def live_client() -> AsyncIterator[Client]:
    settings = Settings.from_env()
    server, registry = create_server(settings)
    try:
        async with Client(server) as c:
            yield c
    finally:
        await registry.aclose()


async def test_read_tools() -> None:
    async with live_client() as client:
        await _read_tools(client)


async def _read_tools(client: Client) -> None:
    projects = json.loads(await call(client, "list_projects"))
    assert projects["projects"][0].get("projectId"), projects
    tree = await call(client, "browse_tree", depth=1)
    assert "resources in the world" in tree
    await call(client, "list_tags")
    assert "gregorian" in await call(client, "list_calendars")
    await call(client, "search", query="the")
    await call(client, "list_members")


@pytest.mark.skipif(not WRITE, reason="set LEGENDKEEPER_LIVE_WRITE=1 to run write tests")
async def test_full_write_cycle(tmp_path: Path) -> None:
    async with live_client() as client:
        await _write_cycle(client, tmp_path)


async def _write_cycle(client: Client, tmp_path: Path) -> None:
    name = f"[mcp-test] {time.strftime('%Y-%m-%d %H:%M:%S')}"
    created = json.loads(
        await call(
            client,
            "create_resource",
            name=name,
            icon_glyph="flask",
            tags=["MCP Test"],
            content="Sandbox created by the legendkeeper-mcp live tests. Safe to delete.",
        )
    )
    sandbox = created["id"]
    try:
        # Child pages with mentions, batch creation and rich Markdown.
        batch = await call(
            client, "create_resources",
            resources=[
                {"name": f"{name} Hero", "key": "hero", "parent": sandbox, "tags": ["MCP Test"]},
                {"name": f"{name} Town", "parent": sandbox, "content": f"Home of the [[{name} Hero]]."},
            ],
        )  # fmt: skip
        hero_id = re.search(r"Hero \(id (\w+)", batch).group(1)  # type: ignore[union-attr]

        markdown = f"""# Overview

A paragraph with **bold**, *italic*, ~~strike~~, `code`, <u>underline</u> and a [link](https://example.com).
It mentions [[{name} Hero]] and [the hero again](lk://resources/{hero_id}).

> [!warning]
> A warning callout.

> [!secret]
> GM-only secret.

> [!expand] Details
> Hidden details.

- bullet
  1. nested ordered

- [ ] open task
- [x] done task

| Key | Value |
|---|---|
| A | 1 |

```python
print("hello")
```

---

## History

Old history.
"""
        await call(client, "write_page", resource=sandbox, mode="replace", markdown=markdown)
        await call(
            client, "set_facts", resource=sandbox, facts={"Founded": "Today", "Hero": f"[[{name} Hero]]"}
        )
        await call(client, "write_page", resource=sandbox, mode="replace_section", section="History",
                   markdown="New history.")  # fmt: skip
        page = await call(client, "read_document", resource=sandbox)
        for expected in ("# Overview", "**bold**", "> [!warning]", "> [!secret]", "> [!expand] Details",
                         "- [x] done task", "| A | 1 |", "```python", "New history.", 'kind="infobox"',
                         f"lk://resources/{hero_id}"):  # fmt: skip
            assert expected in page, f"{expected!r} missing from:\n{page}"
        assert "Old history." not in page

        # Round trip: writing back exactly what was read must keep every block.
        body = page.split("---\n", 1)[1]
        result = await call(client, "write_page", resource=sandbox, mode="replace", markdown=body)
        assert "0 new block(s)" in result, result
        again = await call(client, "read_document", resource=sandbox)
        assert again.split("---\n", 1)[1] == body

        # Image upload, map document with a pin, timeline with an event.
        image = tmp_path / "map.png"
        image.write_bytes(tiny_png())
        asset = json.loads(await call(client, "upload_image", source=str(image), name=f"{name} map"))
        map_doc = json.loads(await call(client, "create_document", resource=sandbox, name="Map", type="map",
                                        map_image_asset_id=asset["id"]))  # fmt: skip
        pin = json.loads(await call(client, "create_map_feature", resource=sandbox, document=map_doc["id"],
                                    name="Town", position=[16, 32], pixel_coordinates=True, icon_glyph="house",
                                    link=hero_id))  # fmt: skip
        assert pin["iconShape"] == "none"
        await call(
            client, "create_document", resource=sandbox, name="Timeline", type="time", calendar_id="gregorian"
        )
        event = await call(client, "create_timeline_event", resource=sandbox, document="Timeline", name="Founding",
                           start="1 March 1200", link=hero_id)  # fmt: skip
        assert "Created event 'Founding'" in event
        assert "Founding" in await call(client, "read_document", resource=sandbox, document="Timeline")
        assert "Town" in await call(client, "read_document", resource=sandbox, document="Map")

        await call(
            client, "update_resource", resource=sandbox, add_tags=["MCP Sandbox"], banner_url=asset["url"]
        )
        assert "matches several" not in await call(client, "get_resource", resource=sandbox)
    finally:
        await call(client, "delete_resource", resource=sandbox, cascade=True, confirm=True)
    assert "No resource" in await call_error(client, "get_resource", resource=sandbox)
