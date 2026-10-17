"""OpenAPI-driven contract checks for the fake LegendKeeper API used in tests."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_URL = "https://www.legendkeeper.com/api/v3/openapi.json"
CACHE = Path(__file__).parent / ".cache" / "openapi.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


def load_spec() -> dict[str, Any] | None:
    """Load the official OpenAPI spec (cached under tests/.cache). Returns None when offline."""
    override = os.environ.get("LEGENDKEEPER_OPENAPI_PATH")
    if override:
        return json.loads(Path(override).read_text(encoding="utf-8"))
    if CACHE.exists() and not os.environ.get("LEGENDKEEPER_REFRESH_SPEC"):
        return json.loads(CACHE.read_text(encoding="utf-8"))
    try:
        response = httpx.get(SPEC_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        raw = response.content
    except httpx.HTTPError:
        return None
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(raw)
    return json.loads(raw)


def _pointer(*parts: str) -> str:
    return "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts)


@dataclass
class Operation:
    method: str
    template: str
    operation_id: str
    regex: re.Pattern[str]
    request_pointer: str | None
    response_pointers: dict[str, str]


class SpecValidator:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.registry = Registry().with_resource("urn:lk", Resource(contents=spec, specification=DRAFT202012))
        self.operations: list[Operation] = []
        for template, item in spec["paths"].items():
            regex = re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$")
            for method, op in item.items():
                if method not in _METHODS:
                    continue
                request = None
                body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
                if body and "schema" in body:
                    request = _pointer("paths", template, method, "requestBody", "content", "application/json", "schema")
                responses = {}
                for status, response in (op.get("responses") or {}).items():
                    content = (response.get("content") or {}).get("application/json")
                    if content and "schema" in content:
                        responses[status] = _pointer(
                            "paths", template, method, "responses", status, "content", "application/json", "schema"
                        )
                self.operations.append(
                    Operation(method.upper(), template, op.get("operationId", ""), regex, request, responses)
                )
        # Literal segments must win over templated ones (e.g. /resources/batch vs /resources/{id}).
        self.operations.sort(key=lambda o: o.template.count("{"))
        self._validators: dict[str, Draft202012Validator] = {}

    def find(self, method: str, path: str) -> Operation | None:
        for op in self.operations:
            if op.method == method and op.regex.match(path):
                return op
        return None

    def _validator(self, pointer: str) -> Draft202012Validator:
        if pointer not in self._validators:
            self._validators[pointer] = Draft202012Validator({"$ref": f"urn:lk#{pointer}"}, registry=self.registry)
        return self._validators[pointer]

    def errors(self, pointer: str, instance: Any) -> list[str]:
        validator = self._validator(pointer)
        return [
            f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:300]}"
            for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        ][:10]

    def schema_errors(self, schema_name: str, instance: Any) -> list[str]:
        return self.errors(_pointer("components", "schemas", schema_name), instance)
