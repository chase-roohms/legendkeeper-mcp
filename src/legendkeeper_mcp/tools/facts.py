"""Structured facts: key-value rows in a page infobox or the resource sidebar."""

from typing import Annotated, Any, Literal

from pydantic import Field

from ..client import ProjectClient
from ..documents import inline_context, pick_document
from ..errors import InputError
from ..markdown import iter_blocks, parse_inline
from ..service import ResourceIndex
from .common import UPDATE, DocumentArg, ProjectArg, ResourceArg, ToolContext


def _find_key_value(props: list[dict[str, Any]], title: str | None) -> dict[str, Any] | None:
    candidates = [p for p in props if p.get("kind") == "keyValue"]
    if title:
        wanted = title.strip().casefold()
        return next((p for p in candidates if str(p.get("title") or "").strip().casefold() == wanted), None)
    return candidates[0] if candidates else None


def _rows_payload(facts: dict[str, str | None], index: ResourceIndex) -> list[dict[str, Any]]:
    return [
        {"key": key, "content": parse_inline(value, inline_context(index))}
        for key, value in facts.items()
        if value is not None
    ]


async def _apply_rows(
    api: ProjectClient,
    base: tuple[str, ...],
    existing_rows: list[dict[str, Any]],
    facts: dict[str, str | None],
    index: ResourceIndex,
) -> list[str]:
    changes: list[str] = []
    by_key = {str(r.get("key", "")).strip().casefold(): r for r in existing_rows}
    for key, value in facts.items():
        row = by_key.get(key.strip().casefold())
        if value is None:
            if row is not None and row.get("id"):
                await api.delete(*base, "rows", row["id"])
                changes.append(f"removed {key}")
            continue
        content = parse_inline(value, inline_context(index))
        if row is not None and row.get("id"):
            if row.get("content") == content:
                continue
            await api.patch(*base, "rows", row["id"], json={"content": content})
            changes.append(f"updated {key}")
        else:
            await api.post(*base, "rows", json={"key": key, "content": content})
            changes.append(f"added {key}")
    return changes


def register(t: ToolContext) -> None:
    registry = t.registry

    @t.tool(annotations=UPDATE, write=True)
    async def set_facts(
        resource: ResourceArg,
        facts: Annotated[
            dict[str, str | None],
            Field(
                description="Fact name -> value. Values accept inline Markdown and [[Name]] links. Use null to "
                "remove a fact. Existing facts with the same name (case-insensitive) are updated in place."
            ),
        ],
        target: Annotated[
            Literal["infobox", "sidebar"],
            Field(
                description="infobox: the box at the top of the page (created if missing). sidebar: properties."
            ),
        ] = "infobox",
        title: Annotated[
            str | None,
            Field(
                description="Title of the key-value group (default: the first one, or 'Facts' when creating)."
            ),
        ] = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Set at-a-glance facts (e.g. Race, Age, Ruler, Population) as key-value rows in the page's infobox or
        the resource's sidebar. Creates the infobox / property group when it does not exist yet."""
        if not facts:
            raise InputError("`facts` is empty.")
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        index = await session.index()

        if target == "sidebar":
            props = (await api.get("resources", rid, "properties") or {}).get("data") or []
            group = _find_key_value(props, title)
            if group is None:
                rows = _rows_payload(facts, index)
                body = {"kind": "keyValue", "title": title or "Facts", "rows": rows}
                created = await api.post("resources", rid, "properties", json=body)
                return f"Created sidebar property '{body['title']}' (id {created.get('id')}) with {len(rows)} fact(s)."
            changes = await _apply_rows(api, ("resources", rid, "properties", group["id"]), group.get("rows") or [],
                                        facts, index)  # fmt: skip
            return f"Sidebar '{group.get('title') or 'Facts'}': " + (", ".join(changes) or "no changes") + "."

        doc = await pick_document(api, rid, document, ("page", "blank"))
        if doc.get("type") not in ("page", "blank"):
            raise InputError(f"Document '{doc.get('name')}' is a {doc.get('type')}; infoboxes live on pages.")
        data = await api.get("resources", rid, "docs", doc["id"], "blocks")
        blocks = data.get("blocks") or []
        if blocks and any(not b.get("id") for b in iter_blocks(blocks)):
            await api.post("resources", rid, "docs", doc["id"], "block-ids")
            data = await api.get("resources", rid, "docs", doc["id"], "blocks")
            blocks = data.get("blocks") or []
        infobox = next((b for b in iter_blocks(blocks) if b.get("kind") == "infobox"), None)
        base = ("resources", rid, "docs", doc["id"], "blocks")

        if infobox is None:
            rows = _rows_payload(facts, index)
            block = {
                "kind": "infobox",
                "rows": [{"kind": "keyValue", "title": title or "Facts", "rows": rows}],
            }
            insert: dict[str, Any] = {"block": block}
            if blocks:
                insert["before"] = blocks[0]["id"]
            created = await api.post(*base, json=insert, if_match=str(data.get("version")))
            return f"Created an infobox (block id {created.get('id')}) at the top of the page with {len(rows)} fact(s)."

        group = _find_key_value(infobox.get("rows") or [], title)
        if group is None:
            rows = _rows_payload(facts, index)
            group_body = {"kind": "keyValue", "title": title or "Facts", "rows": rows}
            await api.post(*base, infobox["id"], "properties", json=group_body)
            return f"Added a '{group_body['title']}' group with {len(rows)} fact(s) to the existing infobox."
        changes = await _apply_rows(
            api, (*base, infobox["id"], "properties", group["id"]), group.get("rows") or [], facts, index
        )
        return f"Infobox '{group.get('title') or 'Facts'}': " + (", ".join(changes) or "no changes") + "."
