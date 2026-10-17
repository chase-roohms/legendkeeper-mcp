"""Document helpers: choosing a document, reading it in an LLM-friendly form, and writing Markdown."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .client import ProjectClient
from .errors import InputError, LegendKeeperError
from .markdown import (
    InlineContext,
    RenderContext,
    blocks_to_markdown,
    drop_keep_sentinels,
    find_section,
    heading_title,
    list_headings,
    materialize,
    parse_markdown,
)
from .markdown.render import OpaqueMode
from .service import ProjectSession, ResourceIndex

Block = dict[str, Any]
WriteMode = Literal["replace", "append", "prepend", "replace_section", "append_to_section"]
DOC_TYPES = ("page", "map", "time", "board", "blank")


async def list_documents(api: ProjectClient, resource_id: str) -> list[dict[str, Any]]:
    page = await api.get("resources", resource_id, "docs")
    return list((page or {}).get("data") or [])


async def pick_document(
    api: ProjectClient,
    resource_id: str,
    ref: str | None,
    kinds: Sequence[str] = (),
) -> dict[str, Any]:
    """Find a document by id/name, or the first document of the preferred ``kinds``."""
    docs = await list_documents(api, resource_id)
    if not docs:
        raise InputError("This resource has no documents. Create one with create_document.")
    listing = ", ".join(f"'{d.get('name')}' ({d.get('type')}, id {d['id']})" for d in docs)
    if ref:
        wanted = ref.strip()
        for d in docs:
            if d["id"] == wanted:
                return d
        named = [d for d in docs if str(d.get("name", "")).casefold() == wanted.casefold()]
        if len(named) == 1:
            return named[0]
        raise InputError(f"No single document '{ref}' on this resource. Documents: {listing}.")
    for kind in kinds:
        for d in docs:
            if d.get("type") == kind:
                return d
    if kinds:
        raise InputError(
            f"This resource has no {' or '.join(kinds)} document. Documents: {listing}. "
            "Pass `document`, or add one with create_document."
        )
    return docs[0]


# --------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------


def _header(
    resource: dict[str, Any] | None, doc: dict[str, Any], index: ResourceIndex, extra: str = ""
) -> str:
    rid = resource["id"] if resource else ""
    name = resource.get("name") if resource else index.name_of(rid)
    lines = [
        f'Resource "{name}" (id {rid}) › document "{doc.get("name")}" (id {doc["id"]}, type {doc.get("type")})'
        + (f" · {extra}" if extra else ""),
    ]
    if rid in index.by_id:
        lines.append(f"Path: {index.path_text(rid)}")
    return "\n".join(lines)


async def read_page(
    api: ProjectClient,
    index: ResourceIndex,
    resource_id: str,
    doc: dict[str, Any],
    *,
    fmt: Literal["markdown", "text", "json"] = "markdown",
    opaque: OpaqueMode = "keep",
) -> str | dict[str, Any]:
    data = await api.get("resources", resource_id, "docs", doc["id"], "blocks")
    if fmt == "json":
        return dict(data)
    header = _header(index.by_id.get(resource_id), doc, index, f"version {data.get('version')}")
    if fmt == "text":
        body = str(data.get("text") or "")
    else:
        body = blocks_to_markdown(
            data.get("blocks") or [], RenderContext(name_of=index.name_of, opaque=opaque)
        )
    return f"{header}\n---\n{body}" if body.strip() else f"{header}\n---\n(empty page)"


def _link_label(uri: str | None, index: ResourceIndex) -> str:
    if not uri:
        return ""
    rid = uri.removeprefix("lk://resources/").split("/")[0] if uri.startswith("lk://resources/") else None
    name = index.name_of(rid) if rid else None
    return f" → {name} ({uri})" if name else f" → {uri}"


def _fmt_pos(pos: Any) -> str:
    if isinstance(pos, list | tuple) and len(pos) == 2:
        return f"[{_num(pos[0])}, {_num(pos[1])}]"
    return "-"


def _num(value: Any) -> str:
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


async def read_map(
    api: ProjectClient, index: ResourceIndex, resource_id: str, doc: dict[str, Any], *, fmt: str = "markdown"
) -> str | dict[str, Any]:
    full = await api.get("resources", resource_id, "docs", doc["id"])
    features = (await api.get("resources", resource_id, "docs", doc["id"], "features") or {}).get(
        "data"
    ) or []
    if fmt == "json":
        return {"document": full, "features": features}
    lines = [_header(index.by_id.get(resource_id), doc, index)]
    image = (full or {}).get("mapImage")
    if image:
        lines.append(
            f"Map image: {image.get('width')}×{image.get('height')} px, bounds {image.get('bounds')} "
            "([[south, west], [north, east]]; positions are [lat, lng], top-left is [0, 0], "
            "lat goes negative downward)."
        )
    else:
        lines.append("Map image: none yet (set one with update_document map_image_asset_id).")
    lines.append(f"Features ({len(features)}):")
    folders = {f["id"]: f.get("name") for f in features if f.get("type") == "folder"}
    for f in sorted(features, key=lambda f: (f.get("parentId") or "", f.get("rank") or "")):
        where = f" in folder '{folders[f['parentId']]}'" if f.get("parentId") in folders else ""
        geometry = ""
        if f.get("pos") is not None:
            geometry = f" at {_fmt_pos(f['pos'])}"
        elif f.get("polygon") or f.get("polygons"):
            count = len(f.get("polygon") or []) or sum(len(p) for p in f.get("polygons") or [])
            geometry = f" ({count} vertices)"
        elif f.get("polyline"):
            geometry = f" ({len(f['polyline'])} points)"
        hidden = " [hidden]" if f.get("isHidden") else ""
        icon = f" icon={f['iconGlyph']}" if f.get("iconGlyph") else ""
        lines.append(
            f'- {f.get("type", "feature")} "{f.get("name") or ""}" (id {f["id"]}){geometry}{icon}{where}'
            f"{_link_label(f.get('uri'), index)}{hidden}"
        )
    return "\n".join(lines)


async def read_timeline(
    api: ProjectClient, index: ResourceIndex, resource_id: str, doc: dict[str, Any], *, fmt: str = "markdown"
) -> str | dict[str, Any]:
    full = await api.get("resources", resource_id, "docs", doc["id"])
    lanes = (await api.get("resources", resource_id, "docs", doc["id"], "lanes") or {}).get("data") or []
    events = await api.list_all("resources", resource_id, "docs", doc["id"], "events")
    if fmt == "json":
        return {"document": full, "lanes": lanes, "events": events}
    lines = [
        _header(index.by_id.get(resource_id), doc, index, f"calendar {full.get('calendarId') or 'none'}")
    ]
    if full.get("sources"):
        lines.append(f"Chains timelines: {full['sources']}")
    lane_names = {lane["id"]: lane.get("name") for lane in lanes}
    lines.append(
        "Lanes: " + (", ".join(f"'{lane.get('name')}' (id {lane['id']})" for lane in lanes) or "none")
    )
    lines.append(f"Events ({len(events)}):")
    for e in sorted(events, key=lambda e: (e.get("start") or 0, e.get("rank") or "")):
        when = e.get("formattedStart") or _num(e.get("start"))
        if e.get("formattedEnd") or e.get("end") is not None:
            when += f" – {e.get('formattedEnd') or _num(e.get('end'))}"
        hidden = " [hidden]" if e.get("isHidden") else ""
        kind = "" if e.get("type") == "event" else f" ({e.get('type')})"
        lines.append(
            f"- {when}: {e.get('name') or '(unnamed)'}{kind} [lane {lane_names.get(e.get('laneId'), e.get('laneId'))}]"
            f" (id {e['id']}){_link_label(e.get('uri'), index)}{hidden}"
        )
    return "\n".join(lines)


def _rich_text(node: Any) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        parts = [_rich_text(c) for c in node.get("content") or []]
        sep = "\n" if node.get("type") in ("doc",) else ""
        return sep.join(p for p in parts if p)
    return ""


async def read_board(
    api: ProjectClient, index: ResourceIndex, resource_id: str, doc: dict[str, Any], *, fmt: str = "markdown"
) -> str | dict[str, Any]:
    content = await api.get("resources", resource_id, "docs", doc["id"], "board")
    if fmt == "json":
        return dict(content)
    shapes = content.get("shapes") or []
    lines = [_header(index.by_id.get(resource_id), doc, index, f"version {content.get('version')}")]
    lines.append(f"Pages: {len(content.get('pages') or [])}, shapes: {len(shapes)}. Text on the board:")
    for s in shapes:
        props = s.get("props") or {}
        text = props.get("text") or _rich_text(props.get("richText")) or props.get("name") or ""
        if text.strip():
            lines.append(f"- {s.get('type')} ({s.get('id')}): {' '.join(str(text).split())[:300]}")
    lines.append("(Use format='json' for raw tldraw records; edit boards with api_request.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Writing Markdown
# --------------------------------------------------------------------------------------------


@dataclass
class WriteOutcome:
    version: str | None
    blocks: int
    reused: int = 0
    kept: int = 0
    created: int = 0
    warnings: list[str] = field(default_factory=list)
    note: str = ""


def inline_context(index: ResourceIndex) -> InlineContext:
    return InlineContext(resolve_name=index.resolve_wiki, name_of=index.name_of)


def markdown_to_new_blocks(markdown: str, index: ResourceIndex) -> tuple[list[Block], list[str]]:
    """Markdown for a brand-new page (no existing blocks to keep or reuse)."""
    parsed = parse_markdown(markdown, inline_context(index))
    blocks, warnings = drop_keep_sentinels(parsed.blocks)
    return blocks, parsed.warnings + warnings


async def write_markdown(
    session: ProjectSession,
    resource_id: str,
    doc: dict[str, Any],
    markdown: str,
    mode: WriteMode,
    section: str | None = None,
) -> WriteOutcome:
    api = await session.api()
    index = await session.index()
    if doc.get("type") not in ("page", "blank"):
        raise InputError(
            f"Document '{doc.get('name')}' is a {doc.get('type')} document; write_page only edits page documents."
        )
    if mode in ("replace_section", "append_to_section") and not (section and section.strip()):
        raise InputError(f"mode='{mode}' needs `section` (the heading text of the section).")

    parsed = parse_markdown(markdown, inline_context(index))
    render_ctx = RenderContext(name_of=index.name_of)

    for attempt in range(2):
        current = await api.get("resources", resource_id, "docs", doc["id"], "blocks")
        existing: list[Block] = list(current.get("blocks") or [])
        mat = materialize(parsed.blocks, existing, render_ctx, reuse_unchanged=True)
        final, note = _combine(existing, mat.blocks, mode, section)
        try:
            result = await api.put(
                "resources", resource_id, "docs", doc["id"], "blocks",
                json={"blocks": final},
                if_match=str(current.get("version")) if current.get("version") is not None else None,
            )  # fmt: skip
        except LegendKeeperError as exc:
            if exc.code == "VERSION_MISMATCH" and attempt == 0:
                continue
            raise
        return WriteOutcome(
            version=(result or {}).get("version"),
            blocks=len(final),
            reused=mat.reused,
            kept=mat.kept,
            created=mat.created,
            warnings=parsed.warnings + mat.warnings,
            note=note,
        )
    raise InputError("The page kept changing while writing; try again.")  # pragma: no cover


def _combine(
    existing: list[Block], new: list[Block], mode: WriteMode, section: str | None
) -> tuple[list[Block], str]:
    if mode == "replace":
        return new, ""
    if mode == "append":
        return existing + new, ""
    if mode == "prepend":
        return new + existing, ""
    assert section is not None
    found = find_section(existing, section)
    if found is None:
        heading: Block = {
            "kind": "heading",
            "level": 2,
            "content": [{"text": section.strip().lstrip("#").strip()}],
        }
        starts_with_heading = bool(new) and new[0].get("kind") == "heading" and _same_title(new[0], section)
        body = new if starts_with_heading else [heading, *new]
        available = ", ".join(f"'{t}'" for _, _, t in list_headings(existing)) or "none"
        return (
            existing + body,
            f"Section '{section}' did not exist and was added at the end (headings: {available}).",
        )
    if mode == "append_to_section":
        return existing[: found.end] + new + existing[found.end :], ""
    # replace_section: keep the heading unless the new Markdown brings its own.
    if new and new[0].get("kind") == "heading" and _same_title(new[0], section):
        return existing[: found.start] + new + existing[found.end :], ""
    return existing[: found.start + 1] + new + existing[found.end :], ""


def _same_title(block: Block, title: str) -> bool:
    return heading_title(block).casefold() == " ".join(title.strip().lstrip("#").split()).casefold()


# --------------------------------------------------------------------------------------------
# Maps
# --------------------------------------------------------------------------------------------


def pixel_to_pos(image: dict[str, Any], x: float, y: float) -> list[float]:
    """Convert image pixel coordinates (from the top-left corner) into a map ``[lat, lng]``."""
    bounds = image.get("bounds")
    width, height = image.get("width"), image.get("height")
    if bounds and width and height:
        (south, west), (north, east) = bounds
        return [north - y * (north - south) / height, west + x * (east - west) / width]
    if width and height:
        z = math.ceil(math.log2(max(width, height) / 256))
        return [-y / 2**z, x / 2**z]
    raise InputError(
        "The map has no image, so pixel coordinates cannot be converted. Pass `position` instead."
    )
