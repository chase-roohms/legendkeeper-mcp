"""Markdown <-> LegendKeeper block conversion."""

from __future__ import annotations

from typing import Any

import pytest

from legendkeeper_mcp.errors import ConversionError
from legendkeeper_mcp.markdown import (
    InlineContext,
    RenderContext,
    blocks_to_markdown,
    parse_inline,
    parse_markdown,
    runs_to_markdown,
)
from legendkeeper_mcp.markdown.render import is_expressible, keep_summary

NAMES = {"pb000001": "Princess Bubblegum", "finn0001": "Finn"}


def resolve(name: str) -> tuple[str, str] | None:
    for rid, canonical in NAMES.items():
        if canonical.casefold() == name.casefold():
            return rid, canonical
    return None


CTX = InlineContext(resolve_name=resolve, name_of=NAMES.get)
RCTX = RenderContext(name_of=NAMES.get)


def parse(md: str) -> list[dict[str, Any]]:
    return parse_markdown(md, InlineContext(resolve_name=resolve, name_of=NAMES.get)).blocks


def roundtrip(md: str) -> str:
    return blocks_to_markdown(parse(md), RCTX)


def p(*runs: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "paragraph", "content": list(runs)}


@pytest.fixture
def check_blocks(validator):
    def check(blocks: list[dict[str, Any]]) -> None:
        if validator is None:
            pytest.skip("OpenAPI spec unavailable")
        errors = validator.schema_errors("DocumentBlocksWrite", {"blocks": blocks})
        assert not errors, errors

    return check


# ------------------------------------------------------------------------------------------ parse


def test_paragraph_marks():
    blocks = parse("Plain **bold** *it* ***both*** ~~gone~~ `code` <u>u</u> H<sub>2</sub>O x<sup>2</sup>")
    assert blocks == [
        p(
            {"text": "Plain "}, {"text": "bold", "bold": True}, {"text": " "}, {"text": "it", "italic": True},
            {"text": " "}, {"text": "both", "bold": True, "italic": True}, {"text": " "},
            {"text": "gone", "strike": True}, {"text": " "}, {"text": "code", "code": True}, {"text": " "},
            {"text": "u", "underline": True}, {"text": " H"}, {"text": "2", "sub": True}, {"text": "O x"},
            {"text": "2", "sup": True},
        )
    ]  # fmt: skip


def test_links_mentions_and_wiki_links():
    blocks = parse(
        "[site](https://example.com) [Bubblegum](lk://resources/pb000001) [Finn](lk://resources/finn0001/docs/d1) "
        "[[princess bubblegum]] [[Finn|the human]] [[Unknown]]"
    )
    runs = blocks[0]["content"]
    assert runs[0] == {"text": "site", "link": "https://example.com"}
    assert {"mention": {"resourceId": "pb000001", "text": "Bubblegum"}} in runs
    assert {"mention": {"resourceId": "finn0001", "documentId": "d1"}} in runs
    assert {"mention": {"resourceId": "pb000001", "text": "princess bubblegum"}} in runs
    assert {"mention": {"resourceId": "finn0001", "text": "the human"}} in runs
    assert runs[-1] == {"text": " [[Unknown]]"}


def test_unresolved_wiki_link_warning():
    result = parse_markdown("See [[Nobody]].", CTX)
    assert result.warnings == ["Could not resolve wiki link [[Nobody]]; kept it as plain text."]


def test_hard_breaks_and_soft_breaks():
    blocks = parse("line one\nstill one  \nline two\\\nline three")
    assert blocks[0]["content"] == [
        {"text": "line one still one"}, {"break": True}, {"text": "line two"}, {"break": True}, {"text": "line three"}
    ]  # fmt: skip


def test_colored_spans():
    blocks = parse('<span style="color: #FF0000; background-color: #00FF00">x</span> <mark>y</mark>')
    assert blocks[0]["content"][0] == {"text": "x", "color": "#FF0000", "highlight": "#00FF00"}
    assert blocks[0]["content"][2]["highlight"]


def test_headings_rules_code():
    blocks = parse("# One\n\n###### Six\n\n---\n\n```python\nprint(1)\n```\n\n    indented")
    assert blocks[0] == {"kind": "heading", "level": 1, "content": [{"text": "One"}]}
    assert blocks[1]["level"] == 6
    assert blocks[2] == {"kind": "rule"}
    assert blocks[3] == {"kind": "code", "text": "print(1)", "language": "python"}
    assert blocks[4] == {"kind": "code", "text": "indented"}


