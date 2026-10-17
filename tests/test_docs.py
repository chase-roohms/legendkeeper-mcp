"""Keep documentation and examples in sync with the code."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from legendkeeper_mcp import __version__
from legendkeeper_mcp.config import Settings
from legendkeeper_mcp.server import create_server

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", sorted((ROOT / "examples").rglob("*.json")), ids=lambda p: p.name)
def test_example_configs_are_valid_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or data.get("servers")
    assert servers and "legendkeeper" in servers


def test_every_env_var_is_documented():
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    used = set(re.findall(r'"((?:LEGENDKEEPER|MCP)_[A-Z_]+)"', source))
    used -= {"LEGENDKEEPER_API_KEY_"}  # prefix used for discovery
    missing = sorted(var for var in used if f"`{var}" not in README)
    assert not missing, f"Document these variables in README.md: {missing}"


def test_every_tool_is_documented():
    server, _ = create_server(Settings.from_env({"LEGENDKEEPER_API_KEY": "k"}))
    names = {t.name for t in asyncio.run(server.list_tools())}
    missing = sorted(name for name in names if f"`{name}`" not in README)
    assert not missing, f"Document these tools in README.md: {missing}"
    assert f"**{len(names)} curated tools**" in README


def test_versions_agree():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject
    assert f"## [{__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_server_json_matches_package():
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    name = server["name"]
    assert len(server["description"]) <= 100  # registry limit
    assert server["version"] == __version__
    pypi = next(p for p in server["packages"] if p["registryType"] == "pypi")
    oci = next(p for p in server["packages"] if p["registryType"] == "oci")
    assert pypi["identifier"] == "legendkeeper-mcp" and pypi["version"] == __version__
    assert oci["identifier"].endswith(f":{__version__}")
    assert f"<!-- mcp-name: {name} -->" in README  # PyPI ownership proof
    assert f'io.modelcontextprotocol.server.name="{name}"' in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert {"LEGENDKEEPER_API_KEY"} <= {e["name"] for e in pypi["environmentVariables"]}
