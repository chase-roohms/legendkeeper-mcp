from __future__ import annotations

import pytest

from legendkeeper_mcp import __version__
from legendkeeper_mcp.cli import check, main
from legendkeeper_mcp.config import Settings

from .fake_lk import API_KEY


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_missing_configuration_exits_2(monkeypatch, capsys):
    for var in list(__import__("os").environ):
        if var.startswith(("LEGENDKEEPER_", "MCP_")):
            monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as info:
        main(["serve"])
    assert info.value.code == 2
    assert "No LegendKeeper API key" in capsys.readouterr().err


def test_http_without_token_on_public_host_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("LEGENDKEEPER_API_KEY", "k")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as info:
        main(["--transport", "http", "--host", "0.0.0.0"])
    assert info.value.code == 2
    assert "MCP_AUTH_TOKEN" in capsys.readouterr().err


async def test_check_reports_projects(fake, capsys):
    settings = Settings.from_env({"LEGENDKEEPER_API_KEY": API_KEY, "LEGENDKEEPER_API_KEY_BROKEN": "bad"})
    assert await check(settings, transport=fake.transport()) == 1
    out = capsys.readouterr().out
    assert (
        "OK   [default (default)] key" in out
        and "project 'Land of Ooo'" in out
        and "rate limit 299/300" in out
    )
    assert "FAIL [broken]" in out and "UNAUTHENTICATED" in out

    ok = Settings.from_env({"LEGENDKEEPER_API_KEY": API_KEY})
    assert await check(ok, transport=fake.transport()) == 0
