"""Async HTTP client for the LegendKeeper v3 REST API."""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__
from .errors import LegendKeeperConnectionError, LegendKeeperError

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v3"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER = 60.0
_MAX_BACKOFF = 8.0


def seg(value: str) -> str:
    """Quote one path segment."""
    return quote(str(value), safe="")


@dataclass
class RateLimitState:
    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None


class LegendKeeperClient:
    """Thin, retrying wrapper around ``httpx.AsyncClient`` for one API key.

    * Bearer auth on every request.
    * Retries 429/5xx and connection failures with exponential backoff, honoring ``Retry-After``.
      ``POST`` requests always carry an ``Idempotency-Key`` so retries can never double-write.
    * Raises :class:`LegendKeeperError` with the API's ``code`` for every 4xx/5xx.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://www.legendkeeper.com",
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max(0, max_retries)
        self._sleep = sleep
        self.rate_limit = RateLimitState()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": f"legendkeeper-mcp/{__version__} (+https://github.com/chase-roohms/legendkeeper-mcp)",
            },
            follow_redirects=False,
        )
        # Uploads go to a signed storage URL and must not carry the API key.
        self._upload_http = httpx.AsyncClient(timeout=httpx.Timeout(max(timeout, 120.0)), transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._upload_http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Send a request and return the decoded JSON body (``None`` for 204)."""
        method = method.upper()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if if_match:
            headers["If-Match"] = if_match
        if method == "POST":
            headers["Idempotency-Key"] = idempotency_key or f"lkmcp-{secrets.token_hex(16)}"
        clean_params = {k: _param(v) for k, v in (params or {}).items() if v is not None}

        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method,
                    path,
                    params=clean_params or None,
                    json=json,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self._max_retries:
                    delay = _backoff(attempt)
                    logger.warning("%s %s failed (%s); retrying in %.1fs", method, path, exc, delay)
                    attempt += 1
                    await self._sleep(delay)
                    continue
                raise LegendKeeperConnectionError(
                    f"Could not reach LegendKeeper ({type(exc).__name__}: {exc})."
                ) from exc

            self._track_rate_limit(response)
            if response.status_code < 400:
                if response.status_code == 204 or not response.content:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return response.text

            error = _to_error(response)
            if response.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                delay = error.retry_after if error.retry_after is not None else _backoff(attempt)
                if delay <= _MAX_RETRY_AFTER:
                    logger.warning(
                        "%s %s -> %s %s; retrying in %.1fs", method, path, error.status, error.code, delay
                    )
                    attempt += 1
                    await self._sleep(delay)
                    continue
            raise error

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = 100,
        max_items: int | None = None,
    ) -> list[Any]:
        """Follow ``nextCursor`` until the collection is exhausted (or ``max_items`` is reached)."""
        items: list[Any] = []
        cursor: str | None = None
        query = dict(params or {})
        while True:
            page = await self.request("GET", path, params={**query, "limit": page_size, "cursor": cursor})
            if not isinstance(page, dict):
                return items
            items.extend(page.get("data") or [])
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            cursor = page.get("nextCursor")
            if not page.get("hasMore") or not cursor:
                return items

    async def upload(self, url: str, method: str, headers: dict[str, str], content: bytes) -> None:
        """Send raw bytes to a signed storage URL from ``createImageUpload``."""
        try:
            response = await self._upload_http.request(method, url, headers=headers, content=content)
        except httpx.HTTPError as exc:
            raise LegendKeeperConnectionError(f"Image upload failed: {exc}") from exc
        if response.status_code >= 400:
            raise LegendKeeperError(
                response.status_code,
                "UPLOAD_FAILED",
                f"Storage rejected the upload: {response.text[:300]}",
            )

    async def fetch_bytes(self, url: str, max_bytes: int) -> bytes:
        """Download a public file (e.g. an image to upload) without sending the API key."""
        chunks: list[bytes] = []
        size = 0
        try:
            async with self._upload_http.stream("GET", url, follow_redirects=True) as response:
                if response.status_code >= 400:
                    raise LegendKeeperConnectionError(
                        f"Download failed: HTTP {response.status_code} for {url}"
                    )
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise LegendKeeperConnectionError(
                            f"Download exceeds the {max_bytes}-byte upload limit."
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise LegendKeeperConnectionError(f"Download failed: {exc}") from exc
        return b"".join(chunks)

    def _track_rate_limit(self, response: httpx.Response) -> None:
        h = response.headers
        try:
            if "x-ratelimit-remaining" in h:
                self.rate_limit = RateLimitState(
                    limit=int(h.get("x-ratelimit-limit", "0")) or None,
                    remaining=int(h["x-ratelimit-remaining"]),
                    reset=int(h.get("x-ratelimit-reset", "0")) or None,
                )
        except ValueError:
            pass


class ProjectClient:
    """A :class:`LegendKeeperClient` bound to one project id; paths are project-relative."""

    def __init__(self, client: LegendKeeperClient, project_id: str) -> None:
        self.http = client
        self.project_id = project_id

    def path(self, *parts: str) -> str:
        base = f"{API_PREFIX}/projects/{seg(self.project_id)}"
        return base + "".join(f"/{seg(p)}" for p in parts)

    async def get(self, *parts: str, params: dict[str, Any] | None = None) -> Any:
        return await self.http.request("GET", self.path(*parts), params=params)

    async def post(
        self, *parts: str, json: Any = None, if_match: str | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.http.request("POST", self.path(*parts), json=json, if_match=if_match, params=params)

    async def put(self, *parts: str, json: Any = None, if_match: str | None = None) -> Any:
        return await self.http.request("PUT", self.path(*parts), json=json, if_match=if_match)

    async def patch(self, *parts: str, json: Any = None, if_match: str | None = None) -> Any:
        return await self.http.request("PATCH", self.path(*parts), json=json, if_match=if_match)

    async def delete(
        self, *parts: str, params: dict[str, Any] | None = None, if_match: str | None = None
    ) -> Any:
        return await self.http.request("DELETE", self.path(*parts), params=params, if_match=if_match)

    async def list_all(
        self, *parts: str, params: dict[str, Any] | None = None, max_items: int | None = None
    ) -> list[Any]:
        return await self.http.paginate(self.path(*parts), params, max_items=max_items)


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _backoff(attempt: int) -> float:
    base: float = min(_MAX_BACKOFF, 0.5 * (2**attempt))
    return base * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not crypto


def _to_error(response: httpx.Response) -> LegendKeeperError:
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    retry_after: float | None = None
    raw_retry = response.headers.get("retry-after")
    if raw_retry:
        try:
            retry_after = max(0.0, float(raw_retry))
        except ValueError:
            retry_after = None
    elif response.status_code == 429 and response.headers.get("x-ratelimit-reset"):
        try:
            retry_after = max(0.0, float(response.headers["x-ratelimit-reset"]) - time.time())
        except ValueError:
            retry_after = None
    default_message = response.reason_phrase or "Request failed"
    return LegendKeeperError(
        response.status_code,
        str(body.get("code") or f"HTTP_{response.status_code}"),
        str(body.get("message") or (response.text[:300] if not body else default_message)),
        request_id=body.get("requestId") or response.headers.get("x-request-id"),
        errors=body.get("errors") if isinstance(body.get("errors"), list) else None,
        retry_after=retry_after,
    )
