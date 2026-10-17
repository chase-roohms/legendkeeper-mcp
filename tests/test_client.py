from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from legendkeeper_mcp.client import LegendKeeperClient, ProjectClient
from legendkeeper_mcp.errors import LegendKeeperConnectionError, LegendKeeperError


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(recorder: Recorder, **kwargs: Any) -> tuple[LegendKeeperClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = LegendKeeperClient(
        "lk_secret",
        base_url="https://lk.test",
        transport=httpx.MockTransport(recorder),
        sleep=fake_sleep,
        **kwargs,
    )
    return client, sleeps


def error(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"message": f"{code} happened", "code": code, "requestId": "r1"}, headers=headers
    )


async def test_auth_header_and_json():
    rec = Recorder(httpx.Response(200, json={"ok": True}))
    client, _ = make(rec)
    assert await client.request("GET", "/api/v3/x", params={"a": True, "b": None, "c": 2}) == {"ok": True}
    req = rec.requests[0]
    assert req.headers["authorization"] == "Bearer lk_secret"
    assert "legendkeeper-mcp/" in req.headers["user-agent"]
    assert dict(req.url.params) == {"a": "true", "c": "2"}
    assert "idempotency-key" not in req.headers
    await client.aclose()


async def test_post_gets_stable_idempotency_key_across_retries():
    rec = Recorder(error(503, "INTERNAL"), httpx.Response(201, json={"id": "x"}))
    client, sleeps = make(rec)
    assert await client.request("POST", "/p", json={"name": "a"}) == {"id": "x"}
    keys = {r.headers["idempotency-key"] for r in rec.requests}
    assert len(keys) == 1
    assert len(sleeps) == 1
    assert json.loads(rec.requests[0].content) == {"name": "a"}


async def test_if_match_header():
    rec = Recorder(httpx.Response(204))
    client, _ = make(rec)
    assert await client.request("PUT", "/p", json={}, if_match="7") is None
    assert rec.requests[0].headers["if-match"] == "7"


async def test_retry_after_is_honored_for_429():
    rec = Recorder(error(429, "RATE_LIMITED", **{"retry-after": "3"}), httpx.Response(200, json=[]))
    client, sleeps = make(rec)
    assert await client.request("GET", "/p") == []
    assert sleeps == [3.0]


async def test_long_retry_after_is_not_waited_for():
    rec = Recorder(error(429, "RATE_LIMITED", **{"retry-after": "3600"}))
    client, sleeps = make(rec)
    with pytest.raises(LegendKeeperError) as info:
        await client.request("GET", "/p")
    assert info.value.code == "RATE_LIMITED" and info.value.retry_after == 3600
    assert sleeps == []


async def test_gives_up_after_max_retries():
    rec = Recorder(error(502, "BAD"), error(502, "BAD"), error(502, "BAD"))
    client, sleeps = make(rec, max_retries=2)
    with pytest.raises(LegendKeeperError):
        await client.request("GET", "/p")
    assert len(rec.requests) == 3 and len(sleeps) == 2


async def test_client_errors_are_not_retried_and_are_described():
    body = {
        "message": "Invalid body", "code": "VALIDATION_ERROR", "requestId": "abc",
        "errors": [{"path": "name", "message": "Required"}],
    }  # fmt: skip
    rec = Recorder(httpx.Response(400, json=body))
    client, sleeps = make(rec)
    with pytest.raises(LegendKeeperError) as info:
        await client.request("POST", "/p", json={})
    text = str(info.value)
    assert "400 VALIDATION_ERROR: Invalid body" in text
    assert "- name: Required" in text and "request id abc" in text
    assert not sleeps


async def test_hints_for_known_codes():
    rec = Recorder(error(402, "PAID_PLAN_REQUIRED"))
    client, _ = make(rec)
    with pytest.raises(LegendKeeperError) as info:
        await client.request("GET", "/p")
    assert "paid plan" in str(info.value)


