"""End-to-end tests: MCP client -> server -> fake LegendKeeper API (validated against the OpenAPI spec)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from .conftest import call, call_error
from .fake_lk import PROJECT_ID

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def page_blocks(fake: Any, rid: str) -> list[dict[str, Any]]:
    return fake.doc_of(rid)["blocks"]


# ------------------------------------------------------------------------------------------ discovery


async def test_lists_tools_and_resources(make_client):
    async with make_client() as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"write_page", "read_document", "set_facts", "api_request", "upload_image"} <= tools
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert "legendkeeper://guides/markdown" in resources
        guide = await client.read_resource("legendkeeper://guides/markdown")
        assert "lk:keep" in guide.contents[0].text
        assert "LegendKeeper" in (client.instructions or "")


async def test_read_only_mode_hides_write_tools(make_client, fake):
    async with make_client(LEGENDKEEPER_READ_ONLY="true") as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert "write_page" not in tools
        assert "delete_resource" not in tools
        assert {"read_document", "search", "api_request"} <= tools
        text = await call_error(client, "api_request", method="POST", path="/resources", body={"name": "x"})
        assert "read-only" in text
        assert not fake.calls("POST")


async def test_list_projects(make_client):
    async with make_client() as client:
        data = json.loads(await call(client, "list_projects"))
        assert data["projects"][0]["projectId"] == PROJECT_ID
        assert data["projects"][0]["title"] == "Land of Ooo"
        assert data["projects"][0]["default"] is True


async def test_browse_tree(make_client, fake):
    async with make_client() as client:
        text = await call(client, "browse_tree")
        assert "- Locations (id" in text
        assert "  - Candy Kingdom" in text
        assert "#Location" in text
        assert "NPC Template" not in text
        assert "{map}" in text
        sub = await call(client, "browse_tree", parent="Characters", depth=1)
        assert "Finn" in sub and "Locations" not in sub.split("\n", 1)[1]


async def test_search(make_client):
    async with make_client() as client:
        text = await call(client, "search", query="sweet")
        assert "Candy Kingdom" in text and "match in document 'Main'" in text
        assert "No results" in await call(client, "search", query="zzzz")


async def test_get_resource_by_name_and_alias(make_client, fake):
    async with make_client() as client:
        text = await call(client, "get_resource", resource="finn the human")
        data = json.loads(text)
        assert data["id"] == fake.ids["finn"]
        assert data["path"] == "Characters"
        assert data["tags"] == ["NPC", "Ally"]


async def test_ambiguous_and_missing_names(make_client, fake):
    fake.add_resource("Finn", fake.ids["locations"])
    async with make_client() as client:
        text = await call_error(client, "get_resource", resource="Finn")
        assert "matches several resources" in text
        text = await call_error(client, "get_resource", resource="Nobody Here")
        assert "No resource with id or name" in text


async def test_list_tags_and_fallback_for_member_keys(make_client, fake):
    async with make_client() as client:
        text = await call(client, "list_tags")
        assert "- NPC (3)" in text and "- Location (2)" in text
    fake.admin = False
    async with make_client() as client:
        text = await call(client, "list_tags")
        assert "- NPC" in text


async def test_list_templates_calendars_members_assets(make_client):
    async with make_client() as client:
        assert "NPC Template" in await call(client, "list_templates")
        assert "Gregorian (id gregorian): 12 months" in await call(client, "list_calendars")
        assert "Finn — OWNER" in await call(client, "list_members")
        assert "No assets" in await call(client, "list_assets")


# ------------------------------------------------------------------------------------------ reading


async def test_read_page_as_markdown(make_client, fake):
    async with make_client() as client:
        text = await call(client, "read_document", resource="Candy Kingdom")
        assert '<!-- lk:keep id="' in text and 'kind="infobox"' in text
        assert "- **Ruler:** Princess Bubblegum" in text
        assert "## History" in text and "Founded long ago." in text
        assert "Path: Locations" in text
        raw = json.loads(await call(client, "read_document", resource="Candy Kingdom", format="json"))
        assert raw["blocks"][0]["kind"] == "infobox"
        assert "Founded long ago." in await call(
            client, "read_document", resource="Candy Kingdom", format="text"
        )
        js = await call(client, "read_document", resource="Candy Kingdom", opaque_blocks="json")
        assert "```lk-block" in js


async def test_read_map_timeline_board(make_client):
    async with make_client() as client:
        text = await call(client, "read_document", resource="World Map")
        assert 'pin "Candy Castle" (id pinaaaa1) at [-10, 20]' in text
        assert "→ Candy Kingdom" in text
        assert "2048×1024" in text
        text = await call(client, "read_document", resource="History")
        assert "Mushroom War" in text and "[lane Wars]" in text and "calendar gregorian" in text
        text = await call(client, "read_document", resource="Brainstorm")
        assert "Villain idea: the Lich" in text


async def test_read_empty_page(make_client):
    async with make_client() as client:
        assert "(empty page)" in await call(client, "read_document", resource="Ice Kingdom")


# ------------------------------------------------------------------------------------------ writing pages


async def test_write_page_append_with_mentions(make_client, fake):
    async with make_client() as client:
        text = await call(
            client, "write_page", resource="Finn", mode="append",
            markdown="He serves [[Princess Bubblegum]] and visits [[Candy Kingdom|the kingdom]].\n\n"
            "> [!secret]\n> Secretly fears the ocean.",
        )  # fmt: skip
        assert "mode append" in text
    blocks = page_blocks(fake, fake.ids["finn"])
    assert blocks[0]["content"] == [{"text": "A brave hero."}]
    runs = blocks[1]["content"]
    assert {"mention": {"resourceId": fake.ids["pb"]}} in runs
    assert {"mention": {"resourceId": fake.ids["candy"], "text": "the kingdom"}} in runs
    assert blocks[2]["kind"] == "secret"


async def test_write_page_reports_unresolved_links(make_client):
    async with make_client() as client:
        text = await call(
            client, "write_page", resource="Finn", mode="append", markdown="Meets [[Nobody Known]]."
        )
        assert "Could not resolve wiki link [[Nobody Known]]" in text


async def test_write_page_replace_preserves_kept_and_unchanged_blocks(make_client, fake):
    rid = fake.ids["candy"]
    original = page_blocks(fake, rid)
    infobox_id, colored_id = original[0]["id"], original[2]["id"]
    async with make_client() as client:
        current = await call(client, "read_document", resource=rid)
        markdown = current.split("---\n", 1)[1].replace("Sweet hills.", "Sweet, rolling hills.")
        text = await call(client, "write_page", resource=rid, mode="replace", markdown=markdown)
        assert "1 new block(s)" in text and "1 preserved" in text
    blocks = page_blocks(fake, rid)
    assert blocks[0]["id"] == infobox_id and blocks[0]["kind"] == "infobox"
    assert blocks[2]["id"] == colored_id and blocks[2]["content"][0]["color"] == "#FF0000"
    assert blocks[4]["content"] == [{"text": "Sweet, rolling hills."}]
    put = fake.calls("PUT", "/blocks")[-1]
    assert put.headers["if-match"] == "1"


async def test_write_page_warns_when_keep_region_is_edited(make_client, fake):
    rid = fake.ids["candy"]
    async with make_client() as client:
        current = await call(client, "read_document", resource=rid)
        markdown = current.split("---\n", 1)[1].replace("Population:** Lots", "Population:** Few")
        text = await call(client, "write_page", resource=rid, mode="replace", markdown=markdown)
        assert "was edited" in text and "set_facts" in text


async def test_write_page_sections(make_client, fake):
    rid = fake.ids["candy"]
    async with make_client() as client:
        await call(client, "write_page", resource=rid, mode="replace_section", section="history",
                   markdown="Rebuilt after the war.")  # fmt: skip
        await call(client, "write_page", resource=rid, mode="append_to_section", section="Geography",
                   markdown="- Cotton candy forest")  # fmt: skip
        text = await call(client, "write_page", resource=rid, mode="append_to_section", section="Notable People",
                          markdown="Peppermint Butler.")  # fmt: skip
        assert "did not exist" in text
    blocks = page_blocks(fake, rid)
    kinds = [b["kind"] for b in blocks]
    assert kinds == [
        "infobox",
        "heading",
        "paragraph",
        "heading",
        "paragraph",
        "bulletList",
        "heading",
        "paragraph",
    ]
    assert blocks[2]["content"] == [{"text": "Rebuilt after the war."}]
    assert blocks[6]["content"] == [{"text": "Notable People"}]


async def test_write_page_retries_once_on_version_conflict(make_client, fake):
    fake.fail("PUT", r"/blocks$", 412, "VERSION_MISMATCH")
    async with make_client() as client:
        await call(client, "write_page", resource="Finn", mode="prepend", markdown="# Finn")
    assert len(fake.calls("PUT", "/blocks")) == 2
    assert page_blocks(fake, fake.ids["finn"])[0]["kind"] == "heading"


async def test_write_page_rejects_non_page_documents(make_client):
    async with make_client() as client:
        text = await call_error(client, "write_page", resource="World Map", mode="append", markdown="x")
        assert "no page or blank document" in text


async def test_write_page_section_mode_requires_section(make_client):
    async with make_client() as client:
        text = await call_error(client, "write_page", resource="Finn", mode="replace_section", markdown="x")
        assert "needs `section`" in text


async def test_write_page_lk_block_fence(make_client, fake):
    md = '```lk-block\n{"kind":"embed","provider":"youtube","url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}\n```'
    async with make_client() as client:
        await call(client, "write_page", resource="Finn", mode="append", markdown=md)
        bad = await call_error(
            client, "write_page", resource="Finn", mode="append", markdown="```lk-block\n{nope\n```"
        )
        assert "Invalid JSON" in bad
    assert page_blocks(fake, fake.ids["finn"])[-1]["kind"] == "embed"


# ------------------------------------------------------------------------------------------ resources


async def test_create_resource_with_content(make_client, fake):
    async with make_client() as client:
        text = await call(
            client, "create_resource", name="Marceline", parent="Characters", tags=["NPC"], icon_glyph="guitar",
            icon_color="#AA0000", content="# Marceline\n\nFriend of [[Finn]].\n\n- [ ] Write backstory",
        )  # fmt: skip
        created = json.loads(text)
    resource = fake.resources[created["id"]]
    assert resource["parentId"] == fake.ids["characters"]
    assert resource["iconGlyph"] == "guitar"
    blocks = page_blocks(fake, created["id"])
    assert [b["kind"] for b in blocks] == ["heading", "paragraph", "taskList"]
    assert {"mention": {"resourceId": fake.ids["finn"]}} in blocks[1]["content"]


async def test_create_resource_with_typed_document(make_client, fake):
    async with make_client() as client:
        created = json.loads(
            await call(client, "create_resource", name="Wars", document_type="time", calendar_id="gregorian")
        )
    assert fake.doc_of(created["id"])["type"] == "time"


async def test_create_resources_batch_with_keys(make_client, fake):
    async with make_client() as client:
        text = await call(
            client, "create_resources",
            resources=[
                {"name": "Fire Kingdom", "key": "fire", "parent": "Locations", "tags": ["Location"]},
                {"name": "Flame Princess", "parent_key": "fire", "content": "Ruler of the [[Fire Kingdom]]."},
            ],
        )  # fmt: skip
        assert "Created 2 resources" in text
        bad = await call_error(client, "create_resources", resources=[{"name": "X", "parent_key": "missing"}])
        assert "earlier item" in bad
    by_name = {r["name"]: r for r in fake.resources.values()}
    assert by_name["Flame Princess"]["parentId"] == by_name["Fire Kingdom"]["id"]
    assert by_name["Fire Kingdom"]["parentId"] == fake.ids["locations"]


async def test_update_resource_tags_and_banner(make_client, fake):
    async with make_client() as client:
        await call(client, "update_resource", resource="Finn", add_tags=["Hero", "NPC"], remove_tags=["ally"],
                   add_aliases=["The Human"], banner_url="https://assets.legendkeeper.test/b.png")  # fmt: skip
        assert "Nothing to update" in await call_error(client, "update_resource", resource="Finn")
    finn = fake.resources[fake.ids["finn"]]
    assert finn["tags"] == ["NPC", "Hero"]
    assert finn["aliases"] == ["Finn the Human", "The Human"]
    assert finn["banner"] == {
        "url": "https://assets.legendkeeper.test/b.png",
        "enabled": True,
        "yPosition": 50,
    }


async def test_move_delete_restore(make_client, fake):
    async with make_client() as client:
        assert "Moved 'Finn'" in await call(client, "move_resource", resource="Finn", new_parent="Locations")
        assert fake.resources[fake.ids["finn"]]["parentId"] == fake.ids["locations"]
        await call(client, "move_resource", resource="Finn", new_parent="root")
        assert fake.resources[fake.ids["finn"]]["parentId"] is None

        assert "confirm=true" in await call_error(client, "delete_resource", resource="Locations")
        assert not fake.calls("DELETE")
        text = await call_error(client, "delete_resource", resource="Locations", confirm=True)
        assert "HAS_CHILDREN" in text and "cascade=true" in text
        text = await call(client, "delete_resource", resource="Locations", confirm=True, cascade=True)
        assert "2 descendant(s)" in text
        assert fake.resources[fake.ids["candy"]]["deleted"]
        assert "Restored 'Locations'" in await call(
            client, "restore_resource", resource_id=fake.ids["locations"]
        )


async def test_create_from_template(make_client, fake):
    async with make_client() as client:
        created = json.loads(await call(client, "create_from_template", template="NPC Template", name="Jake",
                                        parent="Characters"))  # fmt: skip
    assert fake.resources[created["id"]]["tags"] == ["NPC"]


# ------------------------------------------------------------------------------------------ documents


async def test_document_lifecycle(make_client, fake):
    async with make_client() as client:
        created = json.loads(
            await call(client, "create_document", resource="Finn", name="Notes", type="page")
        )
        await call(
            client, "update_document", resource="Finn", document="Notes", name="GM Notes", is_hidden=True
        )
        assert fake.documents[created["id"]]["name"] == "GM Notes"
        await call(
            client, "write_page", resource="Finn", document="GM Notes", mode="append", markdown="Secret stuff"
        )
        assert fake.documents[created["id"]]["blocks"][0]["content"] == [{"text": "Secret stuff"}]
        assert "confirm=true" in await call_error(
            client, "delete_document", resource="Finn", document="GM Notes"
        )
        await call(client, "delete_document", resource="Finn", document=created["id"], confirm=True)
        assert fake.documents[created["id"]]["deleted"]


# ------------------------------------------------------------------------------------------ facts


async def test_set_facts_updates_existing_infobox(make_client, fake):
    rid = fake.ids["candy"]
    async with make_client() as client:
        text = await call(client, "set_facts", resource=rid,
                          facts={"ruler": "[[Princess Bubblegum]]", "Population": None, "Capital": "Candy Castle"})  # fmt: skip
        assert "updated ruler" in text and "removed Population" in text and "added Capital" in text
    rows = page_blocks(fake, rid)[0]["rows"][0]["rows"]
    assert [r["key"] for r in rows] == ["Ruler", "Capital"]
    assert rows[0]["content"] == [{"mention": {"resourceId": fake.ids["pb"]}}]


async def test_set_facts_creates_infobox_and_sidebar(make_client, fake):
    async with make_client() as client:
        text = await call(client, "set_facts", resource="Finn", facts={"Race": "Human", "Age": "**14**"})
        assert "Created an infobox" in text
        text = await call(client, "set_facts", resource="Finn", target="sidebar", facts={"Status": "Alive"})
        assert "Created sidebar property 'Facts'" in text
        text = await call(client, "set_facts", resource="Finn", target="sidebar", facts={"Status": "Missing"})
        assert "updated Status" in text
        info = await call(client, "get_resource", resource="Finn")
        assert "Status:** Missing" in info
    blocks = page_blocks(fake, fake.ids["finn"])
    assert blocks[0]["kind"] == "infobox"
    assert blocks[0]["rows"][0]["rows"][1]["content"] == [{"text": "14", "bold": True}]
    assert blocks[1]["content"] == [{"text": "A brave hero."}]


async def test_set_facts_on_empty_page(make_client, fake):
    async with make_client() as client:
        await call(client, "set_facts", resource="Ice Kingdom", facts={"Ruler": "Ice King"})
    assert page_blocks(fake, fake.ids["ice"])[0]["kind"] == "infobox"


# ------------------------------------------------------------------------------------------ maps


async def test_map_features(make_client, fake):
    doc = fake.doc_of(fake.ids["map"])
    async with make_client() as client:
        pin = json.loads(await call(client, "create_map_feature", resource="World Map", name="Tree Fort",
                                    position=[-20, 40], link="Finn", icon_glyph="tree"))  # fmt: skip
        assert pin["iconShape"] == "none" and pin["uri"] == f"lk://resources/{fake.ids['finn']}"
        px = json.loads(await call(client, "create_map_feature", resource="World Map", name="Center", type="label",
                                   position=[1024, 512], pixel_coordinates=True))  # fmt: skip
        assert px["pos"] == [-64.0, 128.0]
        region = json.loads(await call(client, "create_map_feature", resource="World Map", name="Grasslands",
                                       type="region", points=[[0, 0], [-5, 5], [-5, 0]], color="#00AA00"))  # fmt: skip
        assert region["polygon"] == [[0, 0], [-5, 5], [-5, 0]]
        assert "at least 3" in await call_error(client, "create_map_feature", resource="World Map", name="Bad",
                                                type="region", points=[[0, 0]])  # fmt: skip
        await call(
            client, "update_map_feature", resource="World Map", feature_id=pin["id"], name="Tree House"
        )
        assert doc["features"][pin["id"]]["name"] == "Tree House"
        await call(client, "delete_map_feature", resource="World Map", feature_id=pin["id"], confirm=True)
        assert pin["id"] not in doc["features"]


# ------------------------------------------------------------------------------------------ timelines


async def test_timeline_events_and_lanes(make_client, fake):
    doc = fake.doc_of(fake.ids["history"])
    async with make_client() as client:
        text = await call(client, "create_timeline_event", resource="History", name="Lich Escapes", start="1004",
                          link="Finn")  # fmt: skip
        assert "Created event 'Lich Escapes'" in text
        text = await call(client, "create_timeline_event", resource="History", name="Coronation", start="1010",
                          lane="Royalty")  # fmt: skip
        assert "Created lane 'Royalty'" in text
        royalty = next(lane for lane in doc["lanes"].values() if lane["name"] == "Royalty")
        assert royalty["rank"] > "a0"
        event_id = next(e["id"] for e in doc["events"].values() if e["name"] == "Coronation")
        await call(client, "update_timeline_event", resource="History", event_id=event_id, lane="Wars",
                   end="1011")  # fmt: skip
        assert doc["events"][event_id]["laneId"] == "lane0001"
        assert "No lane 'Nope'" in await call_error(client, "update_timeline_event", resource="History",
                                                    event_id=event_id, lane="Nope")  # fmt: skip
        await call(client, "create_timeline_lane", resource="History", name="Omens", color="#112233")
        await call(client, "delete_timeline_event", resource="History", event_id=event_id, confirm=True)
        assert event_id not in doc["events"]
        assert "Pass `start`" in await call_error(
            client, "create_timeline_event", resource="History", name="x"
        )


async def test_timeline_on_new_document_creates_default_lane(make_client, fake):
    async with make_client() as client:
        created = json.loads(
            await call(client, "create_resource", name="Calendar of Ooo", document_type="time")
        )
        await call(client, "create_timeline_event", resource=created["id"], name="Start", start="1")
    lanes = fake.doc_of(created["id"])["lanes"]
    assert [lane["name"] for lane in lanes.values()] == ["Events"]


# ------------------------------------------------------------------------------------------ assets


async def test_upload_image_from_file(make_client, fake, tmp_path: Path):
    image = tmp_path / "portrait.png"
    image.write_bytes(PNG)
    async with make_client() as client:
        data = json.loads(await call(client, "upload_image", source=str(image)))
        assert data["name"] == "portrait" and data["url"].startswith("https://assets.legendkeeper.test/")
        notimg = tmp_path / "notes.txt"
        notimg.write_text("hello")
        assert "Not a JPEG" in await call_error(client, "upload_image", source=str(notimg))
        assert "File not found" in await call_error(client, "upload_image", source=str(tmp_path / "nope.png"))
    upload = fake.calls("POST", "/assets/uploads")[0].body
    assert upload["contentType"] == "image/png" and upload["contentLength"] == len(PNG)


async def test_upload_image_respects_upload_root(make_client, tmp_path: Path):
    image = tmp_path / "a.png"
    image.write_bytes(PNG)
    async with make_client(LEGENDKEEPER_UPLOAD_ROOT=str(tmp_path / "allowed")) as client:
        assert "limited to files under" in await call_error(client, "upload_image", source=str(image))


async def test_upload_image_from_url(make_client, fake):
    fake.downloads["/dragon.png"] = PNG
    async with make_client() as client:
        data = json.loads(
            await call(client, "upload_image", source="https://images.example/dragon.png", name="Dragon")
        )
        assert data["name"] == "Dragon"
    async with make_client(LEGENDKEEPER_ALLOW_URL_UPLOADS="false") as client:
        assert "disabled" in await call_error(
            client, "upload_image", source="https://images.example/dragon.png"
        )


# ------------------------------------------------------------------------------------------ raw API


async def test_api_request(make_client, fake):
    async with make_client() as client:
        text = await call(client, "api_request", method="GET", path="/calendars")
        assert "gregorian" in text
        text = await call(client, "api_request", method="GET", path=f"/api/v3/projects/{PROJECT_ID}/tags")
        assert "NPC" in text
        assert "own project" in await call_error(
            client, "api_request", method="GET", path="/api/v3/projects/other/tags"
        )
        assert "Invalid path" in await call_error(
            client, "api_request", method="GET", path="/resources/../../x"
        )
        assert "confirm=true" in await call_error(
            client, "api_request", method="DELETE", path=f"/resources/{fake.ids['ice']}"
        )
        text = await call(
            client, "api_request", method="DELETE", path=f"/resources/{fake.ids['ice']}", confirm=True
        )
        assert text == "204 No Content"


# ------------------------------------------------------------------------------------------ errors & projects


async def test_api_errors_are_explained(make_client, fake):
    fake.fail("GET", r"/search$", 402, "PAID_PLAN_REQUIRED")
    async with make_client() as client:
        text = await call_error(client, "search", query="x")
        assert "PAID_PLAN_REQUIRED" in text and "paid plan" in text


async def test_bad_api_key(make_client):
    async with make_client(LEGENDKEEPER_API_KEY="lk_wrong") as client:
        text = await call_error(client, "browse_tree")
        assert "UNAUTHENTICATED" in text


async def test_multiple_projects(make_client, fake):
    async with make_client(
        LEGENDKEEPER_API_KEY_OTHER="lk_other", LEGENDKEEPER_DEFAULT_PROJECT="default"
    ) as client:
        data = json.loads(await call(client, "list_projects"))
        names = {p["name"]: p for p in data["projects"]}
        assert set(names) == {"default", "other"}
        assert "UNAUTHENTICATED" in names["other"]["error"]
        assert "Unknown project" in await call_error(client, "search", query="x", project="nope")
        assert "UNAUTHENTICATED" in await call_error(client, "search", query="x", project="other")


@pytest.mark.parametrize("project_id", ["proj0001"])
async def test_configured_project_id_skips_discovery(make_client, fake, project_id):
    async with make_client(LEGENDKEEPER_PROJECT_ID=project_id) as client:
        await call(client, "search", query="finn")
    assert all(r.path != "/api/v3/projects" for r in fake.requests)


# ------------------------------------------------------------------------------------------ review regressions


async def test_append_content_identical_to_existing_blocks(make_client, fake):
    rid = fake.ids["candy"]
    async with make_client() as client:
        await call(
            client, "write_page", resource=rid, mode="append", markdown="## Geography\n\nSweet hills.\n\n---"
        )
        await call(client, "write_page", resource=rid, mode="append", markdown="---")
        await call(client, "write_page", resource=rid, mode="append_to_section", section="Geography",
                   markdown="## History")  # fmt: skip
        await call(client, "write_page", resource=rid, mode="prepend", markdown="## History")
        await call(client, "write_page", resource=rid, mode="replace_section", section="History",
                   markdown="Sweet hills.")  # fmt: skip
    blocks = page_blocks(fake, rid)
    ids = [b["id"] for b in blocks]
    assert len(ids) == len(set(ids))


async def test_keep_marker_outside_written_range_is_ignored(make_client, fake):
    rid = fake.ids["candy"]
    box = page_blocks(fake, rid)[0]["id"]
    async with make_client() as client:
        text = await call(client, "write_page", resource=rid, mode="append",
                          markdown=f'<!-- lk:keep id="{box}" kind="infobox" -->\n\nMore.')  # fmt: skip
        assert "outside the part being written" in text
    assert [b["kind"] for b in page_blocks(fake, rid)].count("infobox") == 1


async def test_replace_section_renames_heading(make_client, fake):
    rid = fake.ids["candy"]
    async with make_client() as client:
        await call(client, "write_page", resource=rid, mode="replace_section", section="History",
                   markdown="## Ancient History\n\nLong ago.")  # fmt: skip
    titles = [b["content"][0]["text"] for b in page_blocks(fake, rid) if b["kind"] == "heading"]
    assert titles == ["Ancient History", "Geography"]


async def test_expected_version_prevents_lost_updates(make_client, fake):
    async with make_client() as client:
        page = await call(client, "read_document", resource="Finn")
        version = re.search(r"version (\S+)", page).group(1)
        fake.doc_of(fake.ids["finn"])["version"] += 1  # someone edits in the app
        text = await call_error(client, "write_page", resource="Finn", mode="replace", markdown="Gone",
                                expected_version=version)  # fmt: skip
        assert "changed since version" in text
        assert not fake.calls("PUT", "/blocks")
        fake.fail("PUT", r"/blocks$", 412, "VERSION_MISMATCH")
        current = str(fake.doc_of(fake.ids["finn"])["version"])
        text = await call_error(client, "write_page", resource="Finn", mode="replace", markdown="Gone",
                                expected_version=current)  # fmt: skip
        assert "nothing was written" in text
    assert page_blocks(fake, fake.ids["finn"])[0]["content"] == [{"text": "A brave hero."}]


async def test_append_retry_after_ambiguous_success_does_not_duplicate(make_client, fake):
    rid = fake.ids["finn"]

    def apply_then_conflict(record):
        # Simulate: the first PUT reached the server, but the client saw a timeout and its retry got 412.
        if record.method == "PUT" and record.path.endswith("/blocks") and not getattr(fake, "_done", False):
            fake._done = True
            doc = fake.doc_of(rid)
            doc["blocks"] = fake._with_ids(json.loads(json.dumps(record.body["blocks"])))
            doc["version"] += 1
            fake.fail("PUT", r"/blocks$", 412, "VERSION_MISMATCH")

    fake.before_handle = apply_then_conflict
    async with make_client() as client:
        text = await call(client, "write_page", resource="Finn", mode="append", markdown="Appended once.")
        assert "already present" in text
    texts = [b["content"][0]["text"] for b in page_blocks(fake, rid)]
    assert texts == ["A brave hero.", "Appended once."]


async def test_move_resource_reorders_within_parent(make_client, fake):
    async with make_client() as client:
        await call(client, "move_resource", resource="Finn", before=fake.ids["pb"])
        assert "parentId" not in fake.calls("POST", "/move")[-1].body
        assert "Pass new_parent" in await call_error(client, "move_resource", resource="Finn")
    assert fake.resources[fake.ids["finn"]]["parentId"] == fake.ids["characters"]


async def test_create_document_validates_before_creating(make_client, fake):
    async with make_client() as client:
        text = await call_error(client, "create_document", resource="Finn", name="x", type="page",
                                map_image_asset_id="a1")  # fmt: skip
        assert "only applies to map" in text
    assert not fake.calls("POST", "/docs")


async def test_upload_image_source_forms(make_client):
    async with make_client() as client:
        # Windows drive letters are paths, not URL schemes.
        assert "File not found" in await call_error(client, "upload_image", source="C:\\no\\such\\image.png")
        assert "local file path or an http(s) URL" in await call_error(
            client, "upload_image", source="ftp://example.com/x.png"
        )


async def test_batch_links_resolve_to_items_in_the_same_batch(make_client, fake):
    async with make_client() as client:
        text = await call(
            client, "create_resources",
            resources=[
                {"name": "Witch", "content": "Lives in [[Bog]] and fears [[the old one]]."},
                {"name": "Bog"},
                {"name": "Elder", "aliases": ["The Old One"]},
            ],
        )  # fmt: skip
        assert "Warning" not in text
    by_name = {r["name"]: r for r in fake.resources.values()}
    runs = page_blocks(fake, by_name["Witch"]["id"])[0]["content"]
    assert {"mention": {"resourceId": by_name["Bog"]["id"]}} in runs
    assert {"mention": {"resourceId": by_name["Elder"]["id"], "text": "the old one"}} in runs


async def test_read_document_prefers_typed_documents_over_blank(make_client, fake):
    created = fake.add_resource("Era", doc_type="blank")
    timeline = fake.add_document(created["id"], "Timeline", "time")
    timeline["lanes"]["l1"] = {"id": "l1", "name": "Main", "rank": "a0", "size": "sm"}
    async with make_client() as client:
        text = await call(client, "read_document", resource="Era")
        assert "type time" in text and "Lanes: 'Main'" in text
        assert "{blank}" not in await call(client, "browse_tree")
