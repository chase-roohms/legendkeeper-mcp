"""Runtime configuration, read from environment variables.

Every LegendKeeper API key is scoped to a single project (world). One server can
serve several projects by giving each key a name:

* ``LEGENDKEEPER_API_KEY``          -> project named ``default``
* ``LEGENDKEEPER_API_KEY_<NAME>``   -> project named ``<name>`` (lower-cased)

Any of these may instead be supplied as a file path via the ``_FILE`` suffix
(``LEGENDKEEPER_API_KEY_FILE``, ``LEGENDKEEPER_API_KEY_<NAME>_FILE``), which suits
Docker secrets.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DEFAULT_BASE_URL = "https://www.legendkeeper.com"
KEY_PREFIX = "LEGENDKEEPER_API_KEY"
PROJECT_ID_PREFIX = "LEGENDKEEPER_PROJECT_ID"
DEFAULT_PROJECT_NAME = "default"

Transport = Literal["stdio", "http"]

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n", ""}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Raised when the environment does not describe a usable configuration."""


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    api_key: str = field(repr=False)
    project_id: str | None = None

    @property
    def masked_key(self) -> str:
        key = self.api_key
        return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else "****"


@dataclass(frozen=True)
class Settings:
    projects: Mapping[str, ProjectConfig]
    default_project: str
    read_only: bool = False
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 30.0
    max_retries: int = 3
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    http_path: str = "/mcp"
    auth_token: str | None = field(default=None, repr=False)
    allow_unauthenticated: bool = False
    allowed_hosts: tuple[str, ...] = ()
    upload_root: Path | None = None
    max_upload_bytes: int = 25 * 1024 * 1024
    allow_url_uploads: bool = True
    log_level: str = "INFO"

    def project(self, name: str | None) -> ProjectConfig:
        key = (name or self.default_project).strip().lower()
        try:
            return self.projects[key]
        except KeyError:
            known = ", ".join(sorted(self.projects)) or "(none)"
            raise ConfigError(f"Unknown project '{name}'. Configured projects: {known}.") from None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, require_keys: bool = True) -> Settings:
        env = dict(os.environ if env is None else env)

        projects = _discover_projects(env)
        if require_keys and not projects:
            raise ConfigError(
                "No LegendKeeper API key configured. Set LEGENDKEEPER_API_KEY (or "
                "LEGENDKEEPER_API_KEY_<NAME> for several projects). Create a key in LegendKeeper under "
                "Project Settings -> Developers -> API Keys."
            )

        default_project = (env.get("LEGENDKEEPER_DEFAULT_PROJECT") or "").strip().lower()
        if not default_project:
            if DEFAULT_PROJECT_NAME in projects or not projects:
                default_project = DEFAULT_PROJECT_NAME
            else:
                default_project = sorted(projects)[0]
        elif projects and default_project not in projects:
            raise ConfigError(
                f"LEGENDKEEPER_DEFAULT_PROJECT='{default_project}' does not match a configured key "
                f"({', '.join(sorted(projects))})."
            )

        transport = (env.get("MCP_TRANSPORT") or "stdio").strip().lower()
        if transport in {"streamable-http", "streamable_http", "https"}:
            transport = "http"
        if transport not in {"stdio", "http"}:
            raise ConfigError(f"MCP_TRANSPORT must be 'stdio' or 'http', got '{transport}'.")

        http_path = env.get("MCP_PATH", "/mcp").strip() or "/mcp"
        if not http_path.startswith("/"):
            http_path = "/" + http_path

        upload_root_raw = (env.get("LEGENDKEEPER_UPLOAD_ROOT") or "").strip()
        allowed_hosts = tuple(h.strip() for h in env.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip())

        return cls(
            projects=projects,
            default_project=default_project,
            read_only=_bool(env, "LEGENDKEEPER_READ_ONLY", False),
            base_url=(env.get("LEGENDKEEPER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            timeout=_float(env, "LEGENDKEEPER_TIMEOUT", 30.0),
            max_retries=_int(env, "LEGENDKEEPER_MAX_RETRIES", 3),
            transport=transport,  # type: ignore[arg-type]
            host=env.get("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int(env, "MCP_PORT", 8000),
            http_path=http_path,
            auth_token=_secret(env, "MCP_AUTH_TOKEN"),
            allow_unauthenticated=_bool(env, "MCP_ALLOW_UNAUTHENTICATED", False),
            allowed_hosts=allowed_hosts,
            upload_root=Path(upload_root_raw).expanduser().resolve() if upload_root_raw else None,
            max_upload_bytes=_int(env, "LEGENDKEEPER_MAX_UPLOAD_MB", 25) * 1024 * 1024,
            allow_url_uploads=_bool(env, "LEGENDKEEPER_ALLOW_URL_UPLOADS", True),
            log_level=(env.get("LEGENDKEEPER_LOG_LEVEL") or "INFO").upper(),
        )


def _discover_projects(env: Mapping[str, str]) -> dict[str, ProjectConfig]:
    keys: dict[str, str] = {}
    for var in sorted(env):
        if var != KEY_PREFIX and not var.startswith(KEY_PREFIX + "_"):
            continue
        suffix = var[len(KEY_PREFIX) :].lstrip("_")
        from_file = suffix == "FILE" or suffix.endswith("_FILE")
        if from_file:
            suffix = suffix[: -len("FILE")].rstrip("_")
        name = suffix.lower() if suffix else DEFAULT_PROJECT_NAME
        if not _NAME_RE.match(name):
            raise ConfigError(f"Invalid project name derived from {var}: '{name}'.")
        raw = env[var]
        value = _read_secret_file(raw, var) if from_file else raw.strip()
        if not value:
            continue
        if name in keys and keys[name] != value:
            raise ConfigError(f"Project '{name}' has two different API keys configured ({var}).")
        keys[name] = value

    projects: dict[str, ProjectConfig] = {}
    for name, key in keys.items():
        id_var = PROJECT_ID_PREFIX if name == DEFAULT_PROJECT_NAME else f"{PROJECT_ID_PREFIX}_{name.upper()}"
        project_id = (env.get(id_var) or "").strip() or None
        projects[name] = ProjectConfig(name=name, api_key=key, project_id=project_id)
    return projects


def _read_secret_file(path: str, var: str) -> str:
    try:
        return Path(path.strip()).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"Could not read {var} ({path}): {exc.strerror}") from exc


def _secret(env: Mapping[str, str], var: str) -> str | None:
    if env.get(f"{var}_FILE"):
        return _read_secret_file(env[f"{var}_FILE"], f"{var}_FILE") or None
    return (env.get(var) or "").strip() or None


def _bool(env: Mapping[str, str], var: str, default: bool) -> bool:
    raw = env.get(var)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{var} must be a boolean (true/false), got '{raw}'.")


def _int(env: Mapping[str, str], var: str, default: int) -> int:
    raw = (env.get(var) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{var} must be an integer, got '{raw}'.") from None


def _float(env: Mapping[str, str], var: str, default: float) -> float:
    raw = (env.get(var) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{var} must be a number, got '{raw}'.") from None
