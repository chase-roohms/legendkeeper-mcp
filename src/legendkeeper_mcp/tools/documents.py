"""Tools for reading and writing documents (pages, maps, timelines, boards)."""

from typing import Annotated, Any, Literal

from pydantic import Field

from ..documents import (
    pick_document,
    read_board,
    read_map,
    read_page,
    read_timeline,
    write_markdown,
)
from ..errors import InputError
from .common import (
    DESTRUCTIVE,
    READ,
    UPDATE,
    WRITE,
    DocumentArg,
    ProjectArg,
    ResourceArg,
    ToolContext,
    brief,
    compact,
    dump,
)
from .common import require_confirm as _confirm

MAX_OUTPUT = 120_000
DOC_FIELDS = ("id", "name", "type", "isHidden", "isFullWidth", "calendarId", "mapImage")


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n\n… output truncated ({len(text) - MAX_OUTPUT} more characters)."


def register(t: ToolContext) -> None:
    registry = t.registry

    @t.tool(annotations=READ)
    async def read_document(
        resource: ResourceArg,
        document: DocumentArg = None,
        format: Annotated[
            Literal["markdown", "text", "json"],
            Field(description="markdown (default, editable), text (plain text) or json (raw API data)."),
        ] = "markdown",
        opaque_blocks: Annotated[
            Literal["keep", "json"],
            Field(
                description="How page blocks Markdown cannot express are shown: 'keep' = readable summary between "
                "lk:keep markers (preserved on write), 'json' = editable ```lk-block JSON fences."
            ),
        ] = "keep",
        project: ProjectArg = None,
    ) -> str:
        """Read a document of a resource. Pages come back as Markdown (with mentions as [Name](lk://resources/id)
        links); maps list their pins/regions/paths with coordinates; timelines list lanes and events; boards list
        their text. Without `document`, the first page document (or the first document) is used."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document, ("page", "blank", "map", "time", "board"))
        index = await session.index()
        kind = doc.get("type")
        result: str | dict[str, Any]
        if kind == "map":
            result = await read_map(api, index, rid, doc, fmt=format)
        elif kind == "time":
            result = await read_timeline(api, index, rid, doc, fmt=format)
        elif kind == "board":
            result = await read_board(api, index, rid, doc, fmt=format)
        else:
            result = await read_page(api, index, rid, doc, fmt=format, opaque=opaque_blocks)
        return _clip(result if isinstance(result, str) else dump(result))

    @t.tool(annotations=WRITE, write=True)
    async def write_page(
        resource: ResourceArg,
        markdown: Annotated[
            str,
            Field(description="Page content in LegendKeeper Markdown (see legendkeeper://guides/markdown)."),
        ],
        mode: Annotated[
            Literal["append", "prepend", "replace", "replace_section", "append_to_section"],
            Field(
                description="append/prepend: add to the end/start. replace: replace the whole page (keep lk:keep "
                "markers to preserve special blocks). replace_section: replace the content under the heading "
                "`section` (include the heading in `markdown` to rename it). append_to_section: add to the end of "
                "that section. A missing section is created at the end of the page."
            ),
        ],
        section: Annotated[
            str | None, Field(description="Heading text for the *_section modes (case-insensitive).")
        ] = None,
        expected_version: Annotated[
            str | None,
            Field(
                description="The page version shown by read_document. When set, nothing is written if the page "
                "changed since then. Strongly recommended with mode='replace' or 'replace_section'."
            ),
        ] = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Write Markdown to a page document. [[Name]] and [text](lk://resources/<id>) become mentions;
        callouts, secrets, tables, task lists and images are supported. Writes are atomic; pass expected_version
        (from read_document) so edits made in the app after you read the page are never overwritten."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document, ("page", "blank"))
        outcome = await write_markdown(session, rid, doc, markdown, mode, section, expected_version)
        lines = [
            f"Wrote '{doc.get('name')}' of {(await session.index()).name_of(rid) or rid} (mode {mode}): page now has "
            f"{outcome.blocks} top-level blocks, version {outcome.version}.",
            f"{outcome.created} new block(s), {outcome.reused} unchanged block(s) reused, {outcome.kept} preserved.",
        ]
        if outcome.note:
            lines.append(outcome.note)
        lines.extend(f"Warning: {w}" for w in outcome.warnings)
        return "\n".join(lines)

    @t.tool(annotations=WRITE, write=True)
    async def create_document(
        resource: ResourceArg,
        name: Annotated[str, Field(min_length=1, max_length=500)],
        type: Annotated[
            Literal["page", "map", "time", "board"],
            Field(description="page (wiki text), map, time (timeline) or board (whiteboard)."),
        ],
        calendar_id: Annotated[
            str | None,
            Field(description="For timelines: calendar id from list_calendars (e.g. 'gregorian')."),
        ] = None,
        map_image_asset_id: Annotated[
            str | None, Field(description="For maps: id of an uploaded image asset to use as the map.")
        ] = None,
        is_hidden: Annotated[bool, Field(description="Hide the document from non-admins.")] = False,
        project: ProjectArg = None,
    ) -> str:
        """Add a document (tab) to a resource: a page, a map, a timeline or a board. A resource holds at most 5."""
        if map_image_asset_id and type != "map":
            raise InputError("map_image_asset_id only applies to map documents.")
        if calendar_id and type != "time":
            raise InputError("calendar_id only applies to timeline (time) documents.")
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        body = compact({"name": name, "type": type, "calendarId": calendar_id, "isHidden": is_hidden or None})
        doc = await api.post("resources", rid, "docs", json=body)
        if map_image_asset_id:
            doc = await api.patch(
                "resources", rid, "docs", doc["id"], json={"mapImage": {"assetId": map_image_asset_id}}
            )
        return brief(doc, DOC_FIELDS)

    @t.tool(annotations=UPDATE, write=True)
    async def update_document(
        resource: ResourceArg,
        document: Annotated[str, Field(description="Document id or name.")],
        name: Annotated[str | None, Field(max_length=500)] = None,
        is_hidden: bool | None = None,
        is_full_width: bool | None = None,
        calendar_id: Annotated[str | None, Field(description="Timeline calendar id.")] = None,
        map_image_asset_id: Annotated[
            str | None, Field(description="Image asset id to set as the map image.")
        ] = None,
        project: ProjectArg = None,
    ) -> str:
        """Rename a document, hide/show it, toggle full width, set a timeline's calendar or a map's image."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document)
        body = compact(
            {
                "name": name,
                "isHidden": is_hidden,
                "isFullWidth": is_full_width,
                "calendarId": calendar_id,
                "mapImage": {"assetId": map_image_asset_id} if map_image_asset_id else None,
            }
        )
        if not body:
            raise InputError("Nothing to update.")
        return brief(await api.patch("resources", rid, "docs", doc["id"], json=body), DOC_FIELDS)

    @t.tool(annotations=DESTRUCTIVE, write=True)
    async def delete_document(
        resource: ResourceArg,
        document: Annotated[str, Field(description="Document id or name.")],
        confirm: Annotated[bool, Field(description="Must be true. Ask the user before deleting.")] = False,
        project: ProjectArg = None,
    ) -> str:
        """Delete one document (tab) of a resource. It can be restored with api_request
        (POST /resources/{id}/docs/{docId}/restore)."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document)
        _confirm(confirm, f"delete document '{doc.get('name')}'")
        await api.delete("resources", rid, "docs", doc["id"])
        return f"Deleted document '{doc.get('name')}' (id {doc['id']}) of resource {rid}."
