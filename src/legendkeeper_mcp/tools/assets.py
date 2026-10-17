"""Image upload tool."""

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

from pydantic import Field

from ..client import LegendKeeperClient
from ..errors import InputError
from .common import WRITE, ProjectArg, ToolContext, compact, dump

_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def sniff_image_type(data: bytes) -> str | None:
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_local(source: str, root: Path | None, max_bytes: int) -> tuple[bytes, str]:
    path = Path(source).expanduser().resolve()
    if root is not None and not path.is_relative_to(root):
        raise InputError(f"Uploads are limited to files under {root}.")
    if not path.is_file():
        raise InputError(f"File not found: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise InputError(f"File is {size} bytes; the limit is {max_bytes}.")
    return path.read_bytes(), path.stem


def register(t: ToolContext) -> None:
    registry = t.registry
    settings = t.settings

    async def load(source: str, client: LegendKeeperClient) -> tuple[bytes, str]:
        parsed = urlparse(source)
        if parsed.scheme in ("http", "https"):
            if not settings.allow_url_uploads:
                raise InputError("Uploading from URLs is disabled (LEGENDKEEPER_ALLOW_URL_UPLOADS=false).")
            data = await client.fetch_bytes(source, settings.max_upload_bytes)
            return data, Path(unquote(parsed.path)).stem or "image"
        # A one-letter "scheme" is a Windows drive letter (C:\...), i.e. a local path.
        if parsed.scheme not in ("", "file") and len(parsed.scheme) != 1:
            raise InputError("`source` must be a local file path or an http(s) URL.")
        local = unquote(parsed.path) if parsed.scheme == "file" else source
        return await asyncio.to_thread(_read_local, local, settings.upload_root, settings.max_upload_bytes)

    @t.tool(annotations=WRITE, write=True)
    async def upload_image(
        source: Annotated[
            str,
            Field(
                description="Local file path (as seen by this server) or an http(s) URL of a JPEG/PNG/GIF/WebP."
            ),
        ],
        name: Annotated[
            str | None, Field(max_length=200, description="Asset name (default: file name).")
        ] = None,
        description: Annotated[str | None, Field(max_length=10000)] = None,
        folder_id: Annotated[str | None, Field(description="Asset folder id (see list_assets).")] = None,
        project: ProjectArg = None,
    ) -> str:
        """Upload an image to the world's asset library and return its asset id and URL. Use the URL in a page
        image (![alt](url)), a resource banner (update_resource banner_url) or as a map image
        (create_document / update_document map_image_asset_id)."""
        session = registry.session(project)
        api = await session.api()
        data, default_name = await load(source.strip(), session.client)
        if len(data) > settings.max_upload_bytes:
            raise InputError(f"Image is {len(data)} bytes; the limit is {settings.max_upload_bytes}.")
        mime = sniff_image_type(data)
        if mime is None:
            raise InputError("Not a JPEG, PNG, GIF or WebP image.")
        ticket = await api.post(
            "assets",
            "uploads",
            json={
                "contentType": mime,
                "contentLength": len(data),
                "contentHash": hashlib.sha256(data).hexdigest(),
            },
        )
        await session.client.upload(
            ticket["uploadUrl"], ticket.get("method", "PUT"), ticket.get("headers") or {}, data
        )
        body = compact(
            {
                "uploadKey": ticket["uploadKey"],
                "name": (name or default_name or "image")[:200],
                "description": description,
                "folderId": folder_id,
            }
        )
        asset = await api.post("assets", "images", json=body)
        return dump(
            {k: asset.get(k) for k in ("id", "name", "type", "url", "width", "height", "folderId")}
            | {"usage": f"Page image: ![{asset.get('name')}]({asset.get('url')})"}
        )