def test_lists():
    blocks = parse("- a\n- b\n  1. c\n  2. d\n\n3. x\n4. y")
    assert blocks[0]["kind"] == "bulletList"
    item_b = blocks[0]["children"][1]
    assert item_b["children"][0] == p({"text": "b"})
    assert item_b["children"][1]["kind"] == "orderedList"
    assert blocks[1] == {
        "kind": "orderedList",
        "start": 3,
        "children": [
            {"kind": "listItem", "children": [p({"text": "x"})]},
            {"kind": "listItem", "children": [p({"text": "y"})]},
        ],
    }


def test_task_lists_split_from_bullets():
    blocks = parse("- plain\n- [ ] todo\n- [x] **done**")
    assert [b["kind"] for b in blocks] == ["bulletList", "taskList"]
    assert blocks[1]["children"] == [
        {"kind": "taskItem", "checked": False, "content": [{"text": "todo"}]},
        {"kind": "taskItem", "checked": True, "content": [{"text": "done", "bold": True}]},
    ]


def test_callouts_secret_expand():
    blocks = parse(
        "> [!WARNING]\n> Danger\n\n> [!tip] Pro tip\n> Body\n\n> [!secret]\n> GM only\n\n"
        "> [!expand] Spoilers\n> Hidden\n\n> [!weird]\n> x\n\n> plain quote"
    )
    assert blocks[0] == {"kind": "callout", "tone": "warning", "children": [p({"text": "Danger"})]}
    assert blocks[1]["tone"] == "success"
    assert blocks[1]["children"][0] == p({"text": "Pro tip", "bold": True})
    assert blocks[2] == {"kind": "secret", "children": [p({"text": "GM only"})]}
    assert blocks[3] == {"kind": "expand", "title": "Spoilers", "children": [p({"text": "Hidden"})]}
    assert blocks[4]["tone"] == "note"
    assert blocks[5] == {"kind": "quote", "children": [p({"text": "plain quote"})]}


def test_empty_callout_gets_paragraph():
    assert parse("> [!info]")[0]["children"] == [{"kind": "paragraph", "content": []}]


def test_tables():
    blocks = parse("| A | B |\n|---|---|\n| 1<br>2 | **x** |")
    table = blocks[0]
    assert table["kind"] == "table"
    header, row = table["children"]
    assert all(c["header"] for c in header["children"])
    assert row["children"][0]["children"][0]["content"] == [{"text": "1"}, {"break": True}, {"text": "2"}]
    headerless = parse("|  |  |\n|---|---|\n| a | b |")[0]
    assert len(headerless["children"]) == 1
    assert "header" not in headerless["children"][0]["children"][0]


def test_images():
    blocks = parse(
        '![Map of Ooo](https://assets.example/map.png "The Map")\n\nText ![i](https://x/i.png) more'
    )
    assert blocks[0] == {
        "kind": "image", "url": "https://assets.example/map.png", "alt": "Map of Ooo", "title": "The Map",
        "isTitleHidden": False,
    }  # fmt: skip
    assert [b["kind"] for b in blocks[1:]] == ["paragraph", "image", "paragraph"]


def test_lk_block_fences():
    blocks = parse(
        '```lk-block\n[{"kind":"rule"},{"kind":"embed","provider":"youtube","url":"https://youtu.be/x"}]\n```'
    )
    assert blocks == [{"kind": "rule"}, {"kind": "embed", "provider": "youtube", "url": "https://youtu.be/x"}]
    with pytest.raises(ConversionError):
        parse("```lk-block\n{bad json\n```")
    with pytest.raises(ConversionError):
        parse('```lk-block\n{"no": "kind"}\n```')


def test_keep_markers_and_comments():
    md = '<!-- lk:keep id="b1" kind="infobox" -->\n**Infobox**\n- x\n<!-- /lk:keep -->\n\n<!-- a comment -->\n\nText'
    blocks = parse(md)
    assert blocks[0] == {"kind": "__keep__", "id": "b1", "region": "**Infobox**\n- x\n"}
    assert blocks[1] == p({"text": "Text"})
    assert parse('<!-- lk:keep id="b2" kind="embed" -->')[0] == {"kind": "__keep__", "id": "b2"}


