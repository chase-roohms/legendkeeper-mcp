"""Timeline tools (lanes and events)."""

from typing import Annotated, Any, Literal

from pydantic import Field

from ..documents import pick_document
from ..errors import InputError
from ..ranks import rank_after
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

HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
DateArg = Annotated[
    str | None,
    Field(
        description="Date in the timeline's calendar, as people write it: '1492', 'Mirtul 1492', '3 March 1204', "
        "'1204-03-03'. Parsed by LegendKeeper with the document's calendar."
    ),
]


def register(t: ToolContext) -> None:
    registry = t.registry

    async def time_doc(session: Any, resource: str, document: str | None) -> tuple[Any, str, dict[str, Any]]:
        api = await session.api()
        rid = await session.resolve_resource(resource)
        doc = await pick_document(api, rid, document, ("time",))
        return api, rid, doc

    async def resolve_lane(
        api: Any, rid: str, doc: dict[str, Any], lane: str | None, create: bool
    ) -> tuple[str, str]:
        lanes = (await api.get("resources", rid, "docs", doc["id"], "lanes") or {}).get("data") or []
        if lane:
            for item in lanes:
                if item["id"] == lane or str(item.get("name", "")).casefold() == lane.strip().casefold():
                    return item["id"], ""
            if not create:
                names = ", ".join(f"'{x.get('name')}'" for x in lanes) or "none"
                raise InputError(f"No lane '{lane}' on this timeline (lanes: {names}).")
        elif lanes:
            first = min(lanes, key=lambda x: x.get("rank") or "")
            return first["id"], ""
        name = (lane or "Events").strip()
        last = max((x.get("rank") or "" for x in lanes), default=None)
        created = await api.post(
            "resources",
            rid,
            "docs",
            doc["id"],
            "lanes",
            json={"name": name, "rank": rank_after(last), "size": "sm"},
        )
        return created["id"], f"Created lane '{name}' (id {created['id']}). "

    async def link_uri(session: Any, link: str | None) -> str | None:
        if link is None:
            return None
        if link.startswith("lk://"):
            return link
        return f"lk://resources/{await session.resolve_resource(link)}"

    @t.tool(annotations=WRITE, write=True)
    async def create_timeline_event(
        resource: ResourceArg,
        name: Annotated[
            str, Field(max_length=500, description="Short event name (events have no description).")
        ],
        start: DateArg = None,
        end: DateArg = None,
        start_minutes: Annotated[
            float | None, Field(description="Exact start in calendar minutes (alternative to `start`).")
        ] = None,
        end_minutes: float | None = None,
        lane: Annotated[
            str | None,
            Field(description="Lane id or name; a new lane is created if the name does not exist."),
        ] = None,
        link: Annotated[
            str | None, Field(description="Resource id/name (or lk:// URI) with the full story.")
        ] = None,
        type: Annotated[
            Literal["event", "note"], Field(description="'event' or a 'note' annotation.")
        ] = "event",
        color: HexColor | None = None,
        icon_glyph: str | None = None,
        is_hidden: bool | None = None,
        show_on_calendar: bool | None = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Add an event to a timeline document. Link it to the page that tells the full story."""
        if start is None and start_minutes is None:
            raise InputError("Pass `start` (a date) or `start_minutes`.")
        session = registry.session(project)
        api, rid, doc = await time_doc(session, resource, document)
        lane_id, note = await resolve_lane(api, rid, doc, lane, create=True)
        body: dict[str, Any] = compact(
            {
                "laneId": lane_id,
                "type": type,
                "name": name,
                "formattedStart": start if start_minutes is None else None,
                "start": start_minutes,
                "formattedEnd": end if end_minutes is None else None,
                "end": end_minutes,
                "uri": await link_uri(session, link),
                "color": color,
                "iconGlyph": icon_glyph,
                "isHidden": is_hidden,
                "showOnCalendar": show_on_calendar,
            }
        )
        created = await api.post("resources", rid, "docs", doc["id"], "events", json=body)
        when = created.get("formattedStart") or created.get("start")
        return f"{note}Created event '{created.get('name')}' (id {created.get('id')}) at {when}.\n{dump(created)}"

    @t.tool(annotations=UPDATE, write=True)
    async def update_timeline_event(
        resource: ResourceArg,
        event_id: str,
        name: Annotated[str | None, Field(max_length=500)] = None,
        start: DateArg = None,
        end: DateArg = None,
        start_minutes: float | None = None,
        end_minutes: float | None = None,
        lane: Annotated[str | None, Field(description="Move to this lane (id or existing name).")] = None,
        link: str | None = None,
        color: HexColor | None = None,
        icon_glyph: str | None = None,
        is_hidden: bool | None = None,
        show_on_calendar: bool | None = None,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Change a timeline event's name, dates, lane, link or style."""
        session = registry.session(project)
        api, rid, doc = await time_doc(session, resource, document)
        lane_id = (await resolve_lane(api, rid, doc, lane, create=False))[0] if lane else None
        body = compact(
            {
                "laneId": lane_id,
                "name": name,
                "formattedStart": start,
                "start": start_minutes,
                "formattedEnd": end,
                "end": end_minutes,
                "uri": await link_uri(session, link),
                "color": color,
                "iconGlyph": icon_glyph,
                "isHidden": is_hidden,
                "showOnCalendar": show_on_calendar,
            }
        )
        if not body:
            raise InputError("Nothing to update.")
        return dump(await api.patch("resources", rid, "docs", doc["id"], "events", event_id, json=body))

    @t.tool(annotations=DESTRUCTIVE, write=True)
    async def delete_timeline_event(
        resource: ResourceArg,
        event_id: str,
        confirm: Annotated[bool, Field(description="Must be true.")] = False,
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Delete an event from a timeline."""
        session = registry.session(project)
        api, rid, doc = await time_doc(session, resource, document)
        _confirm(confirm, f"delete timeline event {event_id}")
        await api.delete("resources", rid, "docs", doc["id"], "events", event_id)
        return f"Deleted event {event_id}."

    @t.tool(annotations=WRITE, write=True)
    async def create_timeline_lane(
        resource: ResourceArg,
        name: Annotated[str, Field(max_length=500)],
        color: Annotated[HexColor | None, Field(description="Color for every event in the lane.")] = None,
        size: Literal["sm", "lg"] = "sm",
        document: DocumentArg = None,
        project: ProjectArg = None,
    ) -> str:
        """Add a lane (row) to a timeline, placed after the existing lanes."""
        session = registry.session(project)
        api, rid, doc = await time_doc(session, resource, document)
        lanes = (await api.get("resources", rid, "docs", doc["id"], "lanes") or {}).get("data") or []
        last = max((x.get("rank") or "" for x in lanes), default=None)
        body: dict[str, Any] = {"name": name, "rank": rank_after(last), "size": size}
        if color:
            body["style"] = {"color": color}
        return dump(await api.post("resources", rid, "docs", doc["id"], "lanes", json=body))
