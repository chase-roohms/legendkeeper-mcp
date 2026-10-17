"""Render LegendKeeper page blocks as Markdown.

Blocks that Markdown can express (paragraphs, headings, lists, task lists, quotes, callouts,
secrets, expands, code, rules, simple tables and images) become plain Markdown. Everything else
(infoboxes, key-value blocks, meters, collections, links blocks, embeds, maps, layouts, text fields,
complex tables and ``unsupported`` placeholders) is emitted either as

* ``opaque="keep"`` (default): a readable summary wrapped in
  ``<!-- lk:keep id="…" kind="…" -->`` … ``<!-- /lk:keep -->`` markers. Writing the markers back
  preserves the original block untouched, or
* ``opaque="json"``: a fenced ```` ```lk-block ```` code block holding the block's raw JSON, which
  can be edited and written back.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .inline import mention_label, mention_uri, plain_text, runs_to_markdown

Block = dict[str, Any]
OpaqueMode = Literal["keep", "json"]

CALLOUT_TONES = ("info", "note", "success", "warning", "error", "custom")
_TEXT_KINDS = {"paragraph", "heading", "code", "rule", "image"}
_CONTAINER_KINDS = {"quote", "callout", "secret", "expand"}
_LIST_KINDS = {"bulletList", "orderedList"}


@dataclass(frozen=True)
class RenderContext:
    name_of: Callable[[str], str | None] | None = None
    opaque: OpaqueMode = "keep"


_LIST_SEPARATOR = "<!-- -->"


def _list_family(block: Block) -> str | None:
    kind = block.get("kind")
    if kind in ("bulletList", "taskList"):
        return "bullet"
    if kind == "orderedList":
        return "ordered"
    return None


def join_blocks(blocks: list[Block], rendered: list[str]) -> str:
    """Join rendered blocks, separating adjacent lists that CommonMark would otherwise merge."""
    out: list[str] = []
    previous: str | None = None
    for block, text in zip(blocks, rendered, strict=True):
        if not text:
            continue
        family = _list_family(block)
        if family is not None and family == previous:
            out.append(_LIST_SEPARATOR)
        out.append(text)
        previous = family
    return "\n\n".join(out)


def blocks_to_markdown(blocks: list[Block], ctx: RenderContext | None = None) -> str:
    ctx = ctx or RenderContext()
    blocks = blocks or []
    text = join_blocks(blocks, [render_top_block(b, ctx) for b in blocks]).strip()
    return text + "\n" if text else ""


def render_top_block(block: Block, ctx: RenderContext) -> str:
    if is_expressible(block):
        return render_block(block, ctx)
    if ctx.opaque == "json" or not block.get("id"):
        return render_json_block(block)
    return render_keep_region(block, ctx)


def render_json_block(block: Block) -> str:
    return "```lk-block\n" + json.dumps(block, ensure_ascii=False, separators=(",", ":")) + "\n```"


def render_keep_region(block: Block, ctx: RenderContext) -> str:
    summary = keep_summary(block, ctx)
    head = f'<!-- lk:keep id="{block.get("id", "")}" kind="{block.get("kind", "")}" -->'
    return f"{head}\n{summary}\n<!-- /lk:keep -->" if summary else f"{head}\n<!-- /lk:keep -->"


def keep_summary(block: Block, ctx: RenderContext) -> str:
    """Readable, deterministic stand-in for a block that Markdown cannot express."""
    lines = _summary_lines(block, ctx)
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------------------------
# Expressibility
# --------------------------------------------------------------------------------------------


def is_expressible(block: Block) -> bool:
    kind = block.get("kind")
    if kind in _TEXT_KINDS:
        return True
    if kind in _CONTAINER_KINDS:
        return all(is_expressible(c) for c in block.get("children") or [])
    if kind in _LIST_KINDS:
        return all(_list_item_ok(item) for item in block.get("children") or [])
    if kind == "taskList":
        return all(c.get("kind") == "taskItem" for c in block.get("children") or [])
    if kind == "table":
        return _table_ok(block)
    return False


def _list_item_ok(item: Block) -> bool:
    if item.get("kind") != "listItem":
        return False
    children = item.get("children") or []
    if not children or children[0].get("kind") != "paragraph":
        return False
    return all(c.get("kind") in _LIST_KINDS and is_expressible(c) for c in children[1:])


def _table_ok(table: Block) -> bool:
    rows = table.get("children") or []
    if not rows:
        return False
    width = None
    for r_index, row in enumerate(rows):
        if row.get("kind") != "tableRow":
            return False
        cells = row.get("children") or []
        if width is None:
            width = len(cells)
        if not cells or len(cells) != width:
            return False
        for cell in cells:
            if cell.get("kind") != "tableCell":
                return False
            if (cell.get("colspan") or 1) != 1 or (cell.get("rowspan") or 1) != 1:
                return False
            if bool(cell.get("header")) and r_index != 0:
                return False
            if any(c.get("kind") != "paragraph" for c in cell.get("children") or []):
                return False
    first = rows[0].get("children") or []
    headers = {bool(c.get("header")) for c in first}
    return len(headers) == 1


# --------------------------------------------------------------------------------------------
# Markdown rendering for expressible blocks
# --------------------------------------------------------------------------------------------


def render_block(block: Block, ctx: RenderContext) -> str:
    kind = block.get("kind")
    if kind == "paragraph":
        return runs_to_markdown(block.get("content") or [], name_of=ctx.name_of)
    if kind == "heading":
        level = min(6, max(1, int(block.get("level") or 1)))
        text = runs_to_markdown(block.get("content") or [], name_of=ctx.name_of).replace("\\\n", " ")
        # A trailing run of "#" would be read as the heading's optional closing sequence.
        text = re.sub(r"(^|[ \t])(#+)[ \t]*$", lambda m: m.group(1) + "\\" + m.group(2), text)
        return f"{'#' * level} {text}".rstrip()
    if kind == "code":
        text = str(block.get("text") or "")
        longest = max((len(r) for r in _runs_of(text, "`")), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{block.get('language') or ''}\n{text}\n{fence}"
    if kind == "rule":
        return "---"
    if kind == "image":
        return _render_image(block)
    if kind == "quote":
        return _prefix(_render_children(block, ctx), "> ")
    if kind == "callout":
        tone = block.get("tone") or "info"
        body = _render_children(block, ctx)
        return _prefix(f"[!{tone}]" + (f"\n{body}" if body else ""), "> ")
    if kind == "secret":
        body = _render_children(block, ctx)
        return _prefix("[!secret]" + (f"\n{body}" if body else ""), "> ")
    if kind == "expand":
        title = str(block.get("title") or "").replace("\n", " ")
        body = _render_children(block, ctx)
        return _prefix(f"[!expand] {title}".rstrip() + (f"\n{body}" if body else ""), "> ")
    if kind in _LIST_KINDS:
        return _render_list(block, ctx)
    if kind == "taskList":
        lines = []
        for item in block.get("children") or []:
            mark = "x" if item.get("checked") else " "
            text = runs_to_markdown(item.get("content") or [], name_of=ctx.name_of)
            lines.append(f"- [{mark}] " + text.replace("\n", "\n      "))
        return "\n".join(lines)
    if kind == "table":
        return _render_table(block, ctx)
    raise ValueError(f"Block kind '{kind}' is not expressible in Markdown")


def _runs_of(text: str, char: str) -> list[str]:
    out: list[str] = []
    current = ""
    for ch in text:
        if ch == char:
            current += ch
        elif current:
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def _render_image(block: Block) -> str:
    alt = str(block.get("alt") or "").replace("[", r"\[").replace("]", r"\]")
    url = str(block.get("url") or "")
    dest = f"<{url}>" if any(ch in url for ch in " ()") else url
    title = block.get("title")
    if title and block.get("isTitleHidden") is False:
        safe = str(title).replace('"', r"\"")
        return f'![{alt}]({dest} "{safe}")'
    return f"![{alt}]({dest})"


def _render_children(block: Block, ctx: RenderContext) -> str:
    children = block.get("children") or []
    return join_blocks(children, [render_block(c, ctx) for c in children])


def _prefix(text: str, prefix: str) -> str:
    bare = prefix.rstrip()
    return "\n".join(prefix + line if line else bare for line in text.split("\n"))


def _render_list(block: Block, ctx: RenderContext) -> str:
    ordered = block.get("kind") == "orderedList"
    number = int(block.get("start", 1) if block.get("start") is not None else 1)
    lines: list[str] = []
    for item in block.get("children") or []:
        marker = f"{number}. " if ordered else "- "
        number += 1
        children = item.get("children") or []
        first = runs_to_markdown(children[0].get("content") or [], name_of=ctx.name_of) if children else ""
        indent = " " * len(marker)
        body = first.replace("\n", "\n" + indent)
        lines.append((marker + body).rstrip())
        for child in children[1:]:
            nested = render_block(child, ctx)
            lines.append("\n".join(indent + line if line else "" for line in nested.split("\n")))
    return "\n".join(lines)


def _render_table(block: Block, ctx: RenderContext) -> str:
    rows = block.get("children") or []
    matrix = [[_cell_text(cell, ctx) for cell in row.get("children") or []] for row in rows]
    has_header = bool((rows[0].get("children") or [{}])[0].get("header"))
    width = len(matrix[0])
    header = matrix[0] if has_header else [""] * width
    body = matrix[1:] if has_header else matrix
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * width) + "|"]
    out.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(out)


def _cell_text(cell: Block, ctx: RenderContext) -> str:
    paragraphs = [
        runs_to_markdown(p.get("content") or [], in_table=True, name_of=ctx.name_of)
        for p in cell.get("children") or []
    ]
    return "<br><br>".join(p for p in paragraphs if p) or " "


# --------------------------------------------------------------------------------------------
# Readable summaries for opaque blocks
# --------------------------------------------------------------------------------------------


def _title_line(block: Block, default: str) -> list[str]:
    title = block.get("title") or default
    return [f"**{title}**"] if title and not block.get("isTitleHidden") else []


def _row_lines(rows: list[dict[str, Any]], ctx: RenderContext) -> list[str]:
    lines = []
    for row in rows or []:
        value = (
            runs_to_markdown(row["content"], in_table=True, name_of=ctx.name_of)
            if row.get("content")
            else str(row.get("text") or "")
        )
        lines.append(f"- **{row.get('key', '')}:** {value}".rstrip())
    return lines


def _items_lines(items: list[dict[str, Any]], ctx: RenderContext) -> list[str]:
    lines = []
    for item in items or []:
        mention = {"resourceId": item.get("resourceId", "")}
        if item.get("documentId"):
            mention["documentId"] = item["documentId"]
        lines.append(f"- [{mention_label(mention, ctx.name_of)}]({mention_uri(mention)})")
    return lines


def _summary_lines(block: Block, ctx: RenderContext) -> list[str]:
    kind = block.get("kind")
    if is_expressible(block):
        return [render_block(block, ctx)]
    if kind == "keyValue":
        return _title_line(block, "Stats") + _row_lines(block.get("rows") or [], ctx)
    if kind == "infobox":
        lines = ["**Infobox**"]
        for prop in block.get("rows") or []:
            lines.extend(_property_lines(prop, ctx))
        return lines
    if kind == "meter":
        lines = _title_line(block, "Meter")
        for item in block.get("items") or []:
            value = (
                f"{item.get('current', '')}/{item.get('max', '')}"
                if "max" in item
                else item.get("current", "")
            )
            lines.append(f"- {item.get('name') or 'Value'}: {value}")
        return lines
    if kind == "resourceLink":
        return _title_line(block, "Links") + _items_lines(block.get("items") or [], ctx)
    if kind == "resourceCollection":
        lines = _title_line(block, "Collection")
        if block.get("items"):
            lines += _items_lines(block["items"], ctx)
        if block.get("filter"):
            lines.append(
                "_(collection filtered by: " + json.dumps(block["filter"], ensure_ascii=False) + ")_"
            )
        return lines
    if kind == "embed":
        return [f"[{block.get('provider', 'embed')} embed]({block.get('url', '')})"]
    if kind == "map":
        mention = {"resourceId": block.get("resourceId", "")}
        name = mention_label(mention, ctx.name_of) if block.get("resourceId") else "map"
        return [f"_(embedded map: {name})_"]
    if kind == "textField":
        return _title_line(block, "Text") + _child_summaries(block, ctx)
    if kind in ("layout", "layoutColumn"):
        return _child_summaries(block, ctx)
    if kind in _CONTAINER_KINDS | _LIST_KINDS | {"table", "tableRow", "tableCell", "listItem", "taskList"}:
        return _child_summaries(block, ctx) or [f"_({kind})_"]
    if kind == "unsupported":
        return ["_(content that the LegendKeeper API cannot express)_"]
    if "content" in block:
        return [runs_to_markdown(block.get("content") or [], name_of=ctx.name_of)]
    return [f"_({kind})_"]


def _child_summaries(block: Block, ctx: RenderContext) -> list[str]:
    lines: list[str] = []
    for child in block.get("children") or []:
        if lines:
            lines.append("")
        lines.extend(_summary_lines(child, ctx))
    return lines


def _property_lines(prop: Block, ctx: RenderContext) -> list[str]:
    kind = prop.get("kind")
    title = prop.get("title")
    head = [f"*{title}*"] if title and not prop.get("isTitleHidden") else []
    hidden = " _(hidden)_" if prop.get("isHidden") else ""
    if kind == "keyValue":
        return [h + hidden for h in head] + _row_lines(prop.get("rows") or [], ctx)
    if kind == "image":
        return [*head, _render_image({**prop, "isTitleHidden": True})]
    if kind in ("resourceLink", "resourceCollection"):
        return head + _items_lines(prop.get("items") or [], ctx)
    if kind == "textField":
        return [*head, blocks_to_markdown(prop.get("children") or [], RenderContext(ctx.name_of)).strip()]
    inner = dict(prop)
    inner.pop("title", None)
    return head + _summary_lines(inner, ctx)


def properties_to_markdown(properties: list[Block], ctx: RenderContext | None = None) -> str:
    """Readable Markdown for a resource's sidebar properties."""
    ctx = ctx or RenderContext()
    out: list[str] = []
    for prop in properties or []:
        lines = _property_lines(prop, ctx)
        if out:
            out.append("")
        out.extend(lines or [f"_({prop.get('kind')})_"])
    return "\n".join(out).strip()


def block_plain_text(block: Block, name_of: Callable[[str], str | None] | None = None) -> str:
    if "content" in block:
        return plain_text(block.get("content") or [], name_of)
    if block.get("kind") == "code":
        return str(block.get("text") or "")
    return " ".join(block_plain_text(c, name_of) for c in block.get("children") or [])
