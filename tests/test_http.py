from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from legendkeeper_mcp.config import ConfigError, Settings
from legendkeeper_mcp.http_app import build_http_app, check_http_security
from legendkeeper_mcp.server import create_server

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def app_for(**env: str):
    settings = Settings.from_env({"LEGENDKEEPER_API_KEY": "k", "MCP_TRANSPORT": "http", **env})
    server, _ = create_server(settings)
    return build_http_app(server, settings)


def test_requires_token_when_exposed():
    with pytest.raises(ConfigError, match="MCP_AUTH_TOKEN"):
        check_http_security(Settings.from_env({"LEGENDKEEPER_API_KEY": "k", "MCP_HOST": "0.0.0.0"}))
    check_http_security(Settings.from_env({"LEGENDKEEPER_API_KEY": "k"}))  # loopback is fine
    check_http_security(
        Settings.from_env(
            {"LEGENDKEEPER_API_KEY": "k", "MCP_HOST": "0.0.0.0", "MCP_ALLOW_UNAUTHENTICATED": "1"}
        )
    )


def test_bearer_auth_and_health():
    app = app_for(MCP_AUTH_TOKEN="s3cret", MCP_HOST="0.0.0.0", MCP_ALLOWED_HOSTS="testserver")
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 401
        bad = client.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"})
        assert bad.status_code == 401 and bad.headers["www-authenticate"].startswith("Bearer")
        ok = client.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer s3cret"})
        assert ok.status_code == 200, ok.text
        assert "legendkeeper" in ok.text


def test_loopback_without_token():
    with TestClient(app_for(), base_url="http://127.0.0.1:8000") as client:
        ok = client.post("/mcp", json=INIT, headers={**HEADERS, "Host": "127.0.0.1:8000"})
        assert ok.status_code == 200, ok.text