def test_raw_html_block_warns():
    result = parse_markdown("<div>hello</div>", CTX)
    assert result.blocks[0]["content"][0]["text"] == "<div>hello</div>"
    assert result.warnings


def test_parse_inline_for_fact_values():
    assert parse_inline("**14** years, friend of [[Finn]]", CTX) == [
        {"text": "14", "bold": True}, {"text": " years, friend of "}, {"mention": {"resourceId": "finn0001"}},
    ]  # fmt: skip
    assert parse_inline("a\n\nb") == [{"text": "a"}, {"break": True}, {"text": "b"}]


def test_parsed_blocks_validate_against_api_schema(check_blocks):
    md = """# Title

Para with **bold**, [link](https://x.y), [[Finn]], `code` and <span style="color: #123456">color</span>.

> [!info]
> Callout with a list:
>
> - one
> - two

> [!secret]
> secret

> [!expand] More
> hidden

- [ ] task

1. first
   - nested

| H1 | H2 |
|---|---|
| a | b |

![alt](https://assets.legendkeeper.com/x.png)

```js
let x = 1
```

---
"""
    check_blocks(parse(md))


# ------------------------------------------------------------------------------------------ render


def test_render_inline_marks_nest_cleanly():
    runs = [
        {"text": "a ", "bold": True}, {"text": "b", "bold": True, "italic": True}, {"text": " c"},
        {"text": "link", "link": "https://x.y/(1)", "bold": True}, {"text": "code`tick", "code": True},
    ]  # fmt: skip
    md = runs_to_markdown(runs)
    assert md == "<strong>a *b*</strong> c[**link**](<https://x.y/(1)>)``code`tick``"
    assert parse(md)[0]["content"] == runs


def test_render_escapes_markdown_syntax():
    tricky = "# not a heading *stars* _under_ snake_case [brackets] <tag> 1. list ~~x~~ &amp; \\back"
    md = runs_to_markdown([{"text": tricky}])
    assert parse(md)[0]["content"] == [{"text": tricky}]
    for start in ("- item", "+ item", "> quote", "1) one", "===", "---", "~~~"):
        assert parse(runs_to_markdown([{"text": start}]))[0]["content"] == [{"text": start}]


def test_render_breaks_in_paragraph_and_table():
    runs = [{"text": "a"}, {"break": True}, {"text": "# b"}]
    md = runs_to_markdown(runs)
    assert md == "a\\\n\\# b"
    assert parse(md)[0]["content"] == runs
    assert runs_to_markdown([{"text": "x|y"}, {"break": True}, {"text": "z"}], in_table=True) == "x\\|y<br>z"


def test_render_mentions_use_names():
    md = runs_to_markdown(
        [{"mention": {"resourceId": "finn0001"}}, {"mention": {"resourceId": "zzz", "name": "Z"}}],
        name_of=NAMES.get,
    )
    assert md == "[Finn](lk://resources/finn0001)[Z](lk://resources/zzz)"


def test_adjacent_lists_do_not_merge():
    blocks = [
        {"kind": "bulletList", "children": [{"kind": "listItem", "children": [p({"text": "a"})]}]},
        {"kind": "taskList", "children": [{"kind": "taskItem", "checked": True, "content": [{"text": "t"}]}]},
    ]
    md = blocks_to_markdown(blocks)
    assert "<!-- -->" in md
    assert parse(md) == blocks


def test_opaque_blocks_keep_and_json():
    infobox = {
        "id": "box1", "kind": "infobox", "layout": "wrapRight", "width": 40,
        "rows": [
            {"id": "p1", "kind": "keyValue", "title": "Facts", "rows": [{"id": "r1", "rank": "a0", "key": "Age", "text": "14", "content": [{"text": "14"}]}]},
            {"id": "p2", "kind": "image", "url": "https://a/x.png", "alt": "Portrait", "title": "Portrait", "isTitleHidden": True},
            {"id": "p3", "kind": "resourceLink", "title": "Friends", "items": [{"id": "i1", "rank": "a0", "resourceId": "finn0001"}]},
        ],
    }  # fmt: skip
    assert not is_expressible(infobox)
    md = blocks_to_markdown([infobox], RCTX)
    assert md.startswith('<!-- lk:keep id="box1" kind="infobox" -->')
    assert (
        "- **Age:** 14" in md
        and "![Portrait](https://a/x.png)" in md
        and "[Finn](lk://resources/finn0001)" in md
    )
    assert keep_summary(infobox, RCTX) in md
    js = blocks_to_markdown([infobox], RenderContext(opaque="json"))
    assert parse(js) == [infobox]


