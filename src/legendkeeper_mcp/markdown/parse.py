"""Parse Markdown into LegendKeeper page blocks.

Supported syntax (see ``guides.MARKDOWN_GUIDE`` for the user-facing description):

* CommonMark paragraphs, ``#`` headings, lists, ``>`` quotes, fenced/indented code, ``---`` rules
* GFM tables, ``~~strike~~`` and ``- [ ]`` / ``- [x]`` task lists
* Callouts ``> [!info|note|success|warning|error|custom]``, secrets ``> [!secret]`` and collapsible
  sections ``> [!expand] Title``
* ``![alt](url)`` images (the URL must be a LegendKeeper asset URL)
* ```` ```lk-block ```` fences holding raw block JSON (an object or an array of objects)
* ``<!-- lk:keep id="…" -->`` markers produced by the reader, which preserve an existing block

``parse_markdown`` returns blocks that may contain ``{"kind": "__keep__"}`` sentinels; resolve them
with :func:`legendkeeper_mcp.markdown.merge.materialize` before sending blocks to the API.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from ..errors import ConversionError
from .inline import ImageNode, InlineContext, Run, inline_nodes_to_runs, node_plain_text, normalize_runs

Block = dict[str, Any]
KEEP = "__keep__"

_MD = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

_KEEP_REGION_RE = re.compile(
    r"^[ \t]*<!--\s*lk:keep\b(?P<attrs>[^>]*?)-->[ \t]*\r?\n(?P<body>.*?)^[ \t]*<!--\s*/lk:keep\s*-->[ \t]*$",
    re.M | re.S,
)
_KEEP_SINGLE_RE = re.compile(r"^<!--\s*lk:keep(?:-ref)?\b(?P<attrs>.*?)-->\s*$", re.S)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_CALLOUT_RE = re.compile(r"^\[!([A-Za-z][\w-]*)\]([+-]?)[ \t]*(.*)$", re.S)
_TASK_RE = re.compile(r"^\[( |x|X)\][ \t]+")

CALLOUT_ALIASES: dict[str, str] = {
    "info": "info", "important": "info", "question": "info", "faq": "info", "help": "info",
    "note": "note", "abstract": "note", "summary": "note", "tldr": "note", "example": "note", "quote": "note",
    "success": "success", "tip": "success", "hint": "success", "check": "success", "done": "success",
    "warning": "warning", "warn": "warning", "attention": "warning",
    "error": "error", "caution": "error", "danger": "error", "failure": "error", "fail": "error",
    "missing": "error", "bug": "error",
    "custom": "custom",
}  # fmt: skip
SECRET_NAMES = {"secret", "gm", "dm", "gm-only", "dm-only"}
EXPAND_NAMES = {"expand", "details", "spoiler", "collapse", "toggle", "fold"}


@dataclass
class ParseResult:
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)


def parse_markdown(markdown: str, ctx: InlineContext | None = None) -> ParseResult:
    ctx = ctx or InlineContext()
    regions: list[str] = []

    def stash(m: re.Match[str]) -> str:
        regions.append(m.group("body"))
        return f'<!-- lk:keep-ref{m.group("attrs").rstrip()} region="{len(regions) - 1}" -->'

    source = _KEEP_REGION_RE.sub(stash, markdown.replace("\r\n", "\n"))
    tree = SyntaxTreeNode(_MD.parse(source))
    parser = _BlockParser(ctx, regions)
    blocks = parser.blocks(tree.children)
    return ParseResult(blocks=blocks, warnings=list(dict.fromkeys(ctx.warnings)))


def parse_inline(markdown: str, ctx: InlineContext | None = None) -> list[Run]:
    """Parse a single line/paragraph of Markdown into runs (used for fact values)."""
    ctx = ctx or InlineContext()
    tree = SyntaxTreeNode(_MD.parse(markdown.strip()))
    runs: list[Run] = []
    for node in tree.children:
        if runs:
            runs.append({"break": True})
        inline = _first_inline(node)
        if inline is None:
            runs.append({"text": node_plain_text(node)})
            continue
        for item in inline_nodes_to_runs(inline.children, ctx):
            if isinstance(item, ImageNode):
                runs.append({"text": item.alt or item.src})
            else:
                runs.append(item)
    return normalize_runs(runs)


def _first_inline(node: SyntaxTreeNode) -> SyntaxTreeNode | None:
    if node.type == "inline":
        return node
    for child in node.children:
        found = _first_inline(child)
        if found is not None:
            return found
    return None


class _BlockParser:
    def __init__(self, ctx: InlineContext, regions: list[str]) -> None:
        self.ctx = ctx
        self.regions = regions

    def warn(self, message: str) -> None:
        if message not in self.ctx.warnings:
            self.ctx.warnings.append(message)

    # -- containers ---------------------------------------------------------------------------

    def blocks(self, nodes: list[SyntaxTreeNode]) -> list[Block]:
        out: list[Block] = []
        for node in nodes:
            out.extend(self.block(node))
        return out

    def block(self, node: SyntaxTreeNode) -> list[Block]:
        t = node.type
        if t == "paragraph":
            return self.paragraph(node)
        if t == "heading":
            level = int(node.tag[1]) if node.tag and node.tag[1:].isdigit() else 1
            return [{"kind": "heading", "level": level, "content": self.runs(node, flatten_images=True)}]
        if t == "blockquote":
            return [self.blockquote(node)]
        if t == "fence":
            return self.fence(node)
        if t == "code_block":
            return [{"kind": "code", "text": str(node.content).rstrip("\n")}]
        if t == "hr":
            return [{"kind": "rule"}]
        if t == "bullet_list":
            return self.bullet_lists(node)
        if t == "ordered_list":
            return [self.ordered_list(node)]
        if t == "table":
            return [self.table(node)]
        if t == "html_block":
            return self.html_block(node)
        self.warn(f"Unsupported Markdown element '{t}' was converted to plain text.")
        text = node_plain_text(node)
        return [{"kind": "paragraph", "content": [{"text": text}]}] if text.strip() else []

    def runs(self, node: SyntaxTreeNode, *, flatten_images: bool = False) -> list[Run]:
        inline = _first_inline(node)
        if inline is None:
            return []
        out: list[Run] = []
        for item in inline_nodes_to_runs(inline.children, self.ctx):
            if isinstance(item, ImageNode):
                if flatten_images:
                    out.append({"text": item.alt or item.src})
                    self.warn("Images are only allowed as their own paragraph; an inline image became text.")
            else:
                out.append(item)
        return normalize_runs(out)

    def paragraph(self, node: SyntaxTreeNode) -> list[Block]:
        inline = _first_inline(node)
        if inline is None:
            return []
        items = inline_nodes_to_runs(inline.children, self.ctx)
        if not any(isinstance(i, ImageNode) for i in items):
            runs = [i for i in items if not isinstance(i, ImageNode)]
            return [{"kind": "paragraph", "content": _trim_breaks(runs)}] if runs else []
        out: list[Block] = []
        pending: list[Run] = []

        def flush() -> None:
            runs = _trim_breaks(normalize_runs(pending))
            if any(r.get("mention") or str(r.get("text", "")).strip() for r in runs):
                out.append({"kind": "paragraph", "content": runs})
            pending.clear()

        for item in items:
            if isinstance(item, ImageNode):
                flush()
                out.append(_image_block(item))
            else:
                pending.append(item)
        flush()
        return out

    def blockquote(self, node: SyntaxTreeNode) -> Block:
        children = list(node.children)
        marker = self._callout_marker(children)
        if marker is None:
            return {"kind": "quote", "children": self._non_empty(self.blocks(children))}
        name, fold, title, first_runs = marker
        body = self.blocks(children[1:])
        if first_runs:
            body.insert(0, {"kind": "paragraph", "content": first_runs})
        lname = name.lower()
        if lname in EXPAND_NAMES or (fold and lname not in SECRET_NAMES):
            return {"kind": "expand", "title": (title or "Details")[:2048], "children": self._non_empty(body)}
        if title:
            body.insert(0, {"kind": "paragraph", "content": [{"text": title, "bold": True}]})
        if lname in SECRET_NAMES:
            return {"kind": "secret", "children": self._non_empty(body)}
        tone = CALLOUT_ALIASES.get(lname)
        if tone is None:
            self.warn(f"Unknown callout type [!{name}]; used 'note'.")
            tone = "note"
        return {"kind": "callout", "tone": tone, "children": self._non_empty(body)}

    def _callout_marker(self, children: list[SyntaxTreeNode]) -> tuple[str, bool, str, list[Run]] | None:
        if not children or children[0].type != "paragraph":
            return None
        inline = _first_inline(children[0])
        if inline is None or not inline.children:
            return None
        nodes = list(inline.children)
        first = nodes[0]
        if first.type != "text" or not str(first.content).startswith("[!"):
            return None
        split = next((i for i, n in enumerate(nodes) if n.type in ("softbreak", "hardbreak")), len(nodes))
        head_text = "".join(node_plain_text(n) for n in nodes[:split])
        m = _CALLOUT_RE.match(head_text)
        if not m:
            return None
        rest = nodes[split + 1 :]
        runs = [i for i in inline_nodes_to_runs(rest, self.ctx) if not isinstance(i, ImageNode)]
        return m.group(1), bool(m.group(2)), m.group(3).strip(), _trim_breaks(normalize_runs(runs))

    @staticmethod
    def _non_empty(blocks: list[Block]) -> list[Block]:
        return blocks or [{"kind": "paragraph", "content": []}]

    def fence(self, node: SyntaxTreeNode) -> list[Block]:
        info = (node.info or "").strip()
        lang = info.split()[0] if info else ""
        text = str(node.content)
        if lang.lower() in {"lk-block", "lk-blocks", "legendkeeper"}:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ConversionError(f"Invalid JSON in an lk-block fence: {exc}") from exc
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
                    raise ConversionError(
                        "An lk-block fence must hold a block object (with 'kind') or an array."
                    )
            return [dict(item) for item in items]
        if text.endswith("\n"):
            text = text[:-1]
        block: Block = {"kind": "code", "text": text}
        if lang:
            block["language"] = lang[:256]
        return [block]

    def bullet_lists(self, node: SyntaxTreeNode) -> list[Block]:
        """A bullet list, split into task lists and bullet lists where the item style changes."""
        items = [c for c in node.children if c.type == "list_item"]
        groups: list[tuple[bool, list[SyntaxTreeNode]]] = []
        for item in items:
            is_task = self._task_prefix(item) is not None
            if groups and groups[-1][0] == is_task:
                groups[-1][1].append(item)
            else:
                groups.append((is_task, [item]))
        return [
            self.task_list(group)
            if is_task
            else {"kind": "bulletList", "children": [self.list_item(i) for i in group]}
            for is_task, group in groups
        ]

    def bullet_list(self, node: SyntaxTreeNode) -> Block:
        items = [c for c in node.children if c.type == "list_item"]
        return {"kind": "bulletList", "children": [self.list_item(i) for i in items]}

    def ordered_list(self, node: SyntaxTreeNode) -> Block:
        block: Block = {
            "kind": "orderedList",
            "children": [self.list_item(c) for c in node.children if c.type == "list_item"],
        }
        start = node.attrs.get("start")
        if start is not None and int(start) != 1:
            block["start"] = max(0, int(start))
        return block

    def _task_prefix(self, item: SyntaxTreeNode) -> bool | None:
        if not item.children or item.children[0].type != "paragraph":
            return None
        inline = _first_inline(item.children[0])
        if inline is None or not inline.children or inline.children[0].type != "text":
            return None
        m = _TASK_RE.match(str(inline.children[0].content) + " ")
        return None if m is None else m.group(1) in ("x", "X")

    def task_list(self, items: list[SyntaxTreeNode]) -> Block:
        out: list[Block] = []
        for item in items:
            checked = bool(self._task_prefix(item))
            runs = self.runs(item.children[0], flatten_images=True)
            if runs and "text" in runs[0]:
                runs[0] = {**runs[0], "text": _strip_task(runs[0]["text"])}
            out.append({"kind": "taskItem", "checked": checked, "content": normalize_runs(runs)})
            for extra in item.children[1:]:
                if extra.type in ("bullet_list", "ordered_list"):
                    self.warn("Nested items under a task were flattened into the task list.")
                    for sub in extra.children:
                        sub_checked = bool(self._task_prefix(sub))
                        sub_runs = self.runs(sub.children[0], flatten_images=True) if sub.children else []
                        if sub_runs and "text" in sub_runs[0]:
                            sub_runs[0] = {**sub_runs[0], "text": _strip_task(sub_runs[0]["text"])}
                        out.append(
                            {"kind": "taskItem", "checked": sub_checked, "content": normalize_runs(sub_runs)}
                        )
                else:
                    text = node_plain_text(extra)
                    if text.strip():
                        out[-1]["content"] = normalize_runs(
                            [*out[-1]["content"], {"break": True}, {"text": text}]
                        )
        return {"kind": "taskList", "children": out}

    def list_item(self, node: SyntaxTreeNode) -> Block:
        first: list[Run] = []
        nested: list[Block] = []
        seen_paragraph = False
        for child in node.children:
            if child.type in ("bullet_list", "ordered_list"):
                nested.append(
                    self.bullet_list(child) if child.type == "bullet_list" else self.ordered_list(child)
                )
                continue
            if child.type == "paragraph":
                runs = self.runs(child, flatten_images=True)
            else:
                runs = [{"text": node_plain_text(child).strip()}]
                self.warn(f"A {child.type.replace('_', ' ')} inside a list item was flattened to text.")
            if seen_paragraph:
                first.extend([{"break": True}, *runs])
            else:
                first.extend(runs)
                seen_paragraph = True
        return {
            "kind": "listItem",
            "children": [{"kind": "paragraph", "content": normalize_runs(first)}, *nested],
        }

    def table(self, node: SyntaxTreeNode) -> Block:
        rows: list[Block] = []
        for section in node.children:
            header = section.type == "thead"
            for tr in section.children:
                cells = []
                for cell in tr.children:
                    runs = self.runs(cell, flatten_images=True)
                    paragraph: Block = {"kind": "paragraph", "content": _trim_breaks(runs)}
                    entry: Block = {"kind": "tableCell", "children": [paragraph]}
                    if header:
                        entry["header"] = True
                    cells.append(entry)
                if header and all(not c["children"][0]["content"] for c in cells):
                    continue  # an empty header row is how headerless tables are written
                rows.append({"kind": "tableRow", "children": cells})
        if not rows:
            empty_cell = {"kind": "tableCell", "children": [{"kind": "paragraph", "content": []}]}
            rows.append({"kind": "tableRow", "children": [empty_cell]})
        return {"kind": "table", "children": rows}

    def html_block(self, node: SyntaxTreeNode) -> list[Block]:
        raw = str(node.content).strip()
        m = _KEEP_SINGLE_RE.match(raw)
        if m:
            attrs = dict(_ATTR_RE.findall(m.group("attrs")))
            if not attrs.get("id"):
                self.warn("Ignored an lk:keep marker without an id.")
                return []
            sentinel: Block = {"kind": KEEP, "id": attrs["id"]}
            if "region" in attrs and attrs["region"].isdigit():
                sentinel["region"] = self.regions[int(attrs["region"])]
            return [sentinel]
        if raw.startswith("<!--") and raw.endswith("-->"):
            return []  # ordinary HTML comments are invisible
        self.warn("Raw HTML blocks are not supported; the HTML was kept as plain text.")
        return [{"kind": "paragraph", "content": [{"text": raw}]}]


def _strip_task(text: str) -> str:
    padded = text + " "
    m = _TASK_RE.match(padded)
    if not m:
        return text
    rest = padded[m.end() :]
    return rest[:-1] if rest.endswith(" ") else rest


def _image_block(node: ImageNode) -> Block:
    block: Block = {"kind": "image", "url": node.src}
    if node.alt:
        block["alt"] = node.alt[:500]
    if node.title:
        block["title"] = node.title[:256]
        block["isTitleHidden"] = False
    return block


def _trim_breaks(runs: list[Run]) -> list[Run]:
    while runs and runs[0].get("break"):
        runs = runs[1:]
    while runs and runs[-1].get("break"):
        runs = runs[:-1]
    if runs and "text" in runs[0]:
        runs[0] = {**runs[0], "text": runs[0]["text"].lstrip()}
    if runs and "text" in runs[-1]:
        runs[-1] = {**runs[-1], "text": runs[-1]["text"].rstrip()}
    return normalize_runs(runs)
