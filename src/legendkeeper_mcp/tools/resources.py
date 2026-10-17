"""Tools that create, change, move and delete resources."""

import secrets
import string
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..client import ProjectClient
from ..documents import markdown_to_new_blocks
from ..errors import InputError
from ..service import TEMPLATES_ROOT
from .common import (
    DESTRUCTIVE,
    UPDATE,
    WRITE,
    ProjectArg,
    ResourceArg,
    ToolContext,
    compact,
    dump,
    require_confirm,
)

IconShape = Literal["pin", "diamond", "flag", "marker", "shield", "circle", "banner", "arch", "none"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", description="#RRGGBB color")]
_ALPHABET = string.ascii_lowercase + string.digits


def new_id() -> str:
    """A client-chosen id in LegendKeeper's accepted format (24-character cuid2 shape)."""
    return secrets.choice(string.ascii_lowercase) + "".join(secrets.choice(_ALPHABET) for _ in range(23))


class NewResource(BaseModel):
    """One resource in a create_resources batch."""

    name: str = Field(min_length=1, max_length=500)
    key: str | None = Field(
        None, description="Your local label for this item, so later items can use parent_key."
    )
    parent: str | None = Field(None, description="Existing parent resource id or name.")
    parent_key: str | None = Field(None, description="`key` of an earlier item in this batch to nest under.")
    content: str | None = Field(None, description="Markdown for the resource's page.")
    tags: list[str] | None = None
    aliases: list[str] | None = None
    icon_glyph: str | None = Field(None, description="Font Awesome icon name, e.g. 'crown'.")
    icon_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_hidden: bool | None = None


async def resolve_template(api: ProjectClient, ref: str) -> str:
    """Templates may not appear in the resource list, so look them up through listTemplates."""
    ref = ref.strip()
    templates = await api.list_all("templates")
    if any(t["id"] == ref for t in templates):
        return ref
    wanted = " ".join(ref.split()).casefold()
    matches = [t for t in templates if " ".join(str(t.get("name", "")).split()).casefold() == wanted]
    if len(matches) == 1:
        return str(matches[0]["id"])
    names = ", ".join(f"'{t.get('name')}' (id {t['id']})" for t in templates[:30]) or "none"
    if matches:
        raise InputError(f"Several templates are named '{ref}'; pass the id. Templates: {names}.")
    raise InputError(f"No template '{ref}'. Templates: {names}.")


def _summary(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": resource.get("id"),
        "name": resource.get("name"),
        "parentId": resource.get("parentId"),
        "tags": resource.get("tags"),
        "documents": [
            {"id": d.get("id"), "name": d.get("name"), "type": d.get("type")}
            for d in resource.get("documents") or []
        ],
    }


def register(t: ToolContext) -> None:
    registry = t.registry

    async def parent_id(session: Any, parent: str | None) -> str | None:
        if parent is None or not parent.strip() or parent.strip().lower() in ("root", "/", "(root)"):
            return None
        if parent.strip().lower() == TEMPLATES_ROOT:
            return TEMPLATES_ROOT
        return str(await session.resolve_resource(parent))

    @t.tool(annotations=WRITE, write=True)
    async def create_resource(
        name: Annotated[str, Field(min_length=1, max_length=500)],
        parent: Annotated[
            str | None, Field(description="Parent resource id or name. Omit (or 'root') for the top level.")
        ] = None,
        content: Annotated[
            str | None, Field(description="Markdown for the new page. [[Name]] links become mentions.")
        ] = None,
        document_type: Annotated[
            Literal["page", "map", "time", "board"] | None,
            Field(description="Type of the first document when there is no `content` (default: blank)."),
        ] = None,
        document_name: Annotated[
            str | None, Field(max_length=500, description="First document's name.")
        ] = None,
        calendar_id: Annotated[str | None, Field(description="Calendar for a `time` document.")] = None,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
        icon_glyph: Annotated[str | None, Field(description="Font Awesome icon name, e.g. 'dragon'.")] = None,
        icon_color: HexColor | None = None,
        icon_shape: IconShape | None = None,
        is_hidden: Annotated[bool | None, Field(description="Create hidden (ADMIN keys only).")] = None,
        after: Annotated[
            str | None, Field(description="Sibling resource id to place this one after.")
        ] = None,
        project: ProjectArg = None,
    ) -> str:
        """Create a resource (wiki article, location, character…), optionally with page content in Markdown.
        Reuse existing tags (list_tags) and give it an icon. Returns the new id and its documents."""
        session = registry.session(project)
        api = await session.api()
        body: dict[str, Any] = compact(
            {
                "name": name,
                "parentId": await parent_id(session, parent),
                "tags": tags,
                "aliases": aliases,
                "iconGlyph": icon_glyph,
                "iconColor": icon_color,
                "iconShape": icon_shape,
                "isHidden": is_hidden,
                "after": after,
            }
        )
        warnings: list[str] = []
        if content is not None and content.strip():
            if document_type not in (None, "page"):
                raise InputError("`content` creates a page; omit document_type or set it to 'page'.")
            blocks, warnings = markdown_to_new_blocks(content, await session.index())
            body["documents"] = [{"type": "page", "name": document_name or "Main", "blocks": blocks}]
        elif document_type:
            body["documents"] = [
                compact({"type": document_type, "name": document_name or "Main", "calendarId": calendar_id})
            ]
        created = await api.post("resources", json=body)
        session.invalidate()
        out = dump(_summary(created))
        return out + "".join(f"\nWarning: {w}" for w in warnings)

    @t.tool(annotations=WRITE, write=True)
    async def create_resources(
        resources: Annotated[
            list[NewResource],
            Field(min_length=1, max_length=100, description="1-100 resources, created in order."),
        ],
        project: ProjectArg = None,
    ) -> str:
        """Create up to 100 resources in one atomic request: ideal for scaffolding a region, a cast of NPCs or a
        whole hierarchy. Give items a `key` and nest later items under them with `parent_key`."""
        session = registry.session(project)
        api = await session.api()
        index = await session.index()
        keys: dict[str, str] = {}
        payload: list[dict[str, Any]] = []
        warnings: list[str] = []
        ids = [new_id() for _ in resources]
        # [[Links]] may point at any item of the batch, including later ones (LegendKeeper accepts that).
        batch_names: dict[str, tuple[str, str]] = {}
        for rid, item in zip(ids, resources, strict=True):
            for label in [item.name, *(item.aliases or [])]:
                batch_names.setdefault(" ".join(label.split()).casefold(), (rid, item.name))
        for i, (rid, item) in enumerate(zip(ids, resources, strict=True)):
            if item.key:
                if item.key in keys:
                    raise InputError(f"Duplicate key '{item.key}' (item {i}).")
                keys[item.key] = rid
            if item.parent_key and item.parent:
                raise InputError(f"Item {i} has both parent and parent_key.")
            if item.parent_key:
                if item.parent_key not in keys:
                    raise InputError(
                        f"Item {i}: parent_key '{item.parent_key}' must be the key of an earlier item."
                    )
                pid: str | None = keys[item.parent_key]
            else:
                pid = await parent_id(session, item.parent)
            body: dict[str, Any] = compact(
                {
                    "id": rid,
                    "name": item.name,
                    "parentId": pid,
                    "tags": item.tags,
                    "aliases": item.aliases,
                    "iconGlyph": item.icon_glyph,
                    "iconColor": item.icon_color,
                    "isHidden": item.is_hidden,
                }
            )
            if item.content and item.content.strip():
                blocks, item_warnings = markdown_to_new_blocks(item.content, index, batch_names)
                warnings.extend(f"{item.name}: {w}" for w in item_warnings)
                body["documents"] = [{"type": "page", "name": "Main", "blocks": blocks}]
            payload.append(body)
        result = await api.post("resources", "batch", json={"resources": payload})
        session.invalidate()
        created = (result or {}).get("resources") or []
        lines = [f"Created {len(created)} resources:"]
        lines += [
            f"- {r.get('name')} (id {r.get('id')}, parent {r.get('parentId') or 'root'})" for r in created
        ]
        lines += [f"Warning: {w}" for w in warnings]
        return "\n".join(lines)

    @t.tool(annotations=UPDATE, write=True)
    async def update_resource(
        resource: ResourceArg,
        name: Annotated[str | None, Field(max_length=500)] = None,
        tags: Annotated[list[str] | None, Field(description="Replace all tags with this list.")] = None,
        add_tags: Annotated[
            list[str] | None, Field(description="Tags to add (kept if already present).")
        ] = None,
        remove_tags: Annotated[list[str] | None, Field(description="Tags to remove.")] = None,
        aliases: Annotated[list[str] | None, Field(description="Replace all aliases.")] = None,
        add_aliases: list[str] | None = None,
        icon_glyph: Annotated[str | None, Field(description="Font Awesome icon name.")] = None,
        icon_color: HexColor | None = None,
        icon_shape: IconShape | None = None,
        is_hidden: Annotated[bool | None, Field(description="Hide/show (ADMIN keys only).")] = None,
        is_locked: bool | None = None,
        banner_url: Annotated[
            str | None,
            Field(description="Banner image URL (must be an uploaded asset URL, see upload_image)."),
        ] = None,
        banner_enabled: bool | None = None,
        banner_y_position: Annotated[
            float | None, Field(ge=0, le=100, description="Banner focus, 0-100.")
        ] = None,
        project: ProjectArg = None,
    ) -> str:
        """Change a resource's name, tags, aliases, icon, visibility, lock or banner. Content is edited with
        write_page; facts with set_facts; position in the tree with move_resource."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        body: dict[str, Any] = compact(
            {
                "name": name,
                "iconGlyph": icon_glyph,
                "iconColor": icon_color,
                "iconShape": icon_shape,
                "isHidden": is_hidden,
                "isLocked": is_locked,
            }
        )
        touches_banner = banner_url is not None or banner_enabled is not None or banner_y_position is not None
        current: dict[str, Any] | None = None
        if add_tags or remove_tags or add_aliases or touches_banner:
            current = await api.get("resources", rid)
        if tags is not None or add_tags or remove_tags:
            new_tags = list(tags if tags is not None else (current or {}).get("tags") or [])
            for tag in add_tags or []:
                if tag not in new_tags:
                    new_tags.append(tag)
            removals = {r.casefold() for r in remove_tags or []}
            body["tags"] = [tag for tag in new_tags if tag.casefold() not in removals]
        if aliases is not None or add_aliases:
            new_aliases = list(aliases if aliases is not None else (current or {}).get("aliases") or [])
            new_aliases += [a for a in add_aliases or [] if a not in new_aliases]
            body["aliases"] = new_aliases
        if touches_banner:
            banner = dict((current or {}).get("banner") or {})
            if banner_url is not None:
                banner["url"] = banner_url
                banner.setdefault("enabled", True)
            if banner_enabled is not None:
                banner["enabled"] = banner_enabled
            if banner_y_position is not None:
                banner["yPosition"] = banner_y_position
            if not banner.get("url"):
                raise InputError("The resource has no banner yet; pass banner_url (an uploaded image URL).")
            banner.setdefault("enabled", True)
            banner.setdefault("yPosition", 50)
            body["banner"] = {k: banner[k] for k in ("enabled", "url", "yPosition")}
        if not body:
            raise InputError("Nothing to update.")
        updated = await api.patch("resources", rid, json=body)
        session.invalidate()
        extra = {k: updated.get(k) for k in ("aliases", "iconGlyph", "iconColor", "banner") if updated.get(k)}
        return dump(_summary(updated) | extra)

    @t.tool(annotations=UPDATE, write=True)
    async def move_resource(
        resource: ResourceArg,
        new_parent: Annotated[
            str | None,
            Field(description="New parent id or name, or 'root' for the top level. Omit to keep the parent."),
        ] = None,
        after: Annotated[str | None, Field(description="Place after this sibling id.")] = None,
        before: Annotated[str | None, Field(description="Place before this sibling id.")] = None,
        project: ProjectArg = None,
    ) -> str:
        """Move a resource (and its children) to another parent and/or position among its siblings."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        if after and before:
            raise InputError("Pass only one of after/before.")
        body: dict[str, Any] = compact({"after": after, "before": before})
        if new_parent is not None:
            # Omitting parentId keeps the current parent (reorder only); null means the root.
            body["parentId"] = await parent_id(session, new_parent)
        if not body:
            raise InputError("Pass new_parent and/or after/before.")
        moved = await api.post("resources", rid, "move", json=body)
        session.invalidate()
        index = await session.index()
        return f"Moved '{moved.get('name')}' (id {rid}) to {index.path_text(rid)}."

    @t.tool(annotations=DESTRUCTIVE, write=True)
    async def delete_resource(
        resource: ResourceArg,
        cascade: Annotated[bool, Field(description="Also delete all child resources.")] = False,
        confirm: Annotated[bool, Field(description="Must be true. Ask the user before deleting.")] = False,
        project: ProjectArg = None,
    ) -> str:
        """Move a resource (with its documents, and children if cascade=true) to the trash. Restore it with
        restore_resource using the id returned here."""
        session = registry.session(project)
        api = await session.api()
        rid = await session.resolve_resource(resource)
        index = await session.index()
        name = index.name_of(rid) or rid
        descendants = len(list(index.descendants(rid)))
        require_confirm(
            confirm, f"delete '{name}'" + (f" and its {descendants} descendants" if cascade else "")
        )
        await api.delete("resources", rid, params={"cascade": True} if cascade else None)
        session.invalidate()
        extra = f" and {descendants} descendant(s)" if cascade and descendants else ""
        return f"Deleted '{name}' (id {rid}){extra}. Restore with restore_resource(resource_id='{rid}')."

    @t.tool(annotations=UPDATE, write=True)
    async def restore_resource(
        resource_id: Annotated[str, Field(description="Id of the deleted resource.")],
        project: ProjectArg = None,
    ) -> str:
        """Restore a deleted resource from the trash (its parent must not be deleted)."""
        session = registry.session(project)
        api = await session.api()
        restored = await api.post("resources", resource_id.strip(), "restore")
        session.invalidate()
        return f"Restored '{restored.get('name')}' (id {restored.get('id')})."

    @t.tool(annotations=WRITE, write=True)
    async def create_from_template(
        template: Annotated[str, Field(description="Template id or name (see list_templates).")],
        name: Annotated[
            str | None, Field(description="Name of the new resource (default: template name).")
        ] = None,
        parent: Annotated[str | None, Field(description="Parent id or name; omit for top level.")] = None,
        include_children: Annotated[
            bool, Field(description="Also copy the template's child resources.")
        ] = False,
        project: ProjectArg = None,
    ) -> str:
        """Create a resource from a template (copies its documents, properties, icon and tags)."""
        session = registry.session(project)
        api = await session.api()
        template_id = await resolve_template(api, template)
        body = compact(
            {
                "templateId": template_id,
                "name": name,
                "parentId": await parent_id(session, parent),
                "includeChildren": include_children or None,
            }
        )
        created = await api.post("resources", "from-template", json=body)
        session.invalidate()
        return dump(_summary(created))
