"""Map feature tools (pins, labels, regions, paths, folders)."""

from typing import Annotated, Any, Literal

from pydantic import Field

from ..documents import pick_document, pixel_to_pos
from ..errors import InputError
from .common import (
    DESTRUCTIVE,
    UPDATE,
    WRITE,
    DocumentArg,
    ProjectArg,
    ResourceArg,
    ToolContext,
    compact,
    dump,
)
from .common import require_confirm as _confirm

Point = Annotated[
    list[float], Field(min_length=2, max_length=2, description="[lat, lng] (or [x, y] in pixel mode)")
]
IconShape = Literal["pin", "diamond", "flag", "marker", "shield", "circle", "banner", "arch", "none"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


def register(t: ToolContext) -> None:
    registry = t.registry

    async def map_doc(session: Any, resource: str, document: str | None) -> tuple[Any, str, dict[str, Any]]:
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document, ("map",))
        return api, rid, doc

    async def link_uri(session: Any, link: str | None) -> str | None:
        if link is None:
            return None
        if link.startswith("lk://"):
            return link
        return f"lk://resources/{await session.resolve_resource(link)}"

    async def convert(
        api: Any, rid: str, doc: dict[str, Any], points: list[list[float]]
    ) -> list[list[float]]:
        full = await api.get("resources", rid, "docs", doc["id"])
        image = (full or {}).get("mapImage")
        if not image:
            raise InputError("This map has no image, so pixel coordinates cannot be used.")
        return [pixel_to_pos(image, x, y) for x, y in points]

    @t.tool(annotations=WRITE, write=True)
    async def create_map_feature(
        resource: ResourceArg,
        name: Annotated[str, Field(max_length=500)],
        type: Annotated[
            Literal["pin", "label", "region", "path", "folder"], Field(description="Feature type.")
        ] = "pin",
        position: Annotated[Point | None, Field(description="Pin/label position.")] = None,
        points: Annotated[
            list[Point] | None, Field(description="Vertices of a region (polygon) or a path (polyline).")
        ] = None,
        pixel_coordinates: Annotated[
            bool, Field(description="Treat position/points as image pixels [x, y] from the top-left corner.")
        ] = False,
        link: Annotated[
            str | None, Field(description="Resource id/name (or lk:// URI) the feature opens when clicked.")
        ] = None,
        icon_glyph: Annotated[
            str | None, Field(description="Pin icon: a Font Awesome name, e.g. 'castle'.")
        ] = None,
        icon_shape: Annotated[
            IconShape | None, Field(description="Shape behind a pin's glyph; default 'none' (bare glyph).")
        ] = None,
        icon_color: Annotated[
            HexColor | None, Field(description="Pin shape color (only with a shape).")
        ] = None,
        color: Annotated[HexColor | None, Field(description="Region fill/border or path line color.")] = None,
        folder: Annotated[
            str | None, Field(description="Id of a folder feature to nest this feature in.")
        ] = None,
        is_hidden: bool | None = None,
        extra: Annotated[
            dict[str, Any] | None,
            Field(
                description="Any other createFeature fields (labelSize, fillOpacity, strokeWidth, textColor…)."
            ),
        ] = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Add a pin, label, region, path or folder to a map document. Coordinates are [lat, lng] in the map's frame
        (read_document on the map shows its bounds), or image pixels with pixel_coordinates=true."""
        session = registry.session(project)
        api, rid, doc = await map_doc(session, resource, document)
        if pixel_coordinates:
            if position is not None:
                position = (await convert(api, rid, doc, [position]))[0]
            if points:
                points = await convert(api, rid, doc, points)
        body: dict[str, Any] = {"type": type, "name": name}
        if type in ("pin", "label"):
            if position is None:
                raise InputError(f"A {type} needs `position`.")
            body["pos"] = position
        elif type == "region":
            if not points or len(points) < 3:
                raise InputError("A region needs at least 3 `points`.")
            body["polygon"] = points
        elif type == "path":
            if not points or len(points) < 2:
                raise InputError("A path needs at least 2 `points`.")
            body["polyline"] = points
        if type == "pin":
            body["iconShape"] = icon_shape or "none"
            body.update(compact({"iconGlyph": icon_glyph, "iconColor": icon_color}))
        if color and type in ("region", "path"):
            body["color"] = color
        body.update(
            compact({"uri": await link_uri(session, link), "parentId": folder, "isHidden": is_hidden})
        )
        body.update(extra or {})
        created = await api.post("resources", rid, "docs", doc["id"], "features", json=body)
        return dump(created)

    @t.tool(annotations=UPDATE, write=True)
    async def update_map_feature(
        resource: ResourceArg,
        feature_id: str,
        name: Annotated[str | None, Field(max_length=500)] = None,
        position: Point | None = None,
        points: list[Point] | None = None,
        pixel_coordinates: bool = False,
        link: Annotated[str | None, Field(description="Resource id/name or lk:// URI.")] = None,
        icon_glyph: str | None = None,
        icon_shape: IconShape | None = None,
        icon_color: HexColor | None = None,
        color: HexColor | None = None,
        is_hidden: bool | None = None,
        extra: Annotated[dict[str, Any] | None, Field(description="Other updateFeature fields.")] = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Change a map feature: rename, move, reshape, relink, restyle or hide it."""
        session = registry.session(project)
        api, rid, doc = await map_doc(session, resource, document)
        current = await api.get("resources", rid, "docs", doc["id"], "features", feature_id)
        if pixel_coordinates:
            if position is not None:
                position = (await convert(api, rid, doc, [position]))[0]
            if points:
                points = await convert(api, rid, doc, points)
        body: dict[str, Any] = compact(
            {
                "name": name,
                "pos": position,
                "uri": await link_uri(session, link),
                "iconGlyph": icon_glyph,
                "iconShape": icon_shape,
                "iconColor": icon_color,
                "color": color,
                "isHidden": is_hidden,
            }
        )
        if points:
            body["polyline" if current.get("type") == "path" else "polygon"] = points
        body.update(extra or {})
        if not body:
            raise InputError("Nothing to update.")
        return dump(await api.patch("resources", rid, "docs", doc["id"], "features", feature_id, json=body))

    @t.tool(annotations=DESTRUCTIVE, write=True)
    async def delete_map_feature(
        resource: ResourceArg,
        feature_id: str,
        confirm: Annotated[bool, Field(description="Must be true.")] = False,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Delete a feature (pin, label, region, path or folder) from a map."""
        session = registry.session(project)
        api, rid, doc = await map_doc(session, resource, document)
        _confirm(confirm, f"delete map feature {feature_id}")
        await api.delete("resources", rid, "docs", doc["id"], "features", feature_id)
        return f"Deleted map feature {feature_id}."
