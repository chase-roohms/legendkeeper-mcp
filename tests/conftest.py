from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from mcp import Client

from legendkeeper_mcp.config import Settings
from legendkeeper_mcp.server import create_server

from .fake_lk import API_KEY, FakeLegendKeeper
from .spec import SpecValidator, load_spec

INFOBOX = {
    "kind": "infobox",
    "rows": [
        {
            "kind": "keyValue",
            "title": "Facts",
            "rows": [{"key": "Ruler", "text": "Princess Bubblegum"}, {"key": "Population", "text": "Lots"}],
        }
    ],
}


@pytest.fixture(scope="session")
def validator() -> SpecValidator | None:
    spec = load_spec()
    if spec is None:
        if os.environ.get("LK_REQUIRE_SPEC"):
            pytest.fail("The LegendKeeper OpenAPI spec could not be downloaded and LK_REQUIRE_SPEC is set.")
        return None
    return SpecValidator(spec)


def seed(fake: FakeLegendKeeper) -> dict[str, str]:
    ids: dict[str, str] = {}
    locations = fake.add_resource("Locations", icon="map")
    ids["locations"] = locations["id"]
    candy = fake.add_resource(
        "Candy Kingdom",
        locations["id"],
        tags=["Location"],
        blocks=[
            INFOBOX,
            {"kind": "heading", "level": 2, "content": [{"text": "History"}]},
            {"kind": "paragraph", "content": [{"text": "Founded long ago.", "color": "#FF0000"}]},
            {"kind": "heading", "level": 2, "content": [{"text": "Geography"}]},
            {"kind": "paragraph", "content": [{"text": "Sweet hills."}]},
        ],
    )
    ids["candy"] = candy["id"]
    ids["ice"] = fake.add_resource("Ice Kingdom", locations["id"], tags=["Location"])["id"]
    characters = fake.add_resource("Characters")
    ids["characters"] = characters["id"]
    ids["finn"] = fake.add_resource(
        "Finn", characters["id"], tags=["NPC", "Ally"], aliases=["Finn the Human"],
        blocks=[{"kind": "paragraph", "content": [{"text": "A brave hero."}]}],
    )["id"]  # fmt: skip
    ids["pb"] = fake.add_resource("Princess Bubblegum", characters["id"], tags=["NPC", "Candy Kingdom"])["id"]

    world_map = fake.add_resource("World Map", doc_type="map")
    ids["map"] = world_map["id"]
    map_doc = fake.doc_of(world_map["id"])
    map_doc["mapImage"] = {
        "assetId": None, "url": "https://assets.legendkeeper.test/map.png", "width": 2048, "height": 1024,
        "bounds": [[-128, 0], [0, 256]],
    }  # fmt: skip
    map_doc["features"]["pinaaaa1"] = {
        "id": "pinaaaa1", "type": "pin", "name": "Candy Castle", "pos": [-10, 20], "rank": "a0",
        "iconGlyph": "chess-rook", "uri": f"lk://resources/{candy['id']}",
    }  # fmt: skip

    history = fake.add_resource("History", doc_type="time")
    ids["history"] = history["id"]
    time_doc = fake.doc_of(history["id"])
    time_doc["calendarId"] = "gregorian"
    time_doc["lanes"]["lane0001"] = {"id": "lane0001", "name": "Wars", "rank": "a0", "size": "sm"}
    time_doc["events"]["evt00001"] = {
        "id": "evt00001", "laneId": "lane0001", "type": "event", "name": "Mushroom War", "start": 525600.0 * 1000,
        "rank": "a0",
    }  # fmt: skip

    board = fake.add_resource("Brainstorm", doc_type="board")
    ids["board"] = board["id"]
    fake.doc_of(board["id"])["board"] = {
        "pages": [{"id": "page:1", "typeName": "page", "name": "Page 1", "index": "a1", "meta": {}}],
        "shapes": [
            {"id": "shape:1", "typeName": "shape", "type": "note", "x": 0, "y": 0, "rotation": 0, "index": "a1",
             "parentId": "page:1", "isLocked": False, "opacity": 1, "meta": {},
             "props": {"richText": {"type": "doc", "content": [{"type": "paragraph", "content": [
                 {"type": "text", "text": "Villain idea: the Lich"}]}]}}},
        ],
        "bindings": [],
        "assets": [],
    }  # fmt: skip
    ids["template"] = fake.add_resource("NPC Template", "templates", tags=["NPC"])["id"]
    return ids


@pytest.fixture
def fake(validator: SpecValidator | None) -> Any:
    lk = FakeLegendKeeper(validator=validator)
    lk.ids = seed(lk)  # type: ignore[attr-defined]
    yield lk
    assert not lk.contract_errors, "API contract violations:\n" + "\n".join(lk.contract_errors)


@pytest.fixture
def env() -> dict[str, str]:
    return {
        "LEGENDKEEPER_API_KEY": API_KEY,
        "LEGENDKEEPER_BASE_URL": "https://lk.test",
        "LEGENDKEEPER_MAX_RETRIES": "1",
    }


@pytest.fixture
def make_client(fake: FakeLegendKeeper, env: dict[str, str]) -> Callable[..., Any]:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory(**overrides: str) -> AsyncIterator[Client]:
        settings = Settings.from_env({**env, **overrides})
        server, registry = create_server(settings, transport=fake.transport())
        try:
            async with Client(server) as client:
                yield client
        finally:
            await registry.aclose()

    return factory


async def call(client: Client, tool: str, /, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        raise AssertionError(f"{tool} failed: {text}")
    return text


async def call_error(client: Client, tool: str, /, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    assert result.is_error, f"{tool} unexpectedly succeeded: {text}"
    return text
