"""Property-based round-trip tests for the inline Markdown writer/parser."""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from legendkeeper_mcp.markdown import parse_markdown, runs_to_markdown
from legendkeeper_mcp.markdown.inline import normalize_runs

ALPHABET = st.sampled_from([*list("abcXYZ 019*_`[]()<>#!~|&;:.-+=\\\"'/"), "é", "→"])
TEXT = st.text(ALPHABET, min_size=1, max_size=12)
MARKS = st.fixed_dictionaries(
    {},
    optional={
        "bold": st.just(True),
        "italic": st.just(True),
        "strike": st.just(True),
        "underline": st.just(True),
        "sup": st.just(True),
        "link": st.sampled_from(["https://example.com/a", "https://x.y/(p) q"]),
        "color": st.sampled_from(["#FF0000", "#00aa00"]),
    },
)


@st.composite
def runs(draw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _ in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from(["text", "text", "text", "code", "mention", "break"]))
        if kind == "text":
            out.append({"text": draw(TEXT), **draw(MARKS)})
        elif kind == "code":
            out.append({"text": draw(TEXT), "code": True})
        elif kind == "mention":
            out.append({"mention": {"resourceId": "res00001", "text": draw(TEXT)}})
        else:
            out.append({"break": True})
    return out


def canonical(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize whitespace the way Markdown does (edges of a paragraph and around breaks are trimmed)."""
    rs = normalize_runs([dict(r) for r in rs])
    while rs and rs[0].get("break"):
        rs.pop(0)
    while rs and rs[-1].get("break"):
        rs.pop()
    return rs


def visible(rs: list[dict[str, Any]]) -> str:
    out = []
    for r in rs:
        if r.get("break"):
            out.append("\n")
        elif "mention" in r:
            out.append(f"@{r['mention']['text'].strip()}")
        else:
            out.append(r["text"])
    return " ".join("".join(out).split())


@settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(runs())
def test_inline_roundtrip_preserves_visible_text(rs: list[dict[str, Any]]) -> None:
    md = runs_to_markdown(rs)
    blocks = parse_markdown(md).blocks
    parsed = blocks[0]["content"] if blocks else []
    assert visible(parsed) == visible(canonical(rs)), md


@settings(max_examples=1500, deadline=None)
@given(st.lists(TEXT, min_size=1, max_size=4))
def test_plain_text_roundtrips_exactly(parts: list[str]) -> None:
    text = " ".join(p.strip() for p in parts).strip() or "x"
    text = " ".join(text.split())
    md = runs_to_markdown([{"text": text}])
    assert parse_markdown(md).blocks[0]["content"] == [{"text": text}], md


def char_marks(rs: list[dict[str, Any]]) -> list[tuple[str, frozenset[tuple[str, Any]]]]:
    """Each visible (non-space) character with the formatting it carries."""
    out: list[tuple[str, frozenset[tuple[str, Any]]]] = []
    for r in rs:
        if r.get("break"):
            continue
        marks = frozenset((k, v) for k, v in r.items() if k not in ("text", "mention") and v)
        if "mention" in r:
            out.append((f"@{r['mention']['resourceId']}:{' '.join(r['mention']['text'].split())}", marks))
            continue
        out.extend((ch, marks) for ch in r["text"] if not ch.isspace())
    return out


@settings(max_examples=1500, deadline=None)
@given(runs())
def test_inline_roundtrip_preserves_formatting(rs: list[dict[str, Any]]) -> None:
    md = runs_to_markdown(rs)
    blocks = parse_markdown(md).blocks
    parsed = blocks[0]["content"] if blocks else []
    assert char_marks(parsed) == char_marks(canonical(rs)), md


def _container(kind: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    paragraph = {"kind": "paragraph", "content": runs}
    if kind == "heading":
        return {"kind": "heading", "level": 2, "content": runs}
    if kind == "bullet":
        return {"kind": "bulletList", "children": [{"kind": "listItem", "children": [paragraph]}]}
    if kind == "ordered":
        return {"kind": "orderedList", "children": [{"kind": "listItem", "children": [paragraph]}]}
    if kind == "task":
        return {"kind": "taskList", "children": [{"kind": "taskItem", "checked": False, "content": runs}]}
    if kind == "quote":
        return {"kind": "quote", "children": [paragraph]}
    if kind == "callout":
        return {"kind": "callout", "tone": "info", "children": [paragraph]}
    cell = {"kind": "tableCell", "header": True, "children": [paragraph]}
    return {"kind": "table", "children": [{"kind": "tableRow", "children": [cell]}]}


@settings(max_examples=800, deadline=None)
@given(
    st.lists(TEXT, min_size=1, max_size=4),
    st.sampled_from(["heading", "bullet", "ordered", "task", "quote", "callout", "table"]),
)
def test_plain_text_roundtrips_in_containers(parts: list[str], kind: str) -> None:
    from legendkeeper_mcp.markdown import blocks_to_markdown

    text = " ".join(" ".join(p.split()) for p in parts).strip() or "x"
    text = " ".join(text.split())
    block = _container(kind, [{"text": text}])
    md = blocks_to_markdown([block])
    assert parse_markdown(md).blocks == [block], md