@pytest.mark.parametrize(
    "block",
    [
        {"kind": "embed", "provider": "youtube", "url": "https://youtu.be/x", "id": "e"},
        {
            "kind": "meter",
            "mode": "bar",
            "items": [{"id": "m", "name": "HP", "current": 3, "max": 10}],
            "id": "m1",
        },
        {"kind": "resourceCollection", "filter": {"type": "filter"}, "id": "c1"},
        {"kind": "map", "resourceId": "finn0001", "id": "map1"},
        {
            "kind": "layout",
            "id": "l1",
            "children": [{"kind": "layoutColumn", "children": [p({"text": "col"})]}],
        },
        {"kind": "textField", "title": "Notes", "id": "t1", "children": [p({"text": "note"})]},
        {"kind": "unsupported", "id": "u1"},
        {"kind": "keyValue", "title": "Stats", "id": "k1", "rows": [{"key": "STR", "text": "18"}]},
        {
            "kind": "table",
            "id": "tb",
            "children": [
                {
                    "kind": "tableRow",
                    "children": [{"kind": "tableCell", "colspan": 2, "children": [p({"text": "wide"})]}],
                }
            ],
        },
        {
            "kind": "callout",
            "tone": "info",
            "id": "co",
            "children": [{"kind": "embed", "provider": "spotify", "url": "x"}],
        },
    ],
)
def test_opaque_kinds_render_as_keep_regions(block):
    md = blocks_to_markdown([block], RCTX)
    assert md.startswith(f'<!-- lk:keep id="{block["id"]}"')
    assert parse(md)[0]["kind"] == "__keep__"


def test_new_opaque_block_without_id_renders_as_json():
    block = {"kind": "embed", "provider": "youtube", "url": "https://youtu.be/x"}
    assert blocks_to_markdown([block]).startswith("```lk-block")


EXPRESSIBLE_DOCS = [
    "# Title\n\nSimple paragraph.\n",
    "Text with **bold *nested* bold** and ~~strike~~ and `code`.\n",
    "- one\n- two\n  - nested\n  - nested two\n- three\n",
    "1. one\n2. two\n   1. sub\n",
    "5. five\n6. six\n",
    "- [ ] todo\n- [x] done\n",
    "> quote line\n>\n> second paragraph\n",
    "> [!warning]\n> Careful!\n",
    "> [!secret]\n> GM text\n",
    "> [!expand] Title here\n> Body\n",
    "```python\nprint('x')\n```\n",
    "| A | B |\n|---|---|\n| 1 | 2 |\n",
    "![alt](https://assets.legendkeeper.com/i.png)\n",
    "---\n",
    "Ruled by [Princess Bubblegum](lk://resources/pb000001) and [her](lk://resources/pb000001).\n",
    'Colored <span style="color: #FF0000">red</span> and <u>under</u>.\n',
]


@pytest.mark.parametrize("md", EXPRESSIBLE_DOCS)
def test_markdown_roundtrip_is_stable(md):
    once = roundtrip(md)
    assert roundtrip(once) == once
    assert parse(once) == parse(md)


@pytest.mark.parametrize("md", EXPRESSIBLE_DOCS)
def test_expressible_documents_render_back_identically(md):
    assert roundtrip(md) == md


@pytest.mark.parametrize(
    "block",
    [
        {"kind": "quote", "children": [p({"text": "[!warning] beware"})]},
        {
            "kind": "bulletList",
            "children": [{"kind": "listItem", "children": [p({"text": "[x] done thing"})]}],
        },
        {"kind": "bulletList", "children": [{"kind": "listItem", "children": [p({"text": "[ ] open"})]}]},
        {"kind": "expand", "title": "*Secret* <tag> lore [x]", "children": [p({"text": "body"})]},
        {"kind": "callout", "tone": "note", "children": [p({"text": "[!info] literal"})]},
    ],
)
def test_literal_markers_survive_roundtrip(block):
    assert parse(blocks_to_markdown([block])) == [block]
