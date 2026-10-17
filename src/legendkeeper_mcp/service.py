"""Per-project state shared by the tools: API clients, project ids and a cached resource index."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .client import API_PREFIX, LegendKeeperClient, ProjectClient, seg
from .config import ProjectConfig, Settings
from .errors import InputError, LegendKeeperError

TEMPLATES_ROOT = "templates"
INDEX_TTL_SECONDS = 60.0


@dataclass
class ResourceIndex:
    """All resources of a project (as listed by ``listResources``) with tree/name lookups."""

    items: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]] = field(init=False)
    children: dict[str | None, list[dict[str, Any]]] = field(init=False)
    templates: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {r["id"]: r for r in self.items}
        self.children = defaultdict(list)
        for r in self.items:
            parent = r.get("parentId")
            self.children[parent if parent in self.by_id or parent == TEMPLATES_ROOT else None].append(r)
        for siblings in self.children.values():
            siblings.sort(key=lambda r: (r.get("rank") or "", r.get("name") or ""))
        self.templates = set()
        stack = [r["id"] for r in self.children.get(TEMPLATES_ROOT, [])]
        while stack:
            rid = stack.pop()
            self.templates.add(rid)
            stack.extend(c["id"] for c in self.children.get(rid, []))

    def roots(self) -> list[dict[str, Any]]:
        return list(self.children.get(None, []))

    def kids(self, resource_id: str | None) -> list[dict[str, Any]]:
        return list(self.children.get(resource_id, []))

    def name_of(self, resource_id: str) -> str | None:
        item = self.by_id.get(resource_id)
        return str(item["name"]) if item else None

    def path(self, resource_id: str) -> list[dict[str, Any]]:
        """Ancestors from the root down (excluding the resource itself)."""
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current = self.by_id.get(resource_id)
        while current and current.get("parentId") in self.by_id and current["parentId"] not in seen:
            seen.add(current["parentId"])
            current = self.by_id[current["parentId"]]
            chain.append(current)
        return list(reversed(chain))

    def path_text(self, resource_id: str) -> str:
        names = [str(r.get("name")) for r in self.path(resource_id)]
        return " / ".join(names) if names else "(root)"

    def find(self, name: str, *, include_templates: bool = False) -> list[dict[str, Any]]:
        wanted = " ".join(name.split()).casefold()
        exact = [r for r in self.items if " ".join(str(r.get("name", "")).split()).casefold() == wanted]
        if not exact:
            exact = [
                r
                for r in self.items
                if any(" ".join(a.split()).casefold() == wanted for a in r.get("aliases") or [])
            ]
        if not include_templates:
            non_templates = [r for r in exact if r["id"] not in self.templates]
            exact = non_templates or exact
        return exact

    def resolve_wiki(self, name: str) -> tuple[str, str] | None:
        matches = self.find(name)
        if not matches:
            return None
        best = matches[0]
        return best["id"], str(best["name"])

    def descendants(self, resource_id: str) -> Iterable[dict[str, Any]]:
        stack = list(reversed(self.kids(resource_id)))
        while stack:
            item = stack.pop()
            yield item
            stack.extend(reversed(self.kids(item["id"])))


class ProjectSession:
    """Lazily resolves the project id behind an API key and caches the resource index."""

    def __init__(self, config: ProjectConfig, client: LegendKeeperClient) -> None:
        self.config = config
        self.client = client
        self._api: ProjectClient | None = None
        self._info: dict[str, Any] | None = None
        self._index: ResourceIndex | None = None
        self._index_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self.config.name

    async def api(self) -> ProjectClient:
        if self._api is not None:
            return self._api
        async with self._lock:
            if self._api is None:
                project_id = self.config.project_id
                if not project_id:
                    page = await self.client.request("GET", f"{API_PREFIX}/projects", params={"limit": 1})
                    data = (page or {}).get("data") or []
                    if not data:
                        raise LegendKeeperError(
                            404, "NOT_FOUND", "The API key does not belong to any project."
                        )
                    self._info = data[0]
                    project_id = str(data[0]["id"])
                self._api = ProjectClient(self.client, project_id)
        return self._api

    async def info(self) -> dict[str, Any]:
        api = await self.api()
        if self._info is None or "name" not in self._info:
            self._info = await self.client.request("GET", f"{API_PREFIX}/projects/{seg(api.project_id)}")
        return dict(self._info or {})

    async def index(self, *, refresh: bool = False) -> ResourceIndex:
        if not refresh and self._index is not None and time.monotonic() - self._index_at < INDEX_TTL_SECONDS:
            return self._index
        api = await self.api()
        items = await api.list_all("resources")
        self._index = ResourceIndex(items)
        self._index_at = time.monotonic()
        return self._index

    def invalidate(self) -> None:
        self._index = None

    async def resolve_resource(self, ref: str) -> str:
        """Accept a resource id or an exact (case-insensitive) resource name/alias and return the id."""
        ref = ref.strip()
        if not ref:
            raise InputError("A resource id or name is required.")
        index = await self.index()
        if ref in index.by_id:
            return ref
        matches = index.find(ref, include_templates=True)
        if len(matches) == 1:
            return str(matches[0]["id"])
        if len(matches) > 1:
            listing = "\n".join(
                f"  - {m['name']} (id {m['id']}, in {index.path_text(m['id'])})" for m in matches[:10]
            )
            raise InputError(f"'{ref}' matches several resources; pass the id instead:\n{listing}")
        # Possibly created moments ago: refresh once before giving up.
        index = await self.index(refresh=True)
        if ref in index.by_id:
            return ref
        matches = index.find(ref, include_templates=True)
        if len(matches) == 1:
            return str(matches[0]["id"])
        raise InputError(f"No resource with id or name '{ref}'. Use search or browse_tree to find it.")


class Registry:
    """All configured projects."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._sessions: dict[str, ProjectSession] = {}

    def session(self, name: str | None = None) -> ProjectSession:
        config = self.settings.project(name)
        if config.name not in self._sessions:
            client = LegendKeeperClient(
                config.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.timeout,
                max_retries=self.settings.max_retries,
                transport=self._transport,
            )
            self._sessions[config.name] = ProjectSession(config, client)
        return self._sessions[config.name]

    @property
    def names(self) -> list[str]:
        return sorted(self.settings.projects)

    async def aclose(self) -> None:
        for session in self._sessions.values():
            await session.client.aclose()
        self._sessions.clear()
