"""Read-only discovery tools."""

from typing import Annotated, Any

from pydantic import Field

from ..errors import LegendKeeperError
from ..markdown import RenderContext, properties_to_markdown
from ..service import ResourceIndex
from .common import READ, ProjectArg, ResourceArg, ToolContext, dump

MAX_TREE_LINES = 400


def _icon(item: dict[str, Any]) -> str:
    return f" [{item['iconGlyph']}]" if item.get("iconGlyph") else ""


def _tree_line(item: dict[str, Any], depth: int, index: ResourceIndex, show_docs: bool) -> str:
    tags = f" #{' #'.join(item['tags'])}" if item.get("tags") else ""
    hidden = " (hidden)" if item.get("isHidden") else ""
    kids = len(index.kids(item["id"]))
    more = f" (+{kids} children)" if kids else ""
    docs = ""
    if show_docs:
        # Only call out maps, timelines and boards; pages and empty (blank) documents are the norm.
        types = [
            str(d.get("type")) for d in item.get("documents") or [] if d.get("type") not in ("page", "blank")
        ]
        docs = f" {{{', '.join(types)}}}" if types else ""
    return f"{'  ' * depth}- {item.get('name')} (id {item['id']}){_icon(item)}{docs}{tags}{hidden}{more}"


def register(t: ToolContext) -> None:
    registry = t.registry

    @t.tool(annotations=READ)
    async def list_projects() -> str:
        """List the LegendKeeper projects (worlds) this server is configured for, with the default project and
        whether writes are enabled. Each API key belongs to exactly one project."""
        out = []
        for name in registry.names:
            entry: dict[str, Any] = {"name": name, "default": name == t.settings.default_project}
            try:
                info = await registry.session(name).info()
                entry.update(
                    {"projectId": info.get("id"), "title": info.get("name"), "isPublic": info.get("isPublic")}
                )
            except LegendKeeperError as exc:
                entry["error"] = f"{exc.code}: {exc.message}"
            out.append(entry)
        return dump({"readOnly": t.settings.read_only, "projects": out})

    @t.tool(annotations=READ)
    async def browse_tree(
        parent: Annotated[
            str | None, Field(description="Resource id or name to list below. Omit for the top of the world.")
        ] = None,
        depth: Annotated[int, Field(ge=1, le=10, description="How many levels to expand.")] = 2,
        show_document_types: Annotated[
            bool, Field(description="Show document types for resources that are not a single page.")
        ] = True,
        refresh: Annotated[bool, Field(description="Bypass the 60-second resource cache.")] = False,
        project: ProjectArg = None,
    ) -> str:
        """Show the resource tree (the world's wiki hierarchy) as an indented outline with ids, icons, tags and
        child counts. Start here to see how a world is organized."""
        session = registry.session(project)
        index = await session.index(refresh=refresh)
        root_id = await session.resolve_resource(parent) if parent else None
        lines: list[str] = []
        if root_id:
            lines.append(f"{index.name_of(root_id)} (id {root_id}) — path: {index.path_text(root_id)}")
        start = index.kids(root_id) if root_id else index.roots()
        truncated = False

        def walk(items: list[dict[str, Any]], level: int) -> None:
            nonlocal truncated
            for item in items:
                if len(lines) >= MAX_TREE_LINES:
                    truncated = True
                    return
                lines.append(_tree_line(item, level, index, show_document_types))
                if level + 1 < depth:
                    walk(index.kids(item["id"]), level + 1)

        walk(start, 0)
        total = len(index.items) - len(index.templates)
        if not start:
            lines.append("(no resources)")
        if truncated:
            lines.append(f"… truncated at {MAX_TREE_LINES} lines; browse a sub-tree with `parent`.")
        lines.append(f"({total} resources in the world; templates excluded — see list_templates.)")
        return "\n".join(lines)

    @t.tool(annotations=READ)
    async def search(
        query: Annotated[
            str, Field(min_length=1, description="Words to find in names, aliases and page text.")
        ],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        project: ProjectArg = None,
    ) -> str:
        """Full-text search across resource names, aliases and document content (best match first). Returns ids,
        paths, tags and the matching passage."""
        api = await registry.session(project).api()
        result = await api.get("search", params={"q": query, "limit": limit})
        hits = (result or {}).get("data") or []
        if not hits:
            return f"No results for '{query}'."
        lines = []
        for hit in hits:
            r = hit.get("resource") or {}
            path = " / ".join(p.get("name", "") for p in r.get("path") or []) or "(root)"
            tags = f" #{' #'.join(r['tags'])}" if r.get("tags") else ""
            line = f"- {r.get('name')} (id {r.get('id')}) in {path}{tags}"
            doc = hit.get("document")
            if doc:
                line += f"\n    match in document '{doc.get('name')}' (id {doc.get('id')})"
                for h in (doc.get("highlights") or [])[:2]:
                    text = " ".join(str(h.get("segment") or "").split())
                    if text:
                        line += f": …{text[:240]}…"
            lines.append(line)
        return "\n".join(lines)

    @t.tool(annotations=READ)
    async def get_resource(resource: ResourceArg, project: ProjectArg = None) -> str:
        """Get one resource's details: path, tags, aliases, icon, banner, documents, sidebar properties (facts)
        and children. Use read_document to read a document's content."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        data = await api.get("resources", rid)
        index = await session.index()
        docs = [
            {k: d.get(k) for k in ("id", "name", "type", "isHidden", "calendarId") if d.get(k) is not None}
            for d in data.get("documents") or []
        ]
        summary: dict[str, Any] = {
            "id": data.get("id"),
            "name": data.get("name"),
            "path": index.path_text(rid),
            "parentId": data.get("parentId"),
            "tags": data.get("tags"),
            "aliases": data.get("aliases"),
            "icon": {
                k: data.get(k) for k in ("iconGlyph", "iconColor", "iconShape", "iconSize") if data.get(k)
            },
            "isHidden": data.get("isHidden"),
            "isLocked": data.get("isLocked"),
            "isTemplate": rid in index.templates,
            "banner": data.get("banner"),
            "documents": docs,
            "children": [{"id": c["id"], "name": c.get("name")} for c in index.kids(rid)],
            "createdAt": data.get("createdAt"),
            "updatedAt": data.get("updatedAt"),
        }
        text = dump({k: v for k, v in summary.items() if v not in (None, {}, [])})
        props = data.get("properties") or []
        if props:
            rendered = properties_to_markdown(props, RenderContext(name_of=index.name_of))
            ids = ", ".join(
                f"{p.get('title') or p.get('kind')} (id {p.get('id')}, {p.get('kind')})" for p in props
            )
            text += f"\n\nSidebar properties: {ids}\n{rendered}"
        return text

    @t.tool(annotations=READ)
    async def list_tags(project: ProjectArg = None) -> str:
        """List every tag used in the world with how many resources carry it. Check this before tagging so you
        reuse existing tags with the exact same spelling."""
        session = registry.session(project)
        api = await session.api()
        try:
            data = (await api.get("tags") or {}).get("data") or []
            pairs = [(d["tag"], d["count"]) for d in data]
        except LegendKeeperError as exc:
            if exc.status != 403:
                raise
            counts: dict[str, int] = {}
            for item in (await session.index(refresh=True)).items:
                for tag in item.get("tags") or []:
                    counts[tag] = counts.get(tag, 0) + 1
            pairs = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if not pairs:
            return "No tags in this world yet."
        return "\n".join(f"- {tag} ({count})" for tag, count in pairs)

    @t.tool(annotations=READ)
    async def list_templates(project: ProjectArg = None) -> str:
        """List resource templates (id, name, parent). Use create_from_template to instantiate one."""
        api = await registry.session(project).api()
        items = await api.list_all("templates")
        if not items:
            return "No templates in this world."
        return "\n".join(
            f"- {i.get('name')} (id {i['id']}){_icon(i)}"
            + (f" — under {i.get('parentId')}" if i.get("parentId") not in (None, "templates") else "")
            for i in items
        )

    @t.tool(annotations=READ)
    async def list_calendars(project: ProjectArg = None) -> str:
        """List the calendars timelines can use (built-in ones like gregorian, harptos, exandria, plus custom
        calendars): id, name, months and eras."""
        api = await registry.session(project).api()
        items = (await api.get("calendars") or {}).get("data") or []
        lines = []
        for c in items:
            months = ", ".join(m.get("name", "") for m in c.get("months") or [])
            eras = ", ".join(e.get("name", "") for e in c.get("positiveEras") or [])
            lines.append(f"- {c.get('name')} (id {c.get('id')}): {len(c.get('months') or [])} months [{months}]"
                         + (f"; eras: {eras}" if eras else ""))  # fmt: skip
        return "\n".join(lines) or "No calendars."

    @t.tool(annotations=READ)
    async def list_members(project: ProjectArg = None) -> str:
        """List the members of the world and their roles (emails are only visible to ADMIN keys)."""
        api = await registry.session(project).api()
        members = (await api.get("members") or {}).get("data") or []
        return (
            "\n".join(
                f"- {m.get('name') or '(no name)'} — {m.get('role')} (user id {m.get('id')})"
                + (f" <{m['email']}>" if m.get("email") else "")
                for m in members
            )
            or "No members."
        )

    @t.tool(annotations=READ)
    async def list_assets(
        folder_id: Annotated[
            str | None, Field(description="Asset folder id; omit for the library root.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 100,
        project: ProjectArg = None,
    ) -> str:
        """List the world's asset library (uploaded images, maps, folders) with ids and URLs. Image URLs can be
        used in page images, banners, map images and events."""
        api = await registry.session(project).api()
        params = {"folderId": folder_id} if folder_id else None
        items = await api.list_all("assets", params=params, max_items=limit)
        if not items:
            return "No assets here."
        return "\n".join(
            f"- {a.get('type')} '{a.get('name')}' (id {a['id']})"
            + (f" {int(a['width'])}×{int(a['height'])}" if a.get("width") and a.get("height") else "")
            + (f" {a['url']}" if a.get("url") else "")
            for a in items
        )
