from __future__ import annotations

import pytest

from legendkeeper_mcp.markdown import find_section, list_headings, materialize, parse_markdown
from legendkeeper_mcp.markdown.merge import drop_keep_sentinels
from legendkeeper_mcp.ranks import rank_after


def heading(text: str, level: int = 2, bid: str | None = None) -> dict:
    block = {"kind": "heading", "level": level, "content": [{"text": text}]}
    if bid:
        block["id"] = bid
    return block


def para(text: str, bid: str | None = None, **extra: object) -> dict:
    block = {"kind": "paragraph", "content": [{"text": text}], **extra}
    if bid:
        block["id"] = bid
    return block


EXISTING = [
    {"id": "box", "kind": "infobox", "rows": []},
    heading("History", bid="h1"),
    para("Old text", bid="p1", align="center"),
    heading("Early Days", level=3, bid="h2"),
    para("Founding", bid="p2"),
    heading("Geography", bid="h3"),
    para("Hills", bid="p3"),
]


def test_reuses_unchanged_blocks_and_resolves_keep_markers():
    parsed = parse_markdown(
        '<!-- lk:keep id="box" kind="infobox" -->\n\n## History\n\nOld text\n\nNew text'
    ).blocks
    result = materialize(parsed, EXISTING)
    assert result.blocks[0] is EXISTING[0]
    assert result.blocks[1] is EXISTING[1]
    assert result.blocks[2] is EXISTING[2]  # keeps align=center although Markdown cannot express it
    assert result.blocks[3] == para("New text")
    assert (result.kept, result.reused, result.created) == (1, 2, 1)


def test_unknown_and_duplicate_keep_markers_warn():
    parsed = parse_markdown(
        '<!-- lk:keep id="zzz" -->\n\n<!-- lk:keep id="box" -->\n\n<!-- lk:keep id="box" -->'
    ).blocks
    result = materialize(parsed, EXISTING)
    assert result.blocks == [EXISTING[0]]
    assert any("does not match" in w for w in result.warnings)
    assert any("referenced twice" in w for w in result.warnings)


def test_duplicate_unchanged_blocks_reuse_each_original_once():
    existing = [para("Same", bid="a"), para("Same", bid="b")]
    result = materialize(parse_markdown("Same\n\nSame\n\nSame").blocks, existing)
    assert [b.get("id") for b in result.blocks] == ["a", "b", None]


def test_lk_block_with_existing_id_is_kept_once():
    parsed = [{"kind": "rule", "id": "r1"}, {"kind": "rule", "id": "r1"}]
    result = materialize(parsed, [])
    assert result.blocks[0]["id"] == "r1" and "id" not in result.blocks[1]
    assert result.warnings


def test_reuse_can_be_disabled():
    result = materialize(parse_markdown("Hills").blocks, EXISTING, reuse_unchanged=False)
    assert result.blocks == [para("Hills")]


def test_drop_keep_sentinels():
    blocks, warnings = drop_keep_sentinels(parse_markdown('<!-- lk:keep id="x" -->\n\nText').blocks)
    assert blocks == [para("Text")] and warnings


def test_sections():
    assert list_headings(EXISTING) == [(1, 2, "History"), (3, 3, "Early Days"), (5, 2, "Geography")]
    section = find_section(EXISTING, "## history ")
    assert section is not None and (section.start, section.end, section.level) == (1, 5, 2)
    nested = find_section(EXISTING, "Early Days")
    assert nested is not None and (nested.start, nested.end) == (3, 5)
    last = find_section(EXISTING, "Geography")
    assert last is not None and last.end == len(EXISTING)
    assert find_section(EXISTING, "Nope") is None


@pytest.mark.parametrize(
    ("last", "expected"),
    [(None, "a0"), ("a0", "a1"), ("a9", "aA"), ("az", "b00"), ("Zz", "a0"), ("a0V", "a1"), ("b0z", "b10")],
)
def test_rank_after_known_values(last, expected):
    assert rank_after(last) == expected


def test_rank_after_is_monotonic_and_handles_odd_keys():
    rank = None
    seen = []
    for _ in range(300):
        rank = rank_after(rank)
        seen.append(rank)
    assert seen == sorted(seen) and len(set(seen)) == len(seen)
    assert rank_after("zzzzzzzzzzzzzzzzzzzzzzzzzzzzz") > "zzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    assert rank_after("~weird") == "~weirdV"
