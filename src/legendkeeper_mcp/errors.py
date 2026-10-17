"""Error types raised by the LegendKeeper client and the conversion layer."""

from __future__ import annotations

from typing import Any

# Actionable hints for the error codes an assistant is most likely to hit.
_HINTS: dict[str, str] = {
    "UNAUTHENTICATED": "The API key is missing, unknown, revoked or expired. Create a new key under "
    "Project Settings -> Developers -> API Keys.",
    "PAID_PLAN_REQUIRED": "LegendKeeper API keys only work while the project owner is on a paid plan "
    "(a trial does not count).",
    "SUBSCRIPTION_LAPSED": "The project owner's plan does not currently allow writes.",
    "FORBIDDEN": "The key's role is too low for this operation. Some operations (hidden resources, "
    "tags, templates, member emails) need an ADMIN key.",
    "WRONG_PROJECT": "API keys only work inside their own project.",
    "VERSION_MISMATCH": "The document changed since it was read. Read it again and retry.",
    "HAS_CHILDREN": "The resource has children. Pass cascade=true to delete them too.",
    "KIND_MISMATCH": "The target document is a different type (page/map/time/board) than this "
    "operation needs.",
    "RATE_LIMITED": "LegendKeeper's rate limit was reached. Wait and retry later.",
    "NOT_FOUND": "Check the id. Use search or browse_tree to find valid ids.",
    "FEATURE_UNAVAILABLE": "LegendKeeper has not enabled this field yet; retry without it.",
}


class LegendKeeperMCPError(Exception):
    """Base error for this package."""


class LegendKeeperError(LegendKeeperMCPError):
    """An error response from the LegendKeeper API."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.errors = errors or []
        self.retry_after = retry_after
        super().__init__(self.describe())

    @property
    def hint(self) -> str | None:
        return _HINTS.get(self.code)

    def describe(self) -> str:
        parts = [f"LegendKeeper API error {self.status} {self.code}: {self.message}"]
        for item in self.errors[:10]:
            path = item.get("path") or "(body)"
            parts.append(f"  - {path}: {item.get('message', '')}")
        if len(self.errors) > 10:
            parts.append(f"  … and {len(self.errors) - 10} more")
        if self.hint:
            parts.append(f"Hint: {self.hint}")
        if self.request_id:
            parts.append(f"(request id {self.request_id})")
        return "\n".join(parts)


class LegendKeeperConnectionError(LegendKeeperMCPError):
    """The API could not be reached (network failure or timeout)."""


class ConversionError(LegendKeeperMCPError):
    """Markdown could not be converted to LegendKeeper blocks."""


class InputError(LegendKeeperMCPError):
    """A tool was called with arguments that cannot work."""
