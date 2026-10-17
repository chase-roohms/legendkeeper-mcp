from __future__ import annotations

from pathlib import Path

import pytest

from legendkeeper_mcp.config import ConfigError, Settings


def test_single_key_defaults():
    s = Settings.from_env({"LEGENDKEEPER_API_KEY": " lk_abcdefghijklmnop "})
    assert list(s.projects) == ["default"]
    assert s.default_project == "default"
    assert s.project(None).api_key == "lk_abcdefghijklmnop"
    assert s.project(None).masked_key == "lk_ab…mnop"
    assert "lk_abcdefghijklmnop" not in repr(s)
    assert s.transport == "stdio" and s.read_only is False and s.host == "127.0.0.1"
    assert s.base_url == "https://www.legendkeeper.com"


def test_named_keys_and_default_selection():
    env = {"LEGENDKEEPER_API_KEY_HOMEBREW": "k1", "LEGENDKEEPER_API_KEY_CURSE_OF_STRAHD": "k2"}
    s = Settings.from_env(env)
    assert set(s.projects) == {"homebrew", "curse_of_strahd"}
    assert s.default_project == "curse_of_strahd"
    s = Settings.from_env({**env, "LEGENDKEEPER_DEFAULT_PROJECT": "Homebrew"})
    assert s.default_project == "homebrew"
    assert s.project("HOMEBREW").api_key == "k1"
    with pytest.raises(ConfigError, match="Unknown project"):
        s.project("nope")
    with pytest.raises(ConfigError, match="does not match"):
        Settings.from_env({**env, "LEGENDKEEPER_DEFAULT_PROJECT": "nope"})


def test_project_ids():
    s = Settings.from_env(
        {"LEGENDKEEPER_API_KEY": "a", "LEGENDKEEPER_PROJECT_ID": "p1", "LEGENDKEEPER_API_KEY_X": "b",
         "LEGENDKEEPER_PROJECT_ID_X": "p2"}
    )  # fmt: skip
    assert s.project(None).project_id == "p1"
    assert s.project("x").project_id == "p2"


def test_key_files(tmp_path: Path):
    key = tmp_path / "key"
    key.write_text("lk_from_file\n")
    token = tmp_path / "token"
    token.write_text("secret-token")
    s = Settings.from_env(
        {"LEGENDKEEPER_API_KEY_FILE": str(key), "LEGENDKEEPER_API_KEY_WORLD_FILE": str(key),
         "MCP_AUTH_TOKEN_FILE": str(token)}
    )  # fmt: skip
    assert s.project(None).api_key == "lk_from_file"
    assert s.project("world").api_key == "lk_from_file"
    assert s.auth_token == "secret-token"
    with pytest.raises(ConfigError, match="Could not read"):
        Settings.from_env({"LEGENDKEEPER_API_KEY_FILE": str(tmp_path / "missing")})


def test_conflicting_keys():
    with pytest.raises(ConfigError, match="two different API keys"):
        Settings.from_env({"LEGENDKEEPER_API_KEY": "a", "LEGENDKEEPER_API_KEY_DEFAULT": "b"})


def test_requires_a_key():
    with pytest.raises(ConfigError, match="No LegendKeeper API key"):
        Settings.from_env({})
    assert Settings.from_env({}, require_keys=False).projects == {}


def test_http_and_misc_settings(tmp_path: Path):
    s = Settings.from_env(
        {
            "LEGENDKEEPER_API_KEY": "k", "MCP_TRANSPORT": "streamable-http", "MCP_HOST": "0.0.0.0", "MCP_PORT": "9000",
            "MCP_PATH": "lk", "MCP_AUTH_TOKEN": "t", "MCP_ALLOWED_HOSTS": "a.local, b.local:8000",
            "LEGENDKEEPER_READ_ONLY": "yes", "LEGENDKEEPER_UPLOAD_ROOT": str(tmp_path), "LEGENDKEEPER_MAX_UPLOAD_MB": "5",
            "LEGENDKEEPER_BASE_URL": "https://example.test/", "LEGENDKEEPER_TIMEOUT": "12.5",
        }
    )  # fmt: skip
    assert s.transport == "http" and s.host == "0.0.0.0" and s.port == 9000 and s.http_path == "/lk"
    assert s.allowed_hosts == ("a.local", "b.local:8000")
    assert s.read_only and s.upload_root == tmp_path.resolve() and s.max_upload_bytes == 5 * 1024 * 1024
    assert s.base_url == "https://example.test" and s.timeout == 12.5


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("MCP_TRANSPORT", "carrier-pigeon", "MCP_TRANSPORT"),
        ("LEGENDKEEPER_READ_ONLY", "maybe", "boolean"),
        ("MCP_PORT", "eighty", "integer"),
        ("LEGENDKEEPER_TIMEOUT", "soon", "number"),
        ("LEGENDKEEPER_API_KEY_BAD$NAME", "k", "Invalid project name"),
    ],
)
def test_invalid_values(var: str, value: str, message: str):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({"LEGENDKEEPER_API_KEY": "k", var: value})
