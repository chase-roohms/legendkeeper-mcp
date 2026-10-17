"""Inline conversion: LegendKeeper text runs <-> Markdown inline syntax.

A LegendKeeper ``Run`` is one of:

* ``{"text": ..., bold, italic, underline, strike, code, link, sub, sup, color, highlight}``
* ``{"mention": {"resourceId", "documentId"?, "text"?, "name"?}, color?, highlight?}``
* ``{"break": true}``

Markdown mapping:

=================  ==============================================
``**bold**``       bold
``*italic*``       italic
``~~strike~~``     strike
`` `code` ``       code
``<u>x</u>``       underline
``<sub>``/``<sup>``  sub / sup
``<span style="color: #RRGGBB; background-color: #RRGGBB">``  color / highlight
``[text](url)``    link
``[label](lk://resources/<id>)``  mention (``/docs/<docId>`` optional)
``[[Name]]`` / ``[[Name|label]]``  mention resolved by resource name or alias
hard line break    ``{"break": true}``
=================  ==============================================
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

Run = dict[str, Any]

_LK_URI_RE = re.compile(r"^lk://resources/([A-Za-z0-9_-]+)(?:/docs/([A-Za-z0-9_-]+))?/?$")
_WIKI_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
_SPAN_COLOR_RE = re.compile(r"(?<![-\w])color\s*:\s*(#[0-9A-Fa-f]{6})", re.I)
_SPAN_BG_RE = re.compile(r"background(?:-color)?\s*:\s*(#[0-9A-Fa-f]{6})", re.I)
_TAG_RE = re.compile(r"^<\s*(/?)\s*([a-zA-Z]+)([^>]*)>$")
_HTML_MARK_TAGS = {
    "u": "underline",
    "ins": "underline",
    "sub": "sub",
    "sup": "sup",
    "s": "strike",
    "del": "strike",
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
}
DEFAULT_HIGHLIGHT = "#FFF3A3"


# --------------------------------------------------------------------------------------------
# Helpers shared by render and parse
# --------------------------------------------------------------------------------------------


def mention_uri(mention: dict[str, Any]) -> str:
    uri = f"lk://resources/{mention['resourceId']}"
    if mention.get("documentId"):
        uri += f"/docs/{mention['documentId']}"
    return uri


def parse_lk_uri(uri: str) -> tuple[str, str | None] | None:
    m = _LK_URI_RE.match(uri.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def plain_text(runs: Iterable[Run], name_of: Callable[[str], str | None] | None = None) -> str:
    out: list[str] = []
    for run in runs or ():
        if run.get("break"):
            out.append("\n")
        elif "mention" in run:
            out.append(mention_label(run["mention"], name_of))
        else:
            out.append(str(run.get("text", "")))
    return "".join(out)


def mention_label(mention: dict[str, Any], name_of: Callable[[str], str | None] | None = None) -> str:
    label = mention.get("text") or mention.get("name")
    if not label and name_of is not None:
        label = name_of(mention["resourceId"])
    return str(label or mention["resourceId"])


def _marks_of(run: Run) -> dict[str, Any]:
    return {k: v for k, v in run.items() if k not in ("text", "mention", "break") and v not in (None, False)}


def normalize_runs(runs: list[Run]) -> list[Run]:
    """Drop empty text runs and merge neighbours with identical marks (as LegendKeeper does on write)."""
    out: list[Run] = []
    for run in runs:
        if "text" in run:
            if not run["text"]:
                continue
            clean: Run = {"text": run["text"]}
            for key, value in run.items():
                if key != "text" and value not in (None, False):
                    clean[key] = value
            if clean.get("code"):
                clean = {"text": clean["text"], "code": True}
            if clean.get("sub") and clean.get("sup"):
                clean.pop("sub")
            prev = out[-1] if out else None
            if prev is not None and "text" in prev and _marks_of(prev) == _marks_of(clean):
                prev["text"] += clean["text"]
                continue
            out.append(clean)
        else:
            out.append(dict(run))
    return out


# --------------------------------------------------------------------------------------------
# Render: runs -> Markdown
# --------------------------------------------------------------------------------------------

_ESCAPE_ALWAYS = re.compile(r"([\\`*\[\]<])")
_ESCAPE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")
_ESCAPE_TILDE = re.compile(r"~(?=~)")
_ESCAPE_ENTITY = re.compile(r"&(?=#?[A-Za-z0-9]+;)")
_LINE_START = re.compile(
    r"^([ \t]*)("
    r"#{1,6}(?=[ \t]|$)"  # ATX heading
    r"|>"  # block quote
    r"|[-+*](?=[ \t]|$)"  # bullet list item (also "- - -" rules)
    r"|(\d{1,9})([.)])(?=[ \t]|$)"  # ordered list item
    r"|[-=][-= \t]*$"  # setext underline or dashed rule
    r"|\|?[ \t]*:?-+:?[ \t]*\|"  # table delimiter row
    r"|~~~"  # fence
    r")"
)


def escape_text(text: str, *, in_table: bool = False) -> str:
    text = _ESCAPE_ALWAYS.sub(r"\\\1", text)
    text = _ESCAPE_UNDERSCORE.sub(r"\\_", text)
    text = _ESCAPE_TILDE.sub(r"\\~", text)
    text = _ESCAPE_ENTITY.sub(r"\\&", text)
    if in_table:
        text = text.replace("|", r"\|")
    return text


def escape_line_starts(markdown: str) -> str:
    """Escape characters that would turn the start of a paragraph line into block syntax."""

    def fix(line: str) -> str:
        m = _LINE_START.match(line)
        if not m:
            return line
        indent, token = m.group(1), m.group(2)
        # Ordered list markers get their delimiter escaped; other tokens their first character.
        fixed = f"{m.group(3)}\\{m.group(4)}" if m.group(3) else "\\" + token
        return indent + fixed + line[m.end(2) :]

    return "\n".join(fix(line) for line in markdown.split("\n"))


def _code_span(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = (
        text.startswith("`")
        or text.endswith("`")
        or (text.startswith(" ") and text.endswith(" ") and text.strip() != "")
    )
    inner = f" {text} " if pad else text
    return f"{fence}{inner}{fence}"


def _link_dest(url: str) -> str:
    if re.search(r"[\s()<>]", url):
        return "<" + url.replace("<", "%3C").replace(">", "%3E") + ">"
    return url


Mark = tuple[str, ...]
_HTML_TAG = {"underline": "u", "sub": "sub", "sup": "sup"}
_DELIMS = {"bold": "**", "italic": "*", "strike": "~~"}


def _run_marks(run: Run) -> list[Mark]:
    marks: list[Mark] = []
    if run.get("link"):
        marks.append(("link", str(run["link"])))
    for name in ("bold", "italic", "strike", "underline"):
        if run.get(name):
            marks.append((name,))
    if run.get("color") or run.get("highlight"):
        marks.append(("span", str(run.get("color") or ""), str(run.get("highlight") or "")))
    if run.get("sup"):
        marks.append(("sup",))
    elif run.get("sub"):
        marks.append(("sub",))
    return marks


def _opener(mark: Mark, html: bool = False) -> str:
    kind = mark[0]
    if kind == "link":
        return "["
    if kind in _DELIMS:
        return f"<{_HTML_FALLBACK[kind]}>" if html else _DELIMS[kind]
    if kind == "span":
        styles = []
        if mark[1]:
            styles.append(f"color: {mark[1]}")
        if mark[2]:
            styles.append(f"background-color: {mark[2]}")
        return f'<span style="{"; ".join(styles)}">'
    return f"<{_HTML_TAG[kind]}>"


def _closer(mark: Mark, html: bool = False) -> str:
    kind = mark[0]
    if kind == "link":
        return f"]({_link_dest(mark[1])})"
    if kind in _DELIMS:
        return f"</{_HTML_FALLBACK[kind]}>" if html else _DELIMS[kind]
    if kind == "span":
        return "</span>"
    return f"</{_HTML_TAG[kind]}>"


_HTML_FALLBACK = {"bold": "strong", "italic": "em", "strike": "s"}
_ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _category(ch: str | None) -> str:
    """'b' boundary, 'w' whitespace, 'p' punctuation, 'o' other — CommonMark flanking classes."""
    if ch is None:
        return "b"
    if ch.isspace():
        return "w"
    if ch in _ASCII_PUNCT or unicodedata.category(ch)[0] in ("P", "S"):
        return "p"
    return "o"


def _unescaped_bang(text: str) -> bool:
    if not text.endswith("!"):
        return False
    backslashes = len(text[:-1]) - len(text[:-1].rstrip("\\"))
    return backslashes % 2 == 0


@dataclass
class _Tok:
    kind: str  # "text" | "atom" | "open" | "close"
    value: str = ""
    mark: Mark | None = None
    pair: int = -1


@dataclass
class _InlineWriter:
    """Two-phase inline writer.

    Phase 1 turns runs into text/atom/open/close tokens, moving whitespace outside of marks. Phase 2 picks, per
    mark, Markdown delimiters (``**``, ``*``, ``~~``) when CommonMark's flanking rules let them parse at that spot
    and the equivalent HTML tag (``<strong>``, ``<em>``, ``<s>``) otherwise.
    """

    in_table: bool = False
    name_of: Callable[[str], str | None] | None = None
    toks: list[_Tok] = field(default_factory=list)
    stack: list[tuple[Mark, int]] = field(default_factory=list)  # (mark, pair id)
    pairs: int = 0

    def _close_top(self) -> None:
        mark, pair = self.stack.pop()
        # Move trailing whitespace outside the mark.
        trailing = ""
        while (
            self.toks
            and self.toks[-1].kind == "text"
            and self.toks[-1].value.rstrip(" \t") != self.toks[-1].value
        ):
            value = self.toks[-1].value
            stripped = value.rstrip(" \t")
            trailing = value[len(stripped) :] + trailing
            if stripped:
                self.toks[-1].value = stripped
                break
            self.toks.pop()
        if self.toks and self.toks[-1].kind == "open" and self.toks[-1].pair == pair:
            self.toks.pop()  # nothing visible inside: drop the mark
        else:
            self.toks.append(_Tok("close", mark=mark, pair=pair))
        if trailing:
            self.toks.append(_Tok("text", trailing))

    def _set_marks(self, marks: list[Mark], leading: str) -> None:
        current = [m for m, _ in self.stack]
        common = 0
        while common < len(current) and common < len(marks) and current[common] == marks[common]:
            common += 1
        while len(self.stack) > common:
            self._close_top()
        if leading:
            self.toks.append(_Tok("text", leading))
        for mark in marks[common:]:
            self.pairs += 1
            self.toks.append(_Tok("open", mark=mark, pair=self.pairs))
            self.stack.append((mark, self.pairs))

    def add(self, run: Run) -> None:
        if run.get("break"):
            self.toks.append(_Tok("atom", "<br>" if self.in_table else "\\\n"))
            return
        if "mention" in run:
            marks = _run_marks({"color": run.get("color"), "highlight": run.get("highlight")})
            label = escape_text(mention_label(run["mention"], self.name_of), in_table=self.in_table)
            self._set_marks(marks, "")
            self.toks.append(_Tok("atom", f"[{label}]({mention_uri(run['mention'])})"))
            return
        text = str(run.get("text", ""))
        if not text:
            return
        if run.get("code"):
            self._set_marks([], "")
            span = _code_span(text.replace("\n", " "))
            self.toks.append(_Tok("atom", span.replace("|", r"\|") if self.in_table else span))
            return
        marks = _run_marks(run)
        core = text.lstrip(" \t")
        leading = text[: len(text) - len(core)]
        escaped = escape_text(core, in_table=self.in_table)
        escaped = escaped.replace("\n", "<br>" if self.in_table else "\\\n")
        self._set_marks(marks, leading)
        if escaped:
            self.toks.append(_Tok("text", escaped))

    def _edge(self, index: int, step: int) -> str | None:
        """The character next to token ``index`` in direction ``step`` (delimiters count as punctuation)."""
        j = index + step
        if j < 0 or j >= len(self.toks):
            return None
        tok = self.toks[j]
        if tok.kind in ("open", "close"):
            return "*"
        if not tok.value:
            return self._edge(j, step)
        return tok.value[0] if step > 0 else tok.value[-1]

    def _flanks(self, index: int, inner_step: int) -> bool:
        # Conservative: Markdown delimiters only hug "ordinary" characters. Punctuation or other delimiters next to
        # the inside of a mark make the run both-flanking (rule-of-3 territory), so those marks use HTML instead.
        return _category(self._edge(index, inner_step)) == "o"

    def finish(self) -> str:
        while self.stack:
            self._close_top()
        positions: dict[int, list[int]] = {}
        for i, tok in enumerate(self.toks):
            if tok.kind in ("open", "close"):
                positions.setdefault(tok.pair, []).append(i)
        html_pairs = set()
        for pair, (open_i, close_i) in positions.items():
            mark = self.toks[open_i].mark
            if (
                mark is not None
                and mark[0] in _DELIMS
                and not (self._flanks(open_i, +1) and self._flanks(close_i, -1))
            ):
                html_pairs.add(pair)
        # A closing delimiter directly followed by an opening one of the same character (``**a***b*``) is
        # ambiguous; render the opener as HTML.
        for left, right in zip(self.toks, self.toks[1:], strict=False):
            if (
                left.kind == "close"
                and right.kind == "open"
                and left.mark is not None
                and right.mark is not None
                and left.mark[0] in _DELIMS
                and right.mark[0] in _DELIMS
                and left.pair not in html_pairs
                and _DELIMS[left.mark[0]][0] == _DELIMS[right.mark[0]][0]
            ):
                html_pairs.add(right.pair)
        out: list[str] = []
        for tok in self.toks:
            if tok.kind == "open" and tok.mark is not None:
                piece = _opener(tok.mark, tok.pair in html_pairs)
            elif tok.kind == "close" and tok.mark is not None:
                piece = _closer(tok.mark, tok.pair in html_pairs)
            else:
                piece = tok.value
            # "!" right before a link or mention would turn it into image syntax.
            if piece.startswith("[") and out and _unescaped_bang(out[-1]):
                out[-1] = out[-1][:-1] + "\\!"
            out.append(piece)
        return "".join(out)


def runs_to_markdown(
    runs: Iterable[Run],
    *,
    in_table: bool = False,
    name_of: Callable[[str], str | None] | None = None,
) -> str:
    writer = _InlineWriter(in_table=in_table, name_of=name_of)
    normalized = normalize_runs(list(runs or ()))
    # Breaks at the edges of a paragraph cannot be expressed in Markdown (and LegendKeeper trims them too).
    while normalized and (normalized[0].get("break") or str(normalized[0].get("text", "x")).strip() == ""):
        normalized.pop(0)
    while normalized and (normalized[-1].get("break") or str(normalized[-1].get("text", "x")).strip() == ""):
        normalized.pop()
    for run in normalized:
        writer.add(run)
    out = writer.finish()
    return out if in_table else escape_line_starts(out)


# --------------------------------------------------------------------------------------------
# Parse: markdown-it inline nodes -> runs
# --------------------------------------------------------------------------------------------

Resolver = Callable[[str], "tuple[str, str] | None"]


@dataclass
class InlineContext:
    """Callbacks used while parsing inline Markdown."""

    resolve_name: Resolver | None = None
    name_of: Callable[[str], str | None] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _InlineState:
    ctx: InlineContext
    html_marks: dict[str, int] = field(default_factory=dict)
    spans: list[tuple[str | None, str | None]] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)

    def marks(self, base: dict[str, Any]) -> dict[str, Any]:
        marks = dict(base)
        for name, count in self.html_marks.items():
            if count > 0:
                marks[name] = True
        for color, highlight in self.spans:
            if color:
                marks["color"] = color
            if highlight:
                marks["highlight"] = highlight
        return marks


class ImageNode:
    """Placeholder for an inline image (LegendKeeper only has image *blocks*)."""

    def __init__(self, src: str, alt: str, title: str | None) -> None:
        self.src = src
        self.alt = alt
        self.title = title


def node_plain_text(node: Any) -> str:
    if node.type in ("text", "code_inline"):
        return str(node.content)
    if node.type in ("softbreak", "hardbreak"):
        return " "
    return "".join(node_plain_text(child) for child in node.children)


def inline_nodes_to_runs(nodes: Iterable[Any], ctx: InlineContext) -> list[Run | ImageNode]:
    """Convert markdown-it inline syntax-tree nodes into runs (images are returned as ``ImageNode``)."""
    state = _InlineState(ctx=ctx)
    out: list[Run | ImageNode] = []

    def flush() -> None:
        if state.runs:
            out.extend(normalize_runs(state.runs))
            state.runs = []

    def walk(node: Any, marks: dict[str, Any]) -> None:
        t = node.type
        if t == "text":
            _emit_text(state, str(node.content), state.marks(marks))
        elif t == "strong":
            for c in node.children:
                walk(c, {**marks, "bold": True})
        elif t == "em":
            for c in node.children:
                walk(c, {**marks, "italic": True})
        elif t == "s":
            for c in node.children:
                walk(c, {**marks, "strike": True})
        elif t == "code_inline":
            state.runs.append({"text": str(node.content), "code": True})
        elif t == "softbreak":
            _emit_text(state, " ", state.marks(marks))
        elif t == "hardbreak":
            state.runs.append({"break": True})
        elif t == "link":
            href = str(node.attrs.get("href", ""))
            target = parse_lk_uri(href)
            if target is not None:
                label = node_plain_text(node)
                state.runs.append(_mention(target[0], target[1], label, state, ctx))
            else:
                for c in node.children:
                    walk(c, {**marks, "link": href})
        elif t == "image":
            flush()
            out.append(
                ImageNode(
                    str(node.attrs.get("src", "")),
                    node_plain_text(node),
                    str(node.attrs["title"]) if node.attrs.get("title") else None,
                )
            )
        elif t == "html_inline":
            _html_inline(state, str(node.content), marks)
        else:
            for c in node.children:
                walk(c, marks)

    for node in nodes:
        walk(node, {})
    flush()
    return out


def _mention(
    resource_id: str, document_id: str | None, label: str, state: _InlineState, ctx: InlineContext
) -> Run:
    mention: dict[str, Any] = {"resourceId": resource_id}
    if document_id:
        mention["documentId"] = document_id
    current_name = ctx.name_of(resource_id) if ctx.name_of else None
    if label and label != current_name and label != resource_id:
        mention["text"] = label
    run: Run = {"mention": mention}
    styled = state.marks({})
    for key in ("color", "highlight"):
        if styled.get(key):
            run[key] = styled[key]
    return run


def _emit_text(state: _InlineState, text: str, marks: dict[str, Any]) -> None:
    if not text:
        return
    pos = 0
    for m in _WIKI_RE.finditer(text):
        name = m.group(1).strip()
        label = (m.group(2) or "").strip()
        resolved = state.ctx.resolve_name(name) if state.ctx.resolve_name else None
        if resolved is None:
            continue
        if m.start() > pos:
            state.runs.append({"text": text[pos : m.start()], **marks})
        resource_id, canonical = resolved
        mention: dict[str, Any] = {"resourceId": resource_id}
        if label and label != canonical:
            mention["text"] = label
        elif name != canonical:
            mention["text"] = name
        run: Run = {"mention": mention}
        for key in ("color", "highlight"):
            if marks.get(key):
                run[key] = marks[key]
        state.runs.append(run)
        pos = m.end()
    if pos < len(text):
        rest = text[pos:]
        for m in _WIKI_RE.finditer(rest):
            msg = f"Could not resolve wiki link [[{m.group(1).strip()}]]; kept it as plain text."
            if msg not in state.ctx.warnings:
                state.ctx.warnings.append(msg)
        state.runs.append({"text": rest, **marks})


def _html_inline(state: _InlineState, raw: str, marks: dict[str, Any]) -> None:
    m = _TAG_RE.match(raw.strip())
    if not m:
        _emit_text(state, raw, state.marks(marks))
        return
    closing, tag, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
    names = _HTML_MARK_TAGS
    if tag == "br":
        state.runs.append({"break": True})
    elif tag in names:
        key = names[tag]
        state.html_marks[key] = max(0, state.html_marks.get(key, 0) + (-1 if closing else 1))
    elif tag == "span":
        if closing:
            if state.spans:
                state.spans.pop()
        else:
            color = _SPAN_COLOR_RE.search(attrs)
            bg = _SPAN_BG_RE.search(attrs)
            state.spans.append((color.group(1) if color else None, bg.group(1) if bg else None))
    elif tag == "mark":
        if not closing:
            state.spans.append((None, DEFAULT_HIGHLIGHT))
        elif state.spans:
            state.spans.pop()
    else:
        _emit_text(state, raw, state.marks(marks))


def text_runs(text: str) -> list[Run]:
    """Plain text (no Markdown) to runs; newlines become breaks."""
    runs: list[Run] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            runs.append({"break": True})
        if line:
            runs.append({"text": line})
    return runs


__all__ = [
    "ImageNode",
    "InlineContext",
    "Run",
    "escape_text",
    "inline_nodes_to_runs",
    "mention_label",
    "mention_uri",
    "node_plain_text",
    "normalize_runs",
    "parse_lk_uri",
    "plain_text",
    "runs_to_markdown",
    "text_runs",
]
