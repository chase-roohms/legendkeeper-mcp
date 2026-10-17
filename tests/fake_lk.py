"""An in-memory fake of the LegendKeeper v3 API, served through ``httpx.MockTransport``.

When the official OpenAPI spec is available, every request is checked against it: the route must exist,
request bodies must validate against the request schema, and the fake's own responses must validate against
the response schema. This keeps the server honest about the real API contract.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .spec import SpecValidator

PROJECT_ID = "proj0001"
API_KEY = "lk_test_key_123456"
STORAGE = "https://storage.fake"
ASSET_HOST = "https://assets.legendkeeper.test"
NOW = "2026-01-01T00:00:00Z"
MINUTES_PER_YEAR = 525600


class ContractError(AssertionError):
    pass


def new_id() -> str:
    return "c" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(23))


def gregorian() -> dict[str, Any]:
    months = [
        ("january", "January", 31), ("february", "February", 28), ("march", "March", 31), ("april", "April", 30),
        ("may", "May", 31), ("june", "June", 30), ("july", "July", 31), ("august", "August", 31),
        ("september", "September", 30), ("october", "October", 31), ("november", "November", 30),
        ("december", "December", 31),
    ]  # fmt: skip
    return {
        "id": "gregorian",
        "name": "Gregorian",
        "hasZeroYear": False,
        "maxMinutes": 10**12,
        "months": [
            {"id": i, "name": n, "isIntercalary": False, "length": length, "interval": 1, "offset": 0}
            for i, n, length in months
        ],
        "leapDays": [],
        "weekdays": [{"id": d.lower(), "name": d} for d in ("Monday", "Tuesday", "Wednesday")],
        "epochWeekday": 0,
        "hoursInDay": 24,
        "minutesInHour": 60,
        "positiveEras": [{"id": "ad", "name": "Anno Domini", "abbr": "AD", "startsAt": 0, "resetMode": "none"}],
        "version": "1",
    }


@dataclass
class ApiError(Exception):
    status: int
    code: str
    message: str = "error"


@dataclass
class Recorded:
    method: str
    path: str
    params: dict[str, str]
    body: Any
    headers: dict[str, str]


@dataclass
class FakeLegendKeeper:
    validator: SpecValidator | None = None
    project_id: str = PROJECT_ID
    project_name: str = "Land of Ooo"
    admin: bool = True
    api_key: str = API_KEY
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    properties: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    uploads: dict[str, dict[str, Any]] = field(default_factory=dict)
    downloads: dict[str, bytes] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)
    queued_errors: list[tuple[str, str, ApiError, dict[str, str]]] = field(default_factory=list)
    contract_errors: list[str] = field(default_factory=list)
    rank_counter: int = 0
    before_handle: Callable[[Recorded], None] | None = None

    # ------------------------------------------------------------------ setup helpers

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def next_rank(self) -> str:
        self.rank_counter += 1
        return f"a{self.rank_counter:04d}"

    def add_resource(
        self,
        name: str,
        parent_id: str | None = None,
        *,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
        doc_type: str = "page",
        blocks: list[dict[str, Any]] | None = None,
        rid: str | None = None,
        icon: str | None = None,
    ) -> dict[str, Any]:
        rid = rid or new_id()
        resource = {
            "id": rid,
            "name": name,
            "parentId": parent_id,
            "tags": tags or [],
            "aliases": aliases or [],
            "rank": self.next_rank(),
            "isHidden": False,
            "isLocked": False,
            "permissions": {},
            "docIds": [],
            "deleted": False,
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        if icon:
            resource["iconGlyph"] = icon
        self.resources[rid] = resource
        self.properties[rid] = []
        self.add_document(rid, "Main", doc_type, blocks=blocks)
        return resource

    def add_document(
        self, rid: str, name: str, doc_type: str, *, blocks: list[dict[str, Any]] | None = None, **extra: Any
    ) -> dict[str, Any]:
        doc = {
            "id": new_id(),
            "resourceId": rid,
            "name": name,
            "type": doc_type,
            "rank": self.next_rank(),
            "isHidden": False,
            "isFullWidth": False,
            "createdAt": NOW,
            "updatedAt": NOW,
            "version": 1,
            "blocks": self._with_ids(copy.deepcopy(blocks or [])),
            "features": {},
            "lanes": {},
            "events": {},
            "deleted": False,
        }
        doc.update(extra)
        self.documents[doc["id"]] = doc
        self.resources[rid]["docIds"].append(doc["id"])
        return doc

    def doc_of(self, rid: str, index: int = 0) -> dict[str, Any]:
        return self.documents[self.resources[rid]["docIds"][index]]

    def fail(self, method: str, path_regex: str, status: int, code: str, *, headers: dict[str, str] | None = None) -> None:
        """Make the next matching request fail once."""
        self.queued_errors.append((method, path_regex, ApiError(status, code, f"injected {code}"), headers or {}))

    def calls(self, method: str | None = None, contains: str = "") -> list[Recorded]:
        return [r for r in self.requests if (method is None or r.method == method) and contains in r.path]

    # ------------------------------------------------------------------ serialization

    def _with_ids(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for block in blocks:
            if block.get("kind") != "unsupported":
                block.setdefault("id", new_id())
            if block.get("kind") == "infobox":
                for prop in block.get("rows") or []:
                    prop.setdefault("id", new_id())
                    prop.setdefault("rank", self.next_rank())
                    self._row_ids(prop)
            if block.get("kind") == "keyValue":
                self._row_ids(block)
            self._with_ids(block.get("children") or [])
        return blocks

    def _row_ids(self, holder: dict[str, Any]) -> None:
        for row in holder.get("rows") or []:
            row.setdefault("id", new_id())
            row.setdefault("rank", self.next_rank())
            if "content" not in row:
                row["content"] = [{"text": row.get("text", "")}] if row.get("text") else []
            row["text"] = _plain(row.get("content") or [])

    def _listed(self, r: dict[str, Any]) -> dict[str, Any]:
        out = {k: r.get(k) for k in ("id", "name", "parentId", "tags", "aliases", "rank", "isHidden", "createdAt", "updatedAt")}
        if r.get("iconGlyph"):
            out["iconGlyph"] = r["iconGlyph"]
        out["documents"] = [
            {"id": d, "name": self.documents[d]["name"], "type": self.documents[d]["type"]}
            for d in r["docIds"]
            if not self.documents[d]["deleted"]
        ]
        return out

    def _document(self, d: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
        out = {k: d[k] for k in ("id", "name", "type", "rank", "isHidden", "isFullWidth", "createdAt", "updatedAt")}
        if d.get("calendarId"):
            out["calendarId"] = d["calendarId"]
        if full and d["type"] == "map":
            out["mapImage"] = d.get("mapImage")
        if d["type"] == "time":
            out["sources"] = d.get("sources", [])
        return out

    def _resource(self, r: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in r.items() if k not in ("docIds", "deleted") and v is not None}
        out["documents"] = [self._document(self.documents[d]) for d in r["docIds"] if not self.documents[d]["deleted"]]
        out["properties"] = copy.deepcopy(self.properties.get(r["id"], []))
        return out

    def _blocks(self, d: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": d["id"],
            "version": str(d["version"]),
            "schema": 1,
            "text": "\n".join(_block_text(b) for b in d["blocks"] if _block_text(b)),
            "blocks": copy.deepcopy(d["blocks"]),
        }

    # ------------------------------------------------------------------ lookups

    def _res(self, rid: str) -> dict[str, Any]:
        r = self.resources.get(rid)
        if r is None or r["deleted"]:
            raise ApiError(404, "NOT_FOUND", f"resource {rid}")
        return r

    def _doc(self, rid: str, did: str) -> dict[str, Any]:
        self._res(rid)
        d = self.documents.get(did)
        if d is None or d["resourceId"] != rid or d["deleted"]:
            raise ApiError(404, "NOT_FOUND", f"document {did}")
        return d

    def _check_version(self, d: dict[str, Any], headers: dict[str, str]) -> None:
        wanted = headers.get("if-match")
        if wanted is not None and wanted != str(d["version"]):
            raise ApiError(412, "VERSION_MISMATCH", "version mismatch")

    def _bump(self, d: dict[str, Any]) -> None:
        d["version"] += 1
        d["updatedAt"] = NOW

    def _page_doc(self, d: dict[str, Any]) -> None:
        if d["type"] == "blank":
            d["type"] = "page"
        elif d["type"] != "page":
            raise ApiError(400, "KIND_MISMATCH", "not a page")

    # ------------------------------------------------------------------ transport

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "storage.fake":
            key = url.path.rsplit("/", 1)[-1]
            if request.method != "PUT" or key not in self.uploads:
                return httpx.Response(403, text="bad upload")
            ticket = self.uploads[key]
            if len(request.content) != ticket["contentLength"] or request.headers.get("content-type") != ticket["contentType"]:
                return httpx.Response(400, text="mismatch")
            ticket["stored"] = True
            return httpx.Response(200)
        if url.host == "images.example":
            data = self.downloads.get(url.path)
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)

        body = json.loads(request.content) if request.content else None
        headers = {k.lower(): v for k, v in request.headers.items()}
        record = Recorded(request.method, url.path, dict(url.params), body, headers)
        self.requests.append(record)
        if self.before_handle:
            self.before_handle(record)

        if headers.get("authorization") != f"Bearer {self.api_key}":
            return self._error(ApiError(401, "UNAUTHENTICATED", "bad key"))
        if request.method == "POST" and not headers.get("idempotency-key"):
            self.contract_errors.append(f"POST {url.path} without Idempotency-Key")

        op = None
        if self.validator is not None:
            op = self.validator.find(request.method, url.path)
            if op is None:
                self.contract_errors.append(f"{request.method} {url.path} is not an operation in the OpenAPI spec")
                return self._error(ApiError(405, "METHOD_NOT_ALLOWED", "unknown route"))
            if op.request_pointer and body is not None:
                problems = self.validator.errors(op.request_pointer, body)
                if problems:
                    self.contract_errors.append(f"{op.operation_id} request body invalid: {problems}")
                    return self._error(ApiError(400, "VALIDATION_ERROR", "; ".join(problems)))

        for i, (method, pattern, error, extra_headers) in enumerate(self.queued_errors):
            if method == request.method and re.search(pattern, url.path):
                del self.queued_errors[i]
                return self._error(error, extra_headers)

        try:
            status, payload, extra = self._route(request.method, url.path, dict(url.params), body, headers)
        except ApiError as exc:
            return self._error(exc)

        if self.validator is not None and op is not None and payload is not None:
            pointer = op.response_pointers.get(str(status))
            if pointer:
                problems = self.validator.errors(pointer, payload)
                if problems:
                    self.contract_errors.append(f"fake {op.operation_id} response invalid: {problems}")
        response_headers = {"x-ratelimit-limit": "300", "x-ratelimit-remaining": "299", "x-ratelimit-reset": "0"}
        response_headers.update(extra)
        if payload is None:
            return httpx.Response(status, headers=response_headers)
        return httpx.Response(status, json=payload, headers=response_headers)

    def _error(self, error: ApiError, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(
            error.status,
            json={"message": error.message, "code": error.code, "requestId": "req_test"},
            headers=headers or {},
        )

    # ------------------------------------------------------------------ routing

    def _route(  # noqa: C901
        self, method: str, path: str, params: dict[str, str], body: Any, headers: dict[str, str]
    ) -> tuple[int, Any, dict[str, str]]:
        if path == "/api/v3/projects" and method == "GET":
            return 200, {"data": [self._project()], "hasMore": False, "nextCursor": None}, {}
        prefix = f"/api/v3/projects/{self.project_id}"
        if not path.startswith(prefix):
            raise ApiError(403, "WRONG_PROJECT", "wrong project")
        parts = [p for p in path[len(prefix) :].split("/") if p]
        if not parts:
            return 200, self._project(), {}
        handler_name = "h_" + "_".join(p.replace("-", "_") if p in _LITERALS else "x" for p in parts)
        handler_name += "_" + method.lower()
        handler = getattr(self, handler_name, None)
        if handler is None:
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"fake has no {handler_name}")
        ids = [p for p in parts if p not in _LITERALS]
        return handler(*ids, params=params, body=body, headers=headers)

    def _project(self) -> dict[str, Any]:
        return {"id": self.project_id, "name": self.project_name, "isPublic": False, "createdAt": NOW}

    # resources ---------------------------------------------------------------------------

    def h_resources_get(self, *, params: dict[str, str], **_: Any) -> Any:
        items = sorted((r for r in self.resources.values() if not r["deleted"]), key=lambda r: r["id"])
        if params.get("parentId"):
            items = [r for r in items if r.get("parentId") == params["parentId"]]
        cursor = params.get("cursor")
        if cursor:
            items = [r for r in items if r["id"] > cursor]
        limit = int(params.get("limit", 50))
        page = items[:limit]
        more = len(items) > limit
        return 200, {"data": [self._listed(r) for r in page], "hasMore": more, "nextCursor": page[-1]["id"] if more else None}, {}

    def _create(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("parentId") and body["parentId"] != "templates":
            self._res(body["parentId"])
        if body.get("id") and body["id"] in self.resources:
            raise ApiError(409, "DUPLICATE_ID", "duplicate")
        if body.get("isHidden") and not self.admin:
            raise ApiError(403, "FORBIDDEN", "admins only")
        docs = body.get("documents") or [{"type": "blank", "name": "Main"}]
        r = self.add_resource(
            body["name"], body.get("parentId"), tags=body.get("tags"), aliases=body.get("aliases"),
            doc_type=docs[0].get("type") or ("page" if docs[0].get("blocks") else "blank"),
            blocks=docs[0].get("blocks"), rid=body.get("id"),
        )  # fmt: skip
        self.documents[r["docIds"][0]]["name"] = docs[0].get("name") or "Main"
        for extra in docs[1:]:
            self.add_document(r["id"], extra.get("name") or "Main", extra.get("type") or "blank", blocks=extra.get("blocks"))
        for key in ("iconGlyph", "iconColor", "iconShape", "isHidden", "isLocked", "banner"):
            if key in body:
                r[key] = body[key]
        return r

    def h_resources_post(self, *, body: dict[str, Any], **_: Any) -> Any:
        return 201, self._resource(self._create(body)), {}

    def h_resources_batch_post(self, *, body: dict[str, Any], **_: Any) -> Any:
        created = [self._create(item) for item in body["resources"]]
        return 201, {"resources": [self._resource(r) for r in created]}, {}

    def h_templates_get(self, **_: Any) -> Any:
        items = [self._listed(r) for r in self.resources.values() if r.get("parentId") == "templates" and not r["deleted"]]
        return 200, {"data": items, "hasMore": False, "nextCursor": None}, {}

    def h_resources_from_template_post(self, *, body: dict[str, Any], **_: Any) -> Any:
        template = self._res(body["templateId"])
        source = self.doc_of(template["id"])
        r = self._create(
            {"name": body.get("name") or template["name"].replace("Template", "").strip(), "parentId": body.get("parentId"),
             "tags": template["tags"], "documents": [{"type": source["type"], "name": source["name"], "blocks": source["blocks"]}]}
        )  # fmt: skip
        return 201, self._resource(r), {}

    def h_resources_x_get(self, rid: str, **_: Any) -> Any:
        return 200, self._resource(self._res(rid)), {}

    def h_resources_x_patch(self, rid: str, *, body: dict[str, Any], **_: Any) -> Any:
        r = self._res(rid)
        r.update(body)
        return 200, self._resource(r), {}

    def h_resources_x_delete(self, rid: str, *, params: dict[str, str], **_: Any) -> Any:
        r = self._res(rid)
        children = [c for c in self.resources.values() if c.get("parentId") == rid and not c["deleted"]]
        if children and params.get("cascade") != "true":
            raise ApiError(409, "HAS_CHILDREN", "has children")
        stack = [r]
        while stack:
            item = stack.pop()
            item["deleted"] = True
            stack.extend(c for c in self.resources.values() if c.get("parentId") == item["id"] and not c["deleted"])
        return 204, None, {}

    def h_resources_x_restore_post(self, rid: str, **_: Any) -> Any:
        r = self.resources.get(rid)
        if r is None:
            raise ApiError(404, "NOT_FOUND", "nope")
        if not r["deleted"]:
            raise ApiError(409, "NOT_DELETED", "not deleted")
        r["deleted"] = False
        return 200, self._resource(r), {}

    def h_resources_x_move_post(self, rid: str, *, body: dict[str, Any], **_: Any) -> Any:
        r = self._res(rid)
        if body.get("parentId"):
            self._res(body["parentId"])
        r["parentId"] = body.get("parentId")
        return 200, self._resource(r), {}

    # documents ---------------------------------------------------------------------------

    def h_resources_x_docs_get(self, rid: str, **_: Any) -> Any:
        r = self._res(rid)
        docs = [self._document(self.documents[d]) for d in r["docIds"] if not self.documents[d]["deleted"]]
        return 200, {"data": docs, "hasMore": False, "nextCursor": None}, {}

    def h_resources_x_docs_post(self, rid: str, *, body: dict[str, Any], **_: Any) -> Any:
        self._res(rid)
        d = self.add_document(rid, body["name"], body["type"], calendarId=body.get("calendarId"))
        d["isHidden"] = bool(body.get("isHidden"))
        if d["type"] == "map":
            d["mapImage"] = None
        return 201, self._document(d, full=True), {}

    def h_resources_x_docs_x_get(self, rid: str, did: str, **_: Any) -> Any:
        return 200, self._document(self._doc(rid, did), full=True), {}

    def h_resources_x_docs_x_patch(self, rid: str, did: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        for key in ("name", "isHidden", "isFullWidth", "calendarId", "rank", "sources"):
            if key in body:
                d[key] = body[key]
        if "mapImage" in body:
            asset = self.assets.get(body["mapImage"]["assetId"])
            if asset is None:
                raise ApiError(400, "VALIDATION_ERROR", "unknown asset")
            w, h = asset["width"], asset["height"]
            d["mapImage"] = {"assetId": asset["id"], "url": asset["url"], "width": w, "height": h,
                             "bounds": [[-h / 8, 0], [0, w / 8]]}  # fmt: skip
        return 200, self._document(d, full=True), {}

    def h_resources_x_docs_x_delete(self, rid: str, did: str, **_: Any) -> Any:
        self._doc(rid, did)["deleted"] = True
        return 204, None, {}

    # blocks ------------------------------------------------------------------------------

    def h_resources_x_docs_x_blocks_get(self, rid: str, did: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        if d["type"] not in ("page", "blank"):
            raise ApiError(400, "KIND_MISMATCH", "not a page")
        return 200, self._blocks(d), {"etag": str(d["version"])}

    def h_resources_x_docs_x_blocks_put(self, rid: str, did: str, *, body: dict[str, Any], headers: dict[str, str], **_: Any) -> Any:
        d = self._doc(rid, did)
        self._page_doc(d)
        self._check_version(d, headers)
        ids = [b.get("id") for b in _walk(body["blocks"]) if b.get("id")]
        if len(ids) != len(set(ids)):
            raise ApiError(400, "VALIDATION_ERROR", "duplicate block id")
        d["blocks"] = self._with_ids(copy.deepcopy(body["blocks"]))
        self._bump(d)
        return 200, self._blocks(d), {"etag": str(d["version"])}

    def h_resources_x_docs_x_blocks_post(self, rid: str, did: str, *, body: dict[str, Any], headers: dict[str, str], **_: Any) -> Any:
        d = self._doc(rid, did)
        self._page_doc(d)
        self._check_version(d, headers)
        block = self._with_ids([copy.deepcopy(body["block"])])[0]
        top = d["blocks"]
        if body.get("before"):
            index = next((i for i, b in enumerate(top) if b.get("id") == body["before"]), None)
            if index is None:
                raise ApiError(404, "NOT_FOUND", "anchor")
            top.insert(index, block)
        elif body.get("after"):
            index = next((i for i, b in enumerate(top) if b.get("id") == body["after"]), None)
            if index is None:
                raise ApiError(404, "NOT_FOUND", "anchor")
            top.insert(index + 1, block)
        else:
            top.append(block)
        self._bump(d)
        return 201, {**copy.deepcopy(block), "version": str(d["version"])}, {}

    def h_resources_x_docs_x_block_ids_post(self, rid: str, did: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        assigned = sum(1 for b in _walk(d["blocks"]) if not b.get("id"))
        self._with_ids(d["blocks"])
        if assigned:
            self._bump(d)
        return 200, {"assigned": assigned, "version": str(d["version"])}, {}

    def _block(self, d: dict[str, Any], bid: str) -> dict[str, Any]:
        for b in _walk(d["blocks"]):
            if b.get("id") == bid:
                return b
        raise ApiError(404, "NOT_FOUND", "block")

    def h_resources_x_docs_x_blocks_x_properties_post(self, rid: str, did: str, bid: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        box = self._block(d, bid)
        if box.get("kind") != "infobox":
            raise ApiError(400, "KIND_MISMATCH", "not an infobox")
        prop = {k: v for k, v in copy.deepcopy(body).items() if k not in ("before", "after")}
        prop.setdefault("id", new_id())
        prop["rank"] = self.next_rank()
        self._row_ids(prop)
        box["rows"].append(prop)
        self._bump(d)
        return 201, {**copy.deepcopy(prop), "version": str(d["version"])}, {}

    def _infobox_prop(self, d: dict[str, Any], bid: str, pid: str) -> dict[str, Any]:
        box = self._block(d, bid)
        for prop in box.get("rows") or []:
            if prop.get("id") == pid:
                return prop
        raise ApiError(404, "NOT_FOUND", "property")

    def h_resources_x_docs_x_blocks_x_properties_x_rows_post(self, rid: str, did: str, bid: str, pid: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        prop = self._infobox_prop(d, bid, pid)
        row = self._new_row(body)
        prop["rows"].append(row)
        self._bump(d)
        return 201, {**row, "version": str(d["version"])}, {}

    def h_resources_x_docs_x_blocks_x_properties_x_rows_x_patch(self, rid: str, did: str, bid: str, pid: str, row_id: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        row = self._find_row(self._infobox_prop(d, bid, pid), row_id)
        self._patch_row(row, body)
        self._bump(d)
        return 200, {**row, "version": str(d["version"])}, {}

    def h_resources_x_docs_x_blocks_x_properties_x_rows_x_delete(self, rid: str, did: str, bid: str, pid: str, row_id: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        prop = self._infobox_prop(d, bid, pid)
        prop["rows"] = [r for r in prop["rows"] if r["id"] != row_id]
        self._bump(d)
        return 204, None, {}

    def _new_row(self, body: dict[str, Any]) -> dict[str, Any]:
        content = body.get("content") if body.get("content") is not None else ([{"text": body["text"]}] if body.get("text") else [])
        return {"id": new_id(), "rank": self.next_rank(), "key": body["key"], "content": content, "text": _plain(content)}

    @staticmethod
    def _find_row(holder: dict[str, Any], row_id: str) -> dict[str, Any]:
        for row in holder.get("rows") or []:
            if row["id"] == row_id:
                return row
        raise ApiError(404, "NOT_FOUND", "row")

    @staticmethod
    def _patch_row(row: dict[str, Any], body: dict[str, Any]) -> None:
        if "key" in body:
            row["key"] = body["key"]
        if "content" in body:
            row["content"] = body["content"] or []
        elif "text" in body:
            row["content"] = [{"text": body["text"]}] if body["text"] else []
        row["text"] = _plain(row["content"])

    # sidebar properties ----------------------------------------------------------------------

    def h_resources_x_properties_get(self, rid: str, **_: Any) -> Any:
        self._res(rid)
        return 200, {"data": copy.deepcopy(self.properties[rid]), "hasMore": False, "nextCursor": None}, {}

    def h_resources_x_properties_post(self, rid: str, *, body: dict[str, Any], **_: Any) -> Any:
        self._res(rid)
        prop = {k: v for k, v in copy.deepcopy(body).items() if k not in ("before", "after")}
        prop.setdefault("id", new_id())
        prop["rank"] = self.next_rank()
        self._row_ids(prop)
        self.properties[rid].append(prop)
        return 201, copy.deepcopy(prop), {}

    def _prop(self, rid: str, pid: str) -> dict[str, Any]:
        self._res(rid)
        for prop in self.properties[rid]:
            if prop["id"] == pid:
                return prop
        raise ApiError(404, "NOT_FOUND", "property")

    def h_resources_x_properties_x_rows_post(self, rid: str, pid: str, *, body: dict[str, Any], **_: Any) -> Any:
        row = self._new_row(body)
        self._prop(rid, pid)["rows"].append(row)
        return 201, {**row, "version": "1"}, {}

    def h_resources_x_properties_x_rows_x_patch(self, rid: str, pid: str, row_id: str, *, body: dict[str, Any], **_: Any) -> Any:
        row = self._find_row(self._prop(rid, pid), row_id)
        self._patch_row(row, body)
        return 200, {**row, "version": "1"}, {}

    def h_resources_x_properties_x_rows_x_delete(self, rid: str, pid: str, row_id: str, **_: Any) -> Any:
        prop = self._prop(rid, pid)
        prop["rows"] = [r for r in prop["rows"] if r["id"] != row_id]
        return 204, None, {}

    # maps ----------------------------------------------------------------------------------

    def _map_doc(self, rid: str, did: str) -> dict[str, Any]:
        d = self._doc(rid, did)
        if d["type"] == "blank":
            d["type"] = "map"
        if d["type"] != "map":
            raise ApiError(400, "KIND_MISMATCH", "not a map")
        return d

    def h_resources_x_docs_x_features_get(self, rid: str, did: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        return 200, {"data": copy.deepcopy(list(d["features"].values())), "hasMore": False, "nextCursor": None}, {}

    def h_resources_x_docs_x_features_post(self, rid: str, did: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._map_doc(rid, did)
        feature = {"id": body.get("id") or new_id(), "rank": self.next_rank(), **body}
        d["features"][feature["id"]] = feature
        return 201, copy.deepcopy(feature), {}

    def h_resources_x_docs_x_features_x_get(self, rid: str, did: str, fid: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        if fid not in d["features"]:
            raise ApiError(404, "NOT_FOUND", "feature")
        return 200, copy.deepcopy(d["features"][fid]), {}

    def h_resources_x_docs_x_features_x_patch(self, rid: str, did: str, fid: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        if fid not in d["features"]:
            raise ApiError(404, "NOT_FOUND", "feature")
        d["features"][fid].update(body)
        return 200, copy.deepcopy(d["features"][fid]), {}

    def h_resources_x_docs_x_features_x_delete(self, rid: str, did: str, fid: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        if d["features"].pop(fid, None) is None:
            raise ApiError(404, "NOT_FOUND", "feature")
        return 204, None, {}

    # timelines -----------------------------------------------------------------------------

    def _time_doc(self, rid: str, did: str) -> dict[str, Any]:
        d = self._doc(rid, did)
        if d["type"] == "blank":
            d["type"] = "time"
        if d["type"] != "time":
            raise ApiError(400, "KIND_MISMATCH", "not a timeline")
        return d

    def h_resources_x_docs_x_lanes_get(self, rid: str, did: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        return 200, {"data": copy.deepcopy(list(d["lanes"].values())), "hasMore": False, "nextCursor": None}, {}

    def h_resources_x_docs_x_lanes_post(self, rid: str, did: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._time_doc(rid, did)
        lane = {"id": body.get("id") or new_id(), **body}
        d["lanes"][lane["id"]] = lane
        return 201, copy.deepcopy(lane), {}

    def _event_out(self, event: dict[str, Any]) -> dict[str, Any]:
        year = int(event["start"] // MINUTES_PER_YEAR)
        out = dict(event)
        out["startDate"] = {"year": year, "month": 1, "monthName": "January", "day": 1, "hour": 0, "minute": 0,
                            "era": {"id": "ad", "name": "Anno Domini", "abbr": "AD"}}  # fmt: skip
        out.setdefault("formattedStart", f"{year} AD")
        if event.get("end") is not None:
            out.setdefault("formattedEnd", f"{int(event['end'] // MINUTES_PER_YEAR)} AD")
        return out

    def _parse_date(self, text: str) -> float:
        m = re.search(r"-?\d+", text)
        if not m:
            raise ApiError(400, "VALIDATION_ERROR", f"cannot parse date {text!r}")
        return float(int(m.group(0)) * MINUTES_PER_YEAR)

    def h_resources_x_docs_x_events_get(self, rid: str, did: str, *, params: dict[str, str], **_: Any) -> Any:
        d = self._doc(rid, did)
        events = sorted(d["events"].values(), key=lambda e: e["id"])
        cursor = params.get("cursor")
        if cursor:
            events = [e for e in events if e["id"] > cursor]
        limit = int(params.get("limit", 50))
        page = events[:limit]
        more = len(events) > limit
        return 200, {"data": [self._event_out(e) for e in page], "hasMore": more,
                     "nextCursor": page[-1]["id"] if more else None}, {}  # fmt: skip

    def h_resources_x_docs_x_events_post(self, rid: str, did: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._time_doc(rid, did)
        if body["laneId"] not in d["lanes"]:
            raise ApiError(404, "NOT_FOUND", "lane")
        event = {k: v for k, v in body.items() if k not in ("formattedStart", "formattedEnd")}
        event["id"] = body.get("id") or new_id()
        event["rank"] = self.next_rank()
        if "start" not in event:
            event["start"] = self._parse_date(body["formattedStart"])
        if body.get("formattedEnd"):
            event["end"] = self._parse_date(body["formattedEnd"])
        d["events"][event["id"]] = event
        return 201, self._event_out(event), {}

    def h_resources_x_docs_x_events_x_patch(self, rid: str, did: str, eid: str, *, body: dict[str, Any], **_: Any) -> Any:
        d = self._doc(rid, did)
        event = d["events"].get(eid)
        if event is None:
            raise ApiError(404, "NOT_FOUND", "event")
        update = {k: v for k, v in body.items() if k not in ("formattedStart", "formattedEnd")}
        if body.get("formattedStart"):
            update["start"] = self._parse_date(body["formattedStart"])
        if body.get("formattedEnd"):
            update["end"] = self._parse_date(body["formattedEnd"])
        event.update(update)
        return 200, self._event_out(event), {}

    def h_resources_x_docs_x_events_x_delete(self, rid: str, did: str, eid: str, **_: Any) -> Any:
        if self._doc(rid, did)["events"].pop(eid, None) is None:
            raise ApiError(404, "NOT_FOUND", "event")
        return 204, None, {}

    # boards ----------------------------------------------------------------------------------

    def h_resources_x_docs_x_board_get(self, rid: str, did: str, **_: Any) -> Any:
        d = self._doc(rid, did)
        board = d.get("board") or {"pages": [], "shapes": [], "bindings": [], "assets": []}
        return 200, {"schema": {"schemaVersion": 2, "sequences": {}}, **copy.deepcopy(board), "version": str(d["version"])}, {}

    # assets ----------------------------------------------------------------------------------

    def h_assets_get(self, *, params: dict[str, str], **_: Any) -> Any:
        items = [a for a in self.assets.values() if a.get("folderId") == params.get("folderId")]
        return 200, {"data": copy.deepcopy(items), "hasMore": False, "nextCursor": None}, {}

    def h_assets_uploads_post(self, *, body: dict[str, Any], **_: Any) -> Any:
        key = new_id()
        self.uploads[key] = {**body, "stored": False}
        ticket = {"uploadKey": key, "uploadUrl": f"{STORAGE}/upload/{key}", "method": "PUT",
                  "headers": {"content-type": body["contentType"]}, "expiresAt": NOW}  # fmt: skip
        return 200, ticket, {}

    def h_assets_images_post(self, *, body: dict[str, Any], **_: Any) -> Any:
        ticket = self.uploads.get(body["uploadKey"])
        if not ticket or not ticket["stored"]:
            raise ApiError(400, "VALIDATION_ERROR", "nothing uploaded")
        asset = {"id": new_id(), "name": body["name"], "type": "IMAGE", "folderId": body.get("folderId"),
                 "isHidden": False, "description": body.get("description"),
                 "url": f"{ASSET_HOST}/{body['uploadKey']}.img", "width": 800, "height": 600, "createdAt": NOW}  # fmt: skip
        self.assets[asset["id"]] = asset
        return 201, copy.deepcopy(asset), {}

    # misc ------------------------------------------------------------------------------------

    def h_tags_get(self, **_: Any) -> Any:
        if not self.admin:
            raise ApiError(403, "FORBIDDEN", "admins only")
        counts: dict[str, int] = {}
        for r in self.resources.values():
            if not r["deleted"]:
                for tag in r["tags"]:
                    counts[tag] = counts.get(tag, 0) + 1
        data = [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        return 200, {"data": data, "hasMore": False, "nextCursor": None}, {}

    def h_search_get(self, *, params: dict[str, str], **_: Any) -> Any:
        q = params.get("q", "").casefold()
        hits = []
        for r in self.resources.values():
            if r["deleted"] or not q:
                continue
            hit: dict[str, Any] = {"resource": {"id": r["id"], "name": r["name"], "tags": r["tags"], "parentId": r.get("parentId")}}
            if q in r["name"].casefold():
                hits.append(hit)
                continue
            for did in r["docIds"]:
                d = self.documents[did]
                text = self._blocks(d)["text"] if d["type"] in ("page", "blank") else ""
                pos = text.casefold().find(q)
                if pos >= 0:
                    hit["document"] = {"id": d["id"], "name": d["name"], "highlights": [
                        {"segment": text, "index": 0, "matchStart": pos, "matchEnd": pos + len(q)}]}  # fmt: skip
                    hits.append(hit)
                    break
        return 200, {"data": hits[: int(params.get("limit", 32))], "hasMore": False, "nextCursor": None}, {}

    def h_calendars_get(self, **_: Any) -> Any:
        return 200, {"data": [gregorian()], "hasMore": False, "nextCursor": None}, {}

    def h_members_get(self, **_: Any) -> Any:
        member = {"id": "user1", "name": "Finn", "email": "finn@example.com" if self.admin else None, "role": "OWNER", "image": None}
        return 200, {"data": [member], "hasMore": False, "nextCursor": None}, {}


_LITERALS = {
    "resources", "templates", "batch", "from-template", "save-as-template", "restore", "move", "docs", "blocks",
    "block-ids", "rows", "properties", "lanes", "events", "features", "board", "shapes", "delete", "assets",
    "uploads", "images", "members", "invites", "tags", "search", "calendars",
}  # fmt: skip


def _walk(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in blocks or []:
        out.append(b)
        out.extend(_walk(b.get("children") or []))
    return out


def _plain(runs: list[dict[str, Any]]) -> str:
    out = []
    for run in runs or []:
        if run.get("break"):
            out.append("\n")
        elif "mention" in run:
            out.append(run["mention"].get("text") or run["mention"].get("name") or "")
        else:
            out.append(run.get("text", ""))
    return "".join(out)


def _block_text(block: dict[str, Any]) -> str:
    if "content" in block:
        return _plain(block["content"])
    return " ".join(t for t in (_block_text(c) for c in block.get("children") or []) if t)