async def test_non_json_error_body():
    rec = Recorder(httpx.Response(500, text="<html>oops</html>"))
    client, _ = make(rec, max_retries=0)
    with pytest.raises(LegendKeeperError) as info:
        await client.request("GET", "/p")
    assert info.value.code == "HTTP_500" and "oops" in info.value.message


async def test_connection_errors_retry_then_raise():
    rec = Recorder(httpx.ConnectError("down"), httpx.ConnectError("down"))
    client, sleeps = make(rec, max_retries=1)
    with pytest.raises(LegendKeeperConnectionError):
        await client.request("GET", "/p")
    assert len(sleeps) == 1


async def test_rate_limit_headers_tracked():
    rec = Recorder(
        httpx.Response(
            200,
            json={},
            headers={"x-ratelimit-limit": "300", "x-ratelimit-remaining": "12", "x-ratelimit-reset": "99"},
        )
    )
    client, _ = make(rec)
    await client.request("GET", "/p")
    assert client.rate_limit.remaining == 12 and client.rate_limit.limit == 300


async def test_paginate_follows_cursors():
    rec = Recorder(
        httpx.Response(200, json={"data": [1, 2], "hasMore": True, "nextCursor": "c2"}),
        httpx.Response(200, json={"data": [3], "hasMore": False, "nextCursor": None}),
    )
    client, _ = make(rec)
    assert await client.paginate("/items", {"folderId": "f"}) == [1, 2, 3]
    assert dict(rec.requests[1].url.params) == {"folderId": "f", "limit": "100", "cursor": "c2"}


async def test_paginate_max_items():
    rec = Recorder(httpx.Response(200, json={"data": [1, 2, 3], "hasMore": True, "nextCursor": "c"}))
    client, _ = make(rec)
    assert await client.paginate("/items", max_items=2) == [1, 2]


async def test_upload_does_not_send_api_key():
    rec = Recorder(httpx.Response(200))
    client, _ = make(rec)
    await client.upload("https://storage.test/u", "PUT", {"content-type": "image/png"}, b"data")
    assert "authorization" not in rec.requests[0].headers
    rec2 = Recorder(httpx.Response(403, text="denied"))
    client2, _ = make(rec2)
    with pytest.raises(LegendKeeperError):
        await client2.upload("https://storage.test/u", "PUT", {}, b"data")


async def test_fetch_bytes_enforces_limit():
    rec = Recorder(
        httpx.Response(200, content=b"x" * 10), httpx.Response(200, content=b"x" * 100), httpx.Response(404)
    )
    client, _ = make(rec)
    assert await client.fetch_bytes("https://img.test/a.png", 50) == b"x" * 10
    with pytest.raises(LegendKeeperConnectionError, match="limit"):
        await client.fetch_bytes("https://img.test/b.png", 50)
    with pytest.raises(LegendKeeperConnectionError, match="404"):
        await client.fetch_bytes("https://img.test/c.png", 50)


def test_project_paths_are_quoted():
    client = LegendKeeperClient("k")
    api = ProjectClient(client, "proj/1")
    assert api.path("resources", "a b", "docs") == "/api/v3/projects/proj%2F1/resources/a%20b/docs"


async def test_api_paths_get_trailing_slash_and_redirects_are_followed():
    rec = Recorder(
        httpx.Response(308, headers={"location": "/api/v3/projects/x/tags/"}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"ok": 1}),
    )
    client, _ = make(rec)
    assert await client.request("GET", "/api/v3/projects/x/tags") == {"data": []}
    assert rec.requests[0].url.path == "/api/v3/projects/x/tags/"
    assert rec.requests[1].headers["authorization"] == "Bearer lk_secret"
    await client.request("GET", "/api/v3/projects/x/")
    assert rec.requests[2].url.path == "/api/v3/projects/x/"


async def test_non_json_success_is_an_error():
    rec = Recorder(httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}))
    client, _ = make(rec)
    with pytest.raises(LegendKeeperError, match="instead of JSON"):
        await client.request("GET", "/api/v3/projects")
